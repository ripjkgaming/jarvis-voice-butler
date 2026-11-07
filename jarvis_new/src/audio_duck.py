"""Duck every non-Jarvis audio stream while Jarvis speaks.

Reference counted, thread safe, never blocks the caller: all pactl work runs
on one worker thread.  Original per-stream volumes live in a ledger file so a
crash while ducked is repaired on the next start.  Never raises.

Env: JARVIS_DUCK=0 disables; JARVIS_DUCK_LEVEL (default 0.18) sets the level;
JARVIS_DUCK_EXEMPT="a,b" adds substrings of app names/binaries never ducked.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

DEFAULT_LEVEL = 0.18
TAIL_S = 0.6
RESCAN_S = 0.5
RAMP_STEPS = 5
RAMP_S = 0.2
TOL = 400  # raw units (65536 = 100%) treated as "unchanged"
_OWN_MARKS = ("jarvis", "piper", "paplay", "livekit", "portaudio", "python")


def _enabled() -> bool:
    return os.environ.get("JARVIS_DUCK", "1").strip().lower() not in (
        "0", "off", "false", "no",
    )


def _level() -> float:
    try:
        return min(1.0, max(0.0, float(os.environ.get("JARVIS_DUCK_LEVEL", DEFAULT_LEVEL))))
    except ValueError:
        return DEFAULT_LEVEL


def _ledger_path() -> Path:
    return Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis")) / "audio_duck_state.json"


def default_runner(args: list[str]) -> str:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=3)
        return p.stdout if p.returncode == 0 else ""
    except Exception:
        return ""


def _text_streams(text: str) -> list[dict]:
    out, cur = [], None
    for line in text.splitlines():
        m = re.match(r"Sink Input #(\d+)", line)
        if m:
            cur = {"index": int(m.group(1)), "mute": False, "volume": {}, "properties": {}}
            out.append(cur)
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("Mute:"):
            cur["mute"] = "yes" in s
        elif s.startswith("Volume:"):
            for i, v in enumerate(re.findall(r"(\d+) /", s)):
                cur["volume"][f"c{i}"] = {"value": int(v)}
        else:
            m = re.match(r'([\w.\-]+) = "(.*)"$', s)
            if m:
                cur["properties"][m.group(1)] = m.group(2)
    return out


def parse_streams(text: str) -> list[dict]:
    """Normalise to [{index, mute, vols, app, binary, role, pid}]."""
    try:
        raw = json.loads(text)
    except Exception:
        raw = _text_streams(text or "")
    out = []
    for s in raw if isinstance(raw, list) else []:
        try:
            props = s.get("properties") or {}
            vols = [int(v["value"]) for v in (s.get("volume") or {}).values()]
            out.append({
                "index": int(s["index"]),
                "mute": bool(s.get("mute")),
                "vols": vols,
                "app": str(props.get("application.name", "")),
                "binary": str(props.get("application.process.binary", "")),
                "role": str(props.get("media.role", "")),
                "pid": str(props.get("application.process.id", "")),
            })
        except Exception:
            continue
    return out


def _ancestors() -> set[str]:
    pids, pid = set(), os.getpid()
    for _ in range(8):
        pids.add(str(pid))
        try:
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except Exception:
            break
        if pid <= 1:
            break
    return pids


def is_own(s: dict) -> bool:
    names = f"{s['app']} {s['binary']}".lower()
    extra = [x.strip().lower() for x in os.environ.get("JARVIS_DUCK_EXEMPT", "").split(",") if x.strip()]
    if any(m in names for m in _OWN_MARKS + tuple(extra)):
        return True
    if s["role"].lower() in ("phone", "communication"):
        return True
    return s["pid"] in _ancestors()


def _near(a: list[int], b: list[int]) -> bool:
    return len(a) == len(b) and all(abs(x - y) <= TOL for x, y in zip(a, b))


class Ducker:
    def __init__(self, runner=None, tail: float = TAIL_S, rescan: float = RESCAN_S,
                 ramp: float = RAMP_S, ledger: Path | None = None, sleep=time.sleep):
        self._run = runner or default_runner
        self.tail, self.rescan, self.ramp = tail, rescan, ramp
        self._ledger = Path(ledger) if ledger else None
        self._sleep = sleep
        self._cv = threading.Condition()
        self._count = 0
        self._release_at = 0.0
        self._next_scan = 0.0
        self._active = False
        self._busy = False
        self._thread: threading.Thread | None = None
        self._streams: dict[int, dict] = {}  # idx -> {orig, set, app, pid}
        self._repaired = False

    # ---- public -------------------------------------------------------
    def duck(self) -> None:
        if not _enabled():
            return
        with contextlib.suppress(Exception):
            with self._cv:
                self._count += 1
                self._ensure_thread()
                self._cv.notify_all()

    def unduck(self, immediate: bool = False) -> None:
        with contextlib.suppress(Exception):
            with self._cv:
                self._count = max(0, self._count - 1)
                if self._count == 0:
                    self._release_at = time.monotonic() + (0 if immediate else self.tail)
                self._cv.notify_all()

    def release_all(self, timeout: float = 3.0) -> None:
        """Shutdown: drop every hold and restore now (blocking, bounded)."""
        with contextlib.suppress(Exception):
            with self._cv:
                self._count = 0
                self._release_at = 0.0
                self._cv.notify_all()
            self.wait_idle(timeout)

    def wait_idle(self, timeout: float = 3.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._cv:
                if not self._busy and not self._active and self._count == 0:
                    return True
            time.sleep(0.01)
        return False

    def repair(self) -> None:
        """Restore streams a crashed run left ducked (from the ledger)."""
        with contextlib.suppress(Exception):
            self._repaired = True
            if not self._ledger or not self._ledger.exists():
                return
            saved = json.loads(self._ledger.read_text())
            cur = {s["index"]: s for s in self._scan()}
            for key, e in saved.items():
                s = cur.get(int(key))
                if s and s["pid"] == e.get("pid") and s["app"] == e.get("app") \
                        and _near(s["vols"], e["set"]):
                    self._set(int(key), e["orig"])
            self._ledger.unlink(missing_ok=True)

    # ---- worker -------------------------------------------------------
    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self._repaired:
            self.repair()
        self._thread = threading.Thread(target=self._loop, name="audio-duck", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while True:
            with self._cv:
                now = time.monotonic()
                act = None
                if self._count > 0:
                    if now >= self._next_scan:
                        act = "duck"
                    else:
                        self._cv.wait(self._next_scan - now)
                elif self._active:
                    if now >= self._release_at:
                        act = "release"
                    else:
                        self._cv.wait(self._release_at - now)
                else:
                    self._cv.wait(30)
                    if self._count == 0 and not self._active:
                        self._thread = None
                        return
                if act:
                    self._busy = True
            if not act:
                continue
            try:
                if act == "duck":
                    self._duck_pass()
                else:
                    self._release_pass()
            except Exception:
                pass
            finally:
                with self._cv:
                    self._busy = False
                    self._next_scan = time.monotonic() + self.rescan

    def _scan(self) -> list[dict]:
        out = self._run(["pactl", "-f", "json", "list", "sink-inputs"])
        if not out.strip():
            return []
        return parse_streams(out)

    def _set(self, idx: int, vols: list[int]) -> None:
        self._run(["pactl", "set-sink-input-volume", str(idx), *[str(v) for v in vols]])

    def _save(self) -> None:
        if not self._ledger:
            return
        with contextlib.suppress(Exception):
            if not self._streams:
                self._ledger.unlink(missing_ok=True)
                return
            self._ledger.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._ledger.with_suffix(".tmp")
            tmp.write_text(json.dumps({str(k): v for k, v in self._streams.items()}))
            tmp.replace(self._ledger)

    def _ramp(self, moves: dict[int, tuple[list[int], list[int]]]) -> None:
        for i in range(1, RAMP_STEPS + 1):
            for idx, (a, b) in moves.items():
                self._set(idx, [round(x + (y - x) * i / RAMP_STEPS) for x, y in zip(a, b)])
            if i < RAMP_STEPS:
                self._sleep(self.ramp / RAMP_STEPS)

    def _duck_pass(self) -> None:
        level = _level()
        live = {s["index"]: s for s in self._scan()}
        moves = {}
        for idx in list(self._streams):
            if idx not in live:
                del self._streams[idx]
        for idx, s in live.items():
            if s["mute"] or not s["vols"] or is_own(s):
                continue
            e = self._streams.get(idx)
            if e is None:
                tgt = [int(v * level) for v in s["vols"]]
                self._streams[idx] = {"orig": s["vols"], "set": tgt, "app": s["app"], "pid": s["pid"]}
                moves[idx] = (s["vols"], tgt)
            elif not _near(s["vols"], e["set"]):
                # Sir changed it while ducked: new value is the new original.
                tgt = [int(v * level) for v in s["vols"]]
                e.update(orig=s["vols"], set=tgt)
                moves[idx] = (s["vols"], tgt)
        if moves:
            self._save()  # ledger first: crash mid-ramp is repairable
            self._ramp(moves)
        self._active = bool(self._streams) or self._count > 0

    def _release_pass(self) -> None:
        live = {s["index"]: s for s in self._scan()}
        moves = {}
        for idx, e in self._streams.items():
            s = live.get(idx)
            if s and _near(s["vols"], e["set"]):
                moves[idx] = (s["vols"], e["orig"])
            # else: gone, or Sir changed it after ducking -> leave alone
        if moves:
            self._ramp(moves)
        self._streams.clear()
        self._save()
        with self._cv:
            if self._count == 0:
                self._active = False


_ducker: Ducker | None = None
_lock = threading.Lock()


def get_ducker() -> Ducker:
    global _ducker
    with _lock:
        if _ducker is None:
            _ducker = Ducker(ledger=_ledger_path())
        return _ducker


def duck() -> None:
    with contextlib.suppress(Exception):
        get_ducker().duck()


def unduck() -> None:
    with contextlib.suppress(Exception):
        get_ducker().unduck()


def repair() -> None:
    """Restore anything a crashed run left ducked (call at agent start)."""
    def _go():
        with contextlib.suppress(Exception):
            get_ducker().repair()
    threading.Thread(target=_go, name="audio-duck-repair", daemon=True).start()


def release_all() -> None:
    with contextlib.suppress(Exception):
        get_ducker().release_all()


@contextlib.contextmanager
def ducking():
    duck()
    try:
        yield
    finally:
        unduck()
