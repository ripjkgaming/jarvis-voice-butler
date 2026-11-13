"""Sleep/wake state machine for Jarvis (JARVIS//SLEEP).

Sleeps after IDLE_SECONDS with no activity. Activity is any of: keyboard or
mouse input (the input-idle helper), a live voice call (voice_active.json),
audio playing (pactl sink-inputs), or a fullscreen window.

Going to sleep writes ~/.jarvis/sleep_state.json as "asleep", marks an away
session, covers the screens (sleep_ctl.sleep_windows) and starts the
screensaver. Waking goes through ~/.jarvis/wake_request so the clap detector
in another process can ask for it: touch_wake_request() only touches the file
(no dbus, no wake). request_wake() touches it and, when the lock is free, wakes
straight away. The run loop polls the file every POLL_S seconds and retries a
failed window restore while awake. Input while asleep never wakes anything.

Every optional collaborator is imported lazily and tolerated when absent:
sleep_ctl.py (desktop/kwin/jarvis-sleep), the screensaver module (default
sleep_screensaver), away_log and wake_sequence.

Wake: a running screensaver gets SIGUSR1 and plays its wake clip (collapse,
flash, fade) itself. The windows come back from the start of the fade, on a
thread, so the clip never holds them. $JARVIS_HOME/no_wake_sequence (a file)
skips wake_sequence, for wake tests that must not start a session.

CLI: python -m src.sleep_controller run|sleep|wake|status
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib
import importlib.util
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger("jarvis.sleep")

#: No activity for this long means sleep. Fixed on purpose.
IDLE_SECONDS = 1200
#: The run loop checks for a wake request this often.
POLL_S = 0.2
#: How often the activity signals are sampled while awake.
TICK_S = 5.0
#: Screensaver module spawned while asleep and for the wake clip. Swap with
#: JARVIS_SCREENSAVER_MODULE (src.sleep_screensaver_opus or src.sleep_screensaver_sonnet).
DEFAULT_SCREENSAVER_MODULE = "src.sleep_screensaver"
#: Seconds into the wake clip when the windows start coming back. Matches the
#: screensaver's fade start (sleep_screensaver.FADE_START_S).
WAKE_WINDOWS_DELAY_S = 0.8
#: While awake with sleep_windows.json still present, a failed window restore is retried this often.
RESTORE_RETRY_S = 5.0
#: Seconds after a successful restore before checking nothing is still minimised.
RESTORE_VERIFY_S = 5.0
#: File under the JARVIS home. While it exists, a wake does not run wake_sequence.
WAKE_SEQUENCE_FLAG = "no_wake_sequence"
#: Signal that tells a running screensaver to play its wake clip and exit.
WAKE_SIGNAL = signal.SIGUSR1

REPO_ROOT = Path(__file__).resolve().parent.parent
SLEEP_CTL_PATH = REPO_ROOT / "desktop" / "kwin" / "jarvis-sleep" / "sleep_ctl.py"

#: Serializes transitions inside this process (also keeps signal handlers
#: from re-entering a transition: they only ever try-acquire).
_thread_lock = threading.Lock()
_screensaver_proc: subprocess.Popen | None = None


# ---------------------------------------------------------------- paths/state


def jarvis_home() -> Path:
    """Base dir honoring $JARVIS_HOME (default ~/.jarvis)."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def _home(home: Path | None) -> Path:
    return jarvis_home() if home is None else Path(home)


def _state_path(home: Path) -> Path:
    return home / "sleep_state.json"


def _wake_request_path(home: Path) -> Path:
    return home / "wake_request"


def _pid_path(home: Path) -> Path:
    return home / "sleep_screensaver.pid"


def _lock_path(home: Path) -> Path:
    return home / "sleep_state.lock"


def _now(now: float | None) -> float:
    return time.time() if now is None else float(now)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_state(home: Path | None = None) -> dict:
    """{"state": "awake"|"asleep", "since": epoch|None}. Missing or bad = awake."""
    awake = {"state": "awake", "since": None}
    try:
        data = json.loads(_state_path(_home(home)).read_text())
    except (OSError, ValueError):
        return awake
    if not isinstance(data, dict) or data.get("state") not in ("awake", "asleep"):
        return awake
    return {"state": data["state"], "since": data.get("since")}


def _write_state(state: str, since: float, home: Path) -> None:
    _atomic_write_text(_state_path(home), json.dumps({"state": state, "since": since}))


def _unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


def _read_pid(home: Path) -> int | None:
    try:
        pid = int(_pid_path(home).read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextlib.contextmanager
def _transition(home: Path, *, blocking: bool = True):
    """In-process lock, then a cross-process flock on sleep_state.lock.

    Yields False only when blocking=False and the in-process lock is busy.
    """
    if not _thread_lock.acquire(blocking=blocking):
        yield False
        return
    try:
        path = _lock_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield True
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    finally:
        _thread_lock.release()


# ------------------------------------------------------------ activity probes


def merge_last_activity(
    now: float,
    *,
    input_idle_s: float | None,
    voice_active: bool,
    audio_playing: bool,
    fullscreen: bool,
) -> float:
    """Timestamp of the latest activity seen right now. Pure.

    input_idle_s None means the input helper is not reporting. That is
    treated as activity, so an unknown state never puts the machine to sleep.
    """
    last = now if input_idle_s is None else now - max(0.0, float(input_idle_s))
    if voice_active or audio_playing or fullscreen:
        last = now
    return last


#: Applications whose audio never means "someone is listening": jarvis-shell's
#: WebKit webaudio streams sit uncorked and silent while the shell is open.
JARVIS_SHELL_APP = "jarvis-shell"


def _parse_sink_inputs(text: str) -> list[dict]:
    """Split `pactl list sink-inputs` into one dict per stream. Pure.

    Each dict has "corked" and "mute" (the top-level fields, lowercased) and
    "props" (the `key = "value"` property lines, quotes stripped).
    """
    streams: list[dict] = []
    current: dict | None = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("Sink Input #"):
            current = {"corked": "", "mute": "", "props": {}}
            streams.append(current)
            continue
        if current is None or not line:
            continue
        key, sep, value = line.partition(" = ")
        if sep and "." in key and " " not in key and ":" not in key:
            current["props"][key] = value.strip().strip('"')
            continue
        key, sep, value = line.partition(":")
        if sep and key in ("Corked", "Mute"):
            current["corked" if key == "Corked" else "mute"] = value.strip().lower()
    return streams


def _is_jarvis_stream(stream: dict, own_pids: frozenset[int] | set[int]) -> bool:
    props = stream["props"]
    if props.get("application.name") == JARVIS_SHELL_APP:
        return True
    if props.get("application.process.binary") == JARVIS_SHELL_APP:
        return True
    # WebKitWebProcess children of jarvis-shell, found by pid (see _jarvis_shell_pids).
    try:
        return int(props.get("application.process.id", "")) in own_pids
    except ValueError:
        return False


def audio_playing_from_pactl(
    text: str, own_pids: frozenset[int] | set[int] = frozenset()
) -> bool:
    """True when some stream is playing: Corked: no, Mute: no, and not jarvis-shell's.

    own_pids are the pids of jarvis-shell and its children. Pure.
    """
    for stream in _parse_sink_inputs(text):
        if stream["corked"] != "no" or stream["mute"] == "yes":
            continue
        if _is_jarvis_stream(stream, own_pids):
            continue
        return True
    return False


def _jarvis_shell_pids() -> frozenset[int]:
    """pids of running jarvis-shell processes and their direct children. Fail-soft: empty."""
    try:
        roots: set[int] = set()
        parent_of: dict[int, int] = {}
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text()
            except OSError:
                continue
            # The comm field sits in parentheses and may hold spaces.
            head, _, rest = stat.rpartition(")")
            comm = head.partition("(")[2]
            fields = rest.split()
            pid = int(entry.name)
            parent_of[pid] = int(fields[1])
            if comm == JARVIS_SHELL_APP:
                roots.add(pid)
        return frozenset(roots | {p for p, ppid in parent_of.items() if ppid in roots})
    except Exception:
        return frozenset()


def fullscreen_from_listing(listing: dict) -> bool:
    """True when the active window in a sleep_ctl.list_windows() result is fullScreen. Pure.

    Anything other than an "ok" listing with an active fullscreen window is False,
    so an unknown answer never keeps the machine awake.
    """
    if not isinstance(listing, dict) or listing.get("status") != "ok":
        return False
    for win in listing.get("windows") or []:
        if isinstance(win, dict) and win.get("active") and win.get("fullScreen"):
            return True
    return False


def _input_idle_s() -> float | None:
    try:
        return _import_optional("input_idle").idle_seconds()
    except Exception:
        return None


def _voice_active() -> bool:
    try:
        return bool(_import_optional("mint_token").active_call_exists())
    except Exception:
        return False


def _audio_playing() -> bool:
    try:
        out = subprocess.run(
            ["pactl", "list", "sink-inputs"],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
    except Exception:
        return False
    return audio_playing_from_pactl(out, _jarvis_shell_pids())


#: Fullscreen is sampled at most this often. Each probe loads and unloads a KWin script.
FULLSCREEN_PROBE_S = 30.0
#: A probe that has not answered by then counts as "not fullscreen".
FULLSCREEN_TIMEOUT_S = 25.0
_fullscreen_seen: tuple[float, bool] | None = None


def _probe_fullscreen() -> bool:
    """One `sleep_ctl.py list` subprocess, bounded in time. Any failure is False."""
    result = _call_sleep_ctl("list_windows", timeout=FULLSCREEN_TIMEOUT_S, loud=False)
    return fullscreen_from_listing(result or {})


def _fullscreen_now(now: float | None = None) -> bool:
    """Is the active window fullscreen, per sleep_ctl.list_windows()? Cached briefly."""
    global _fullscreen_seen
    now = time.monotonic() if now is None else now
    if _fullscreen_seen is not None and now - _fullscreen_seen[0] < FULLSCREEN_PROBE_S:
        return _fullscreen_seen[1]
    value = _probe_fullscreen()
    _fullscreen_seen = (now, value)
    return value


def read_signals() -> dict:
    """The four activity signals, each fail-soft."""
    return {
        "input_idle_s": _input_idle_s(),
        "voice_active": _voice_active(),
        "audio_playing": _audio_playing(),
        "fullscreen": _fullscreen_now(),
    }


# --------------------------------------------------------------------- timer


class IdleTimer:
    """Tracks the last activity time; expires after `threshold` seconds."""

    def __init__(self, threshold: float = IDLE_SECONDS) -> None:
        self.threshold = float(threshold)
        self.last_activity: float | None = None

    def reset(self, now: float) -> None:
        self.last_activity = float(now)

    def floor(self, when: float) -> None:
        """Waking up is activity: the last activity is at least `when`."""
        if self.last_activity is None or when > self.last_activity:
            self.last_activity = float(when)

    def observe(self, now: float, **signals) -> float:
        seen = merge_last_activity(now, **signals)
        if self.last_activity is None or seen > self.last_activity:
            self.last_activity = seen
        return self.last_activity

    def expired(self, now: float) -> bool:
        return (
            self.last_activity is not None
            and now - self.last_activity >= self.threshold
        )


# ------------------------------------------------------- optional collaborators


def _import_optional(name: str):
    """Import a sibling module of src/, with or without src/ on sys.path.

    `python -m src.sleep_controller` only puts the repo root on the path, so the
    bare name is tried after the `src.` one. A missing sibling raises ImportError.
    """
    error: ImportError | None = None
    for candidate in (f"src.{name}", name):
        try:
            return importlib.import_module(candidate)
        except ModuleNotFoundError as missing:
            # "src" or "src.<name>" missing = not on this path; anything else is a
            # real missing dependency inside the module and is raised as such.
            if missing.name not in (candidate, candidate.partition(".")[0]):
                raise
            error = missing
    raise error if error is not None else ImportError(name)


def _call_module(name: str, attr: str, *args):
    """Call `attr` on an optional src/ module. Missing or crashing = logged, None."""
    try:
        return getattr(_import_optional(name), attr)(*args)
    except ImportError:
        log.info("optional %s not installed; skipping %s", name, attr)
        return None
    except Exception:
        log.warning("optional %s.%s failed", name, attr, exc_info=True)
        return None


#: Op name -> sleep_ctl.py sub-command. Every op runs in a fresh subprocess: sleep_ctl
#: registers a D-Bus object and runs a GLib loop, which must never live in this process.
_SLEEP_CTL_CMD = {
    "sleep_windows": "sleep",
    "wake_windows": "wake",
    "list_windows": "list",
}
#: A sleep_ctl subprocess that has not finished by then is killed and counts as a failure.
SLEEP_CTL_TIMEOUT_S = 30.0
#: One sleep_ctl subprocess at a time, so a probe never overlaps a wake.
_sleep_ctl_lock = threading.Lock()


def _call_sleep_ctl(attr: str, timeout: float | None = None, loud: bool = True):
    """Run `sleep_ctl.py <cmd>` in a subprocess; parsed JSON dict, or None on any failure."""
    cmd = _SLEEP_CTL_CMD.get(attr)
    if cmd is None or not SLEEP_CTL_PATH.is_file():
        log.info("sleep_ctl %s unavailable; skipped", attr)
        return None
    emit = log.error if loud else log.info
    argv = ["/usr/bin/python3", str(SLEEP_CTL_PATH), cmd]
    with _sleep_ctl_lock:
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=SLEEP_CTL_TIMEOUT_S if timeout is None else timeout,
                cwd=str(REPO_ROOT),
            )
        except subprocess.TimeoutExpired:
            emit("sleep_ctl %s timed out", cmd)
            return None
        except Exception:
            log.warning("sleep_ctl %s could not run", cmd, exc_info=True)
            return None
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        result = None
    if proc.returncode != 0 or not isinstance(result, dict):
        emit(
            "sleep_ctl %s failed rc=%s: %s",
            cmd,
            proc.returncode,
            (proc.stderr or proc.stdout or "").strip()[-300:],
        )
        return None
    return result


def wake_sequence_enabled(home: Path | None = None) -> bool:
    """False while the no_wake_sequence flag file exists (test mode: wake, no session)."""
    return not (_home(home) / WAKE_SEQUENCE_FLAG).exists()


#: A wake sequence subprocess that outlives this is killed.
WAKE_SEQUENCE_TIMEOUT_S = 12 * 60


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _wake_python() -> str:
    """The repo venv python (has Piper, Whisper, ...); this one when there is no venv."""
    venv = _repo_root() / ".venv" / "bin" / "python"
    return str(venv) if venv.is_file() else sys.executable


def _wake_lock_path(home: Path) -> Path:
    return home / "wake_sequence.lock"


def _run_wake_subprocess(home: Path) -> int | None:
    """Run `python -m src.wake_sequence` under the repo venv; returns its exit code.

    An flock on wake_sequence.lock guards against double runs (None = skipped).
    Output goes to ~/.jarvis/logs/wake_sequence.log.
    """
    home.mkdir(parents=True, exist_ok=True)
    lock = os.open(str(_wake_lock_path(home)), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log.warning("wake sequence already running; not starting another")
            return None
        logs = home / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        with (logs / "wake_sequence.log").open("ab") as logfile:
            try:
                proc = subprocess.run(
                    [_wake_python(), "-m", "src.wake_sequence"],
                    cwd=str(_repo_root()),
                    stdin=subprocess.DEVNULL,
                    stdout=logfile,
                    stderr=subprocess.STDOUT,
                    timeout=WAKE_SEQUENCE_TIMEOUT_S,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                log.warning("wake sequence timed out and was killed")
                return -1
            except OSError:
                log.warning("wake sequence could not start", exc_info=True)
                return -1
        if proc.returncode != 0:
            log.warning(
                "wake sequence exited rc=%s (see wake_sequence.log)", proc.returncode
            )
        return proc.returncode
    finally:
        os.close(lock)


def _start_wake_sequence(home: Path | None = None) -> None:
    """Run the wake sequence in a venv subprocess on a thread, unless flagged off."""
    flag = _home(home) / WAKE_SEQUENCE_FLAG
    if not wake_sequence_enabled(home):
        log.warning("wake sequence skipped: %s exists", flag)
        return
    target = _home(home)
    threading.Thread(
        target=_run_wake_subprocess,
        args=(target,),
        name="jarvis-wake-sequence",
        daemon=True,
    ).start()


def windows_state_path() -> Path | None:
    """sleep_ctl's window state file (sleep_windows.json). None when sleep_ctl is absent."""
    if not SLEEP_CTL_PATH.is_file():
        return None
    override = os.environ.get("JARVIS_SLEEP_STATE")
    return (
        Path(override) if override else Path.home() / ".jarvis" / "sleep_windows.json"
    )


def _read_windows_state() -> dict | None:
    """The saved sleep_windows.json content, or None when absent or unreadable."""
    path = windows_state_path()
    if path is None:
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _still_minimised(saved: dict) -> list[dict]:
    """Recorded windows (not minimised before sleep) that the live list still shows minimised."""
    wanted = {
        w.get("id"): w for w in saved.get("windows", []) if not w.get("minimized")
    }
    listing = _call_sleep_ctl("list_windows", loud=False) or {}
    return [
        wanted[w["id"]]
        for w in listing.get("windows", [])
        if w.get("id") in wanted and w.get("minimized")
    ]


def _restore_windows_now() -> bool:
    """One sleep_ctl.wake_windows() call. True only when the windows came back.

    A failure (no dbus, KWin down, timeout) keeps sleep_windows.json, so the
    controller retries. Non-blocking: a restore already running wins.
    """
    if not _restore_lock.acquire(blocking=False):
        return False
    try:
        result = _call_sleep_ctl("wake_windows")
    finally:
        _restore_lock.release()
    if isinstance(result, dict) and result.get("status") == "ok":
        return True
    log.error(
        "windows NOT restored (sleep_ctl.wake_windows -> %s); state file kept, retry pending",
        result,
    )
    return False


def _verify_restore(saved: dict) -> None:
    """Safety net: RESTORE_VERIFY_S after a wake, restore once more if windows are still minimised.

    A clean wake deletes sleep_windows.json, so the stragglers are written back first.
    """
    time.sleep(RESTORE_VERIFY_S)
    left = _still_minimised(saved)
    if not left:
        return
    log.error(
        "%d window(s) still minimised %.0fs after wake; restoring once more",
        len(left),
        RESTORE_VERIFY_S,
    )
    path = windows_state_path()
    try:
        _atomic_write_text(path, json.dumps({**saved, "windows": left, "active": ""}))
    except OSError:
        log.warning("could not rewrite %s", path, exc_info=True)
        return
    _restore_windows_now()


def _restore_windows_later() -> threading.Thread:
    """sleep_ctl.wake_windows() starts WAKE_WINDOWS_DELAY_S from now, off the wake path.

    Not a daemon: a `wake` run from the CLI must still finish the restore before exit.
    """

    def _run() -> None:
        if WAKE_WINDOWS_DELAY_S > 0:
            time.sleep(WAKE_WINDOWS_DELAY_S)
        saved = _read_windows_state()
        if _restore_windows_now() and saved and RESTORE_VERIFY_S > 0:
            _verify_restore(saved)

    thread = threading.Thread(target=_run, name="jarvis-wake-windows")
    thread.start()
    return thread


#: The restore thread of the latest wake (tests join it).
_last_wake_windows: threading.Thread | None = None
#: One wake_windows() call at a time; a busy lock means another restore is running.
_restore_lock = threading.Lock()


def screensaver_module() -> str:
    """The screensaver module: $JARVIS_SCREENSAVER_MODULE, else the default. Read per spawn."""
    return (
        os.environ.get("JARVIS_SCREENSAVER_MODULE", "").strip()
        or DEFAULT_SCREENSAVER_MODULE
    )


def _spawn_screensaver(args: list[str]) -> subprocess.Popen | None:
    try:
        return subprocess.Popen(
            [sys.executable, "-m", screensaver_module(), *args],
            cwd=str(REPO_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        log.warning("could not start sleep screensaver", exc_info=True)
        return None


def _start_screensaver(home: Path) -> int | None:
    global _screensaver_proc
    proc = _spawn_screensaver([])
    if proc is None:
        return None
    _screensaver_proc = proc
    _atomic_write_text(_pid_path(home), str(proc.pid))
    return proc.pid


def _stop_screensaver(home: Path) -> None:
    global _screensaver_proc
    pid = _read_pid(home)
    if pid:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)
    proc, _screensaver_proc = _screensaver_proc, None
    if proc is not None:
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    _unlink(_pid_path(home))


def _start_wake_anim() -> None:
    """The screensaver sees state awake and plays its flash before closing."""
    _spawn_screensaver(["--wake-anim"])


def _play_wake_clip(home: Path) -> None:
    """Wake clip on the screen that is already covered, with no gap.

    The running screensaver gets SIGUSR1: it plays the collapse, flash and fade in
    the same window, then exits. Killing it first would show the desktop for the
    seconds a new process takes to start. With no live screensaver, one is spawned.
    """
    global _screensaver_proc
    pid = _read_pid(home)
    if _pid_alive(pid):
        try:
            os.kill(pid, WAKE_SIGNAL)
        except (ProcessLookupError, PermissionError):
            pass
        else:
            _unlink(_pid_path(home))
            _screensaver_proc = None  # reaped by Popen's own bookkeeping once it exits
            return
    _start_wake_anim()


# ---------------------------------------------------------------- transitions


def go_to_sleep(home: Path | None = None, now: float | None = None) -> bool:
    """Enter the asleep state. False (and nothing done) when already asleep."""
    home = _home(home)
    with _transition(home):
        if read_state(home)["state"] == "asleep":
            return False
        _unlink(_wake_request_path(home))  # a stale wake must not undo sleep
        _write_state("asleep", _now(now), home)
        _call_module("away_log", "start_away")
        _call_sleep_ctl("sleep_windows")
        _start_screensaver(home)
    log.info("JARVIS asleep")
    return True


def _wake_now(home: Path, now: float | None = None) -> bool:
    """Wake if asleep. Caller holds _transition(). Idempotent.

    Windows start coming back on their own thread at the fade (WAKE_WINDOWS_DELAY_S),
    so the clip is never waited on.
    """
    global _last_wake_windows
    _unlink(_wake_request_path(home))
    if read_state(home)["state"] != "asleep":
        return False
    _write_state("awake", _now(now), home)
    _play_wake_clip(home)
    _last_wake_windows = _restore_windows_later()
    _call_module("away_log", "end_away")
    _start_wake_sequence(home)
    log.info("JARVIS awake")
    return True


def request_wake(home: Path | None = None, now: float | None = None) -> bool:
    """Ask for a wake. Safe from threads, signal handlers and other processes.

    Does nothing when the state is awake. Otherwise touches the wake request
    file. If this process can take the lock
    right now, it wakes immediately; otherwise the run loop picks the file up.
    Returns True only when this call performed the wake.
    """
    home = _home(home)
    if read_state(home)["state"] != "asleep":
        return False
    home.mkdir(parents=True, exist_ok=True)
    _wake_request_path(home).touch()
    with _transition(home, blocking=False) as got:
        if not got:
            return False
        return _wake_now(home, now)


def touch_wake_request(home: Path | None = None) -> bool:
    """Ask the controller to wake. Writes the wake_request file only; never wakes here.

    Safe in any process, including one without dbus (the wake_client clap hook).
    Only while asleep. The controller's poll loop does the wake within POLL_S.
    """
    home = _home(home)
    if read_state(home)["state"] != "asleep":
        return False
    home.mkdir(parents=True, exist_ok=True)
    _wake_request_path(home).touch()
    return True


# ----------------------------------------------------------------- controller


class SleepController:
    """One idle-monitor loop. `clock` and `signals` are injectable for tests."""

    def __init__(
        self,
        home: Path | None = None,
        *,
        clock=time.time,
        signals=None,
        timer: IdleTimer | None = None,
    ) -> None:
        self.home = _home(home)
        self.clock = clock
        self.signals = signals if signals is not None else read_signals
        self.timer = timer if timer is not None else IdleTimer()
        self._next_restore = 0.0

    def poll_wake(self) -> bool:
        """Perform a pending wake request. True if this poll woke JARVIS."""
        if not _wake_request_path(self.home).exists():
            return False
        with _transition(self.home):
            return _wake_now(self.home, now=self.clock())

    def poll_input_wake(self) -> bool:
        """Asleep with the screensaver up: any keyboard/mouse input wakes.

        Edge-detects "recent" (input in last 10 s) False -> True in the
        compositor idle helper's state file, so input that started the sleep
        cannot instantly undo it (2 s guard after sleep begins).
        """
        try:
            idle = json.loads((self.home / "input_idle.json").read_text())
            recent = bool(idle.get("recent"))
            fresh = time.time() - float(idle.get("updated", 0)) < 40
        except (OSError, ValueError, TypeError):
            return False
        prev, self._prev_recent = getattr(self, "_prev_recent", recent), recent
        st = read_state(self.home)
        if st["state"] != "asleep" or not fresh:
            return False
        since = st.get("since")
        if isinstance(since, (int, float)) and time.time() - since < 2.0:
            return False
        if recent and not prev and _pid_alive(_read_pid(self.home)):
            log.info("input while asleep: waking")
            return touch_wake_request(self.home)
        return False

    def retry_restore(self) -> bool:
        """Re-run wake_windows while awake and sleep_windows.json still exists. Throttled.

        Covers a restore that failed on its own thread. True when this call restored them.
        """
        now = self.clock()
        if now < self._next_restore:
            return False
        self._next_restore = now + RESTORE_RETRY_S
        if read_state(self.home)["state"] != "awake":
            return False
        path = windows_state_path()
        if path is None or not path.exists():
            return False
        if _last_wake_windows is not None and _last_wake_windows.is_alive():
            return False
        log.warning("windows still minimised while awake; retrying restore")
        return _restore_windows_now()

    def tick(self) -> str:
        """One activity sample. Returns "awake" or "asleep".

        Asleep: nothing is sampled, so input cannot wake anything. Awake: the
        state file's `since` (the wake time) is a floor for the last activity,
        so a wake from any path restarts the 20-minute count.
        """
        now = self.clock()
        state = read_state(self.home)
        if state["state"] == "asleep":
            return "asleep"
        since = state["since"]
        if isinstance(since, (int, float)) and not isinstance(since, bool):
            self.timer.floor(float(since))
        self.timer.observe(now, **self.signals())
        if self.timer.expired(now):
            go_to_sleep(self.home, now=now)
            return "asleep"
        return "awake"

    def run(
        self,
        stop: threading.Event | None = None,
        *,
        poll_s: float = POLL_S,
        tick_s: float = TICK_S,
    ) -> None:
        stop = stop if stop is not None else threading.Event()
        next_tick = 0.0
        while not stop.is_set():
            try:
                self.poll_input_wake()
                self.poll_wake()
                self.retry_restore()
                now = self.clock()
                if now >= next_tick:
                    self.tick()
                    next_tick = now + tick_s
            except Exception:
                log.exception("sleep loop step failed; continuing")
            stop.wait(poll_s)


def status(home: Path | None = None) -> dict:
    """Read-only snapshot. The fullscreen probe is skipped (it has side effects)."""
    home = _home(home)
    st = read_state(home)
    pid = _read_pid(home)
    return {
        "state": st["state"],
        "since": st["since"],
        "idle_threshold_s": IDLE_SECONDS,
        "wake_requested": _wake_request_path(home).exists(),
        "screensaver_pid": pid,
        "screensaver_running": _pid_alive(pid),
        "signals": {
            "input_idle_s": _input_idle_s(),
            "voice_active": _voice_active(),
            "audio_playing": _audio_playing(),
            "fullscreen": None,
        },
    }


# ------------------------------------------------------------------------ CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sleep_controller", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="idle monitor loop (long running)")
    sub.add_parser("sleep", help="go to sleep now")
    sub.add_parser("wake", help="request a wake now")
    sub.add_parser("status", help="print the state as JSON")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )

    if args.cmd == "run":
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        try:
            _import_optional("input_idle").ensure_helper()
        except Exception:
            log.warning("input idle helper unavailable", exc_info=True)
        SleepController().run(stop)
        return 0
    if args.cmd == "sleep":
        changed = go_to_sleep()
        print(json.dumps({"changed": changed, "state": read_state()["state"]}))
        return 0
    if args.cmd == "wake":
        woke = request_wake()
        print(json.dumps({"woke": woke, "state": read_state()["state"]}))
        return 0
    print(json.dumps(status(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
