"""Goodnight: wish Sir goodnight, then lock the laptop once he is truly away.

"Goodnight" / "I'm going to sleep" -> a fixed farewell, then a detached
watcher (this module run as a script, so it outlives the voice call) that
locks the laptop only when ALL of these hold:

1. no input for 5 minutes (keyboard/mouse idle from src/input_idle.py; if the
   probe is unavailable, 5 minutes since the farewell),
2. Sir has not spoken to Jarvis again (a new call or a new spoken turn
   cancels it via cancel()),
3. no Claude Code / opencode is coding (see coding_active()).

If a coding agent is still working at the 5-minute mark the lock is held and
re-checked every minute (up to MAX_WAIT_S) so the laptop still locks once the
work finishes. JARVIS_GOODNIGHT=0 disables the whole thing.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

IDLE_S = 300.0  # the "5 minutes with no input"
POLL_S = 15.0
RECHECK_S = 60.0  # while a coding agent is still busy
MAX_WAIT_S = 3600.0  # give up (and leave the laptop unlocked) after this
FAREWELL = (
    "Goodnight, Sir. Sleep well.",
    "Goodnight, Sir. I'll keep watch.",
)

_PHRASES = re.compile(
    r"^(?:(?:ok|okay|alright|right|well)[ ,]+)?(?:"
    r"good ?night"
    r"|night night"
    r"|nighty night"
    r"|night"
    r"|i(?:'m| am) (?:going|off|heading|gonna) (?:to )?(?:go )?(?:to )?(?:sleep|bed)"
    r"|(?:going|heading) to (?:sleep|bed)"
    r"|(?:time|off) (?:for|to) (?:bed|sleep)"
    r"|i(?:'m| am) (?:going to )?(?:turn(?:ing)? in|hit(?:ting)? the hay)"
    r")(?: (?:now|for (?:the )?(?:night|today)|then|tonight))?"
    r"(?: (?:jarvis|sir|mate|buddy))?$"
)


def enabled() -> bool:
    return os.environ.get("JARVIS_GOODNIGHT", "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def is_goodnight(text: str) -> bool:
    """Whole utterance is a goodnight / going-to-sleep line. Pure.

    Strict on purpose: "goodnight's sleep tips" or "I can't sleep" are not."""
    t = re.sub(r"[^a-z' ]+", " ", (text or "").lower())
    t = " ".join(t.split())
    t = re.sub(r"^(?:hey |hi )?(?:jarvis )", "", t)
    t = re.sub(r" jarvis$", "", t)
    return bool(t) and len(t.split()) <= 9 and bool(_PHRASES.match(t))


def farewell(now: float | None = None) -> str:
    return FAREWELL[int(time.time() if now is None else now) % len(FAREWELL)]


# --- state + control -------------------------------------------------------


def state_path() -> Path:
    home = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(home) if home else Path.home() / ".jarvis") / "goodnight.json"


def _read() -> dict | None:
    try:
        data = json.loads(state_path().read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def pending() -> bool:
    return _read() is not None


def cancel() -> bool:
    """Drop a pending lock (Sir spoke again). True if one was pending."""
    had = pending()
    with contextlib.suppress(OSError):
        state_path().unlink()
    return had


def arm(now: float | None = None, spawn=subprocess.Popen) -> str:
    """Record the farewell time and start the detached watcher. -> token."""
    token = uuid.uuid4().hex
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"token": token, "armed": time.time() if now is None else now})
    )
    if os.environ.get("PYTEST_CURRENT_TEST") and spawn is subprocess.Popen:
        return token  # tests must never start the real watcher
    with contextlib.suppress(Exception):
        spawn(
            [sys.executable, str(Path(__file__).resolve()), "--watch", token],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    return token


# --- is anything coding? ---------------------------------------------------

_TARGETS = {"claude", "opencode"}
CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
CPU_BUSY = 0.03  # of one core, summed over an agent and everything it spawned
RECENT_FILE_S = 90.0


def _scan(proc: Path) -> dict[int, tuple[int, int, bool]]:
    """{pid: (ppid, cpu_ticks, is_agent)} from /proc. Never raises."""
    out: dict[int, tuple[int, int, bool]] = {}
    for d in proc.iterdir() if proc.is_dir() else ():
        if not d.name.isdigit():
            continue
        try:
            stat = (d / "stat").read_text()
            args = (d / "cmdline").read_bytes().split(b"\0")
            comm = (d / "comm").read_text().strip()
        except OSError:
            continue
        rest = stat[stat.rindex(")") + 2 :].split()
        argv = [a.decode(errors="ignore") for a in args[:3] if a]
        base = {Path(a).name for a in argv[:2]}
        agent = (comm in _TARGETS or bool(base & _TARGETS)) and not any(
            a.startswith("--type=") or "app.asar" in a for a in argv
        )  # skip the Claude desktop app's Electron processes
        out[int(d.name)] = (int(rest[1]), int(rest[11]) + int(rest[12]), agent)
    return out


def _agent_cpu(procs: dict[int, tuple[int, int, bool]]) -> tuple[int, int]:
    """(agent count, total cpu ticks of agents + their descendants)."""
    roots = {p for p, (_, _, a) in procs.items() if a}
    members = set(roots)
    grew = True
    while grew:
        grew = False
        for pid, (ppid, _, _) in procs.items():
            if pid not in members and ppid in members:
                members.add(pid)
                grew = True
    return len(roots), sum(procs[p][1] for p in members)


def _recent_activity(home: Path, now: float) -> bool:
    """A Claude Code transcript or opencode store written in the last 90s."""
    roots = [home / ".claude" / "projects", home / ".local" / "share" / "opencode"]
    for root in roots:
        if not root.is_dir():
            continue
        base = len(root.parts)
        for dirpath, dirs, files in os.walk(root):
            if len(Path(dirpath).parts) - base >= 4:
                dirs[:] = []
            for f in files:
                with contextlib.suppress(OSError):
                    if now - (Path(dirpath) / f).stat().st_mtime < RECENT_FILE_S:
                        return True
    return False


def coding_active(
    proc: Path = Path("/proc"),
    home: Path | None = None,
    sample_s: float = 3.0,
    sleep=time.sleep,
    now: float | None = None,
) -> bool:
    """True when a Claude Code / opencode process is actively working.

    An agent counts as working when it (with its children: tests, builds)
    burns CPU over a short sample, or its session transcript/store was
    written in the last 90s (covers waiting on the model). A merely open,
    idle session is not "coding". No agent process at all -> False."""
    first = _scan(proc)
    count, t0 = _agent_cpu(first)
    if count == 0:
        return False
    sleep(sample_s)
    _, t1 = _agent_cpu(_scan(proc))
    if (t1 - t0) / CLK_TCK >= CPU_BUSY * sample_s:
        return True
    return _recent_activity(home or Path.home(), time.time() if now is None else now)


# --- the watcher -----------------------------------------------------------


def decide(elapsed_s: float, idle_s: float | None, coding: bool | None) -> str:
    """ "wait" | "lock" | "hold" (coding still busy). Pure.

    idle_s None = input probe unavailable: fall back to time since farewell.
    coding None = not checked yet (only checked once the idle gate passes)."""
    quiet = idle_s >= IDLE_S if idle_s is not None else elapsed_s >= IDLE_S
    if not quiet or elapsed_s < IDLE_S:
        return "wait"
    if coding:
        return "hold"
    return "lock"


def _lock() -> bool:
    try:
        return (
            subprocess.run(
                ["loginctl", "lock-session"], timeout=5, capture_output=True
            ).returncode
            == 0
        )
    except Exception:
        return False


def _log(detail: str) -> None:
    with contextlib.suppress(Exception):
        from system import log_action

        log_action("goodnight", detail)


def watch(token: str, now=time.time, sleep=time.sleep) -> str:
    """Block until the lock happens or is cancelled. -> outcome string."""
    import input_idle

    with contextlib.suppress(Exception):
        input_idle.ensure_helper()
    while True:
        st = _read()
        if st is None or st.get("token") != token:
            return "cancelled"
        armed = st.get("armed")
        elapsed = now() - (now() if armed is None else float(armed))
        if elapsed > MAX_WAIT_S:
            cancel()
            _log("gave up: agent still coding after an hour; left unlocked")
            return "gave-up"
        verdict = decide(elapsed, input_idle.idle_seconds(), None)
        if verdict == "lock":
            busy = coding_active()
            verdict = decide(elapsed, input_idle.idle_seconds(), busy)
            if verdict == "hold":
                _log("lock held: Claude Code/opencode still coding")
                sleep(RECHECK_S)
                continue
            st = _read()  # Sir may have spoken during the 3s CPU sample
            if st is None or st.get("token") != token:
                return "cancelled"
            ok = _lock()
            cancel()
            _log(f"locked ok={ok}")
            return "locked" if ok else "lock-failed"
        sleep(POLL_S)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--watch":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        watch(sys.argv[2])
