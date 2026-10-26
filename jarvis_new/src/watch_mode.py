"""Watch mode: guard the laptop while Sir is away.

"Jarvis, I'm leaving" / "Jarvis, watch the laptop" (bridge voice route
"watch_mode") launches this file as its own process. It:

1. watches the webcam (a high-fps lease on the shared camera hub, or the
   camera directly when no hub is running) and records a clip every time
   something moves, with a short pre-roll so the start isn't lost;
2. covers the screen with a fullscreen "WORK ONGOING" card that lights
   up while motion is being seen;
3. blocks card input except STOP_KEYBIND; Jarvis continues replying as a guest.
   An independent watchdog requests the OS lock if the overlay or camera fails. On
   that combo it closes the card, stitches every clip into one video in
   ~/Videos/Jarvis Watch/ and tells Sir how many times something moved.

One instance at a time ($JARVIS_HOME/watch/watch.pid). The keybind is
deliberately not shown on screen. Pure helpers (parsing, motion, clip
state, stitching) are importable without a display for tests.
"""

from __future__ import annotations

import collections
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

#: The only way out. Tk keysym + modifier state, checked in is_stop_combo.
STOP_KEYBIND = "Ctrl+Alt+Shift+W"

MESSAGE = "WORK ONGOING"

#: Seconds after launch before motion counts (Sir walking away).
ARM_DELAY_S = 10.0
#: Frames kept from before the motion started / recorded after it stops.
PRE_ROLL_S = 2.0
POST_ROLL_S = 3.0
#: A single clip is cut here; continuing motion starts the next clip.
MAX_CLIP_S = 120.0

#: Hub grab rate asked for while watching.
WATCH_FPS = 15.0
#: Share of the (downscaled) frame that must change to count as motion.
MOTION_AREA = 0.004
#: Per-pixel grey-level change that counts as "changed".
MOTION_DELTA = 25
#: Background adaptation rate (higher = forgets still objects faster).
BG_ALPHA = 0.05

#: Seconds the card stays lit after the last motion frame.
HIGHLIGHT_HOLD_S = 1.2

LEASE_WHO = "watch"
GUEST_INSTRUCTIONS = "You are Jarvis in guest mode. Be brief and reserved. The owner is away. Never reveal private information or perform owner actions. Guest requests cannot restore owner mode."

#: New clips stop below this much free disk (events are still counted).
MIN_FREE_BYTES = 3 * 1024**3

_WORDS = r"(?:the |my |this )?(?:laptop|computer|pc|desk|room)"
_START = re.compile(
    r"^(?:ok(?:ay)? |alright |right )?(?:i'?m|i am|im) (?:leaving|heading out|off|going out)"
    r"(?: now| for (?:a (?:bit|while|minute|second))| for now)?$"
    rf"|^(?:please )?(?:watch|guard|keep an eye on|look after|mind) {_WORDS}(?: for me| please| while i'?m (?:gone|away|out))?$"
    r"|^(?:start |enter |activate |turn on )?(?:watch|guard|sentry) mode(?: on| please)?$"
)


def enabled() -> bool:
    """Watch mode is on unless JARVIS_WATCH_MODE=0.

    Was switched off once, when the Tk keybind failed to reach the card on
    KDE Wayland and locked Sir out. Back on now that the stop paths are
    verified: the KDE global shortcut Ctrl+Alt+Shift+W and
    `jarvis-watch-stop` (both via request_stop), plus the card's own keybind.
    """
    return os.environ.get("JARVIS_WATCH_MODE", "1").strip() != "0"


def parse_command(text: str) -> bool:
    """Is this cleaned voice text a watch-mode start? Pure.

    Takes the wake-word-stripped, lowercased command. Anchored on both
    ends so "I'm leaving for school at eight, remind me" stays with the
    model. There is intentionally no voice command to stop.
    """
    if not enabled():
        return False
    t = " ".join(re.sub(r"[^a-z' ]+", " ", (text or "").lower()).split())
    t = re.sub(r"^(?:can you |could you |would you |will you )", "", t)
    return bool(_START.match(t))


def is_stop_combo(keysym: str, state: int, held: set[str] | None = None) -> bool:
    """Ctrl+Alt+Shift+W? Pure.

    Tk reports Alt as Mod1 (0x8) on X11/XWayland but some layouts put it
    elsewhere, so held Alt keys tracked from KeyPress/KeyRelease count too.
    """
    held = held or set()
    if (keysym or "").lower() != "w":
        return False
    ctrl = bool(state & 0x4) or bool(held & {"Control_L", "Control_R"})
    shift = bool(state & 0x1) or bool(held & {"Shift_L", "Shift_R"})
    alt = bool(state & 0x8) or bool(held & {"Alt_L", "Alt_R", "Meta_L", "Meta_R"})
    return ctrl and shift and alt


# --- paths / single instance ----------------------------------------------


def jarvis_home() -> Path:
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def state_dir() -> Path:
    return jarvis_home() / "watch"


def pid_path() -> Path:
    return state_dir() / "watch.pid"


def stop_path() -> Path:
    return state_dir() / "stop"


def guest_path() -> Path:
    return state_dir() / "guest"


def enter_guest() -> None:
    """Persist restrictions independently of the camera and overlay process."""
    from goodnight_supervisor import revoke_security

    revoke_security()
    state_dir().mkdir(parents=True, exist_ok=True)
    guest_path().touch(mode=0o600, exist_ok=True)


def guest_required() -> bool:
    # Existence is intentional: malformed/stale state must never unlock Jarvis.
    return guest_path().exists() or is_running()


def restore_owner() -> None:
    """Only local owner controls or the authenticated owner endpoint call this."""
    with contextlib.suppress(FileNotFoundError):
        guest_path().unlink()


def heartbeat_path() -> Path:
    return state_dir() / "overlay-heartbeat"


def fail_safe_lock(run=None) -> bool:
    """Existing OS session lock on total protection failure; no new lock policy."""
    if run is None:
        run = subprocess.run
    try:
        result = run(["loginctl", "lock-session"], timeout=5, capture_output=True)
        return result.returncode == 0
    except Exception:
        return False


def guard_watch(pid: int) -> None:
    """Independent low-cost watchdog, including native/Tk crashes and hangs.

    Never restores owner state. A failed lock is retried with bounded backoff;
    logout/reboot protection still belongs to the OS session, not this overlay.
    """
    started = time.time()
    while guest_path().exists():
        failed = not _pid_alive(pid)
        if time.time() - started > 30.0:
            try:
                failed |= time.time() - heartbeat_path().stat().st_mtime > 12.0
                data = json.loads(status_path().read_text())
                failed |= time.time() - float(data.get("last_frame", 0)) > 20.0
            except (OSError, ValueError, TypeError):
                failed = True
        if failed:
            while guest_path().exists():
                if fail_safe_lock():
                    return
                time.sleep(10.0)
            return
        time.sleep(2.0)


def request_stop() -> bool:
    """Ask a running watch to end and save (the focus-free exit path).

    The card polls this file, so anything can trigger it: the KDE global
    shortcut, `jarvis-watch-stop` from a TTY, a phone button. The Tk
    keybind alone was not enough: under KDE Wayland the XWayland card
    does not reliably get keyboard focus. True when a watch is running.
    Never raises.
    """
    if not is_running():
        if guest_path().exists():
            restore_owner()
            return True
        return False
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        stop_path().write_text(str(time.time()))
        restore_owner()
    except OSError:
        return False
    return True


def status_path() -> Path:
    return state_dir() / "status.json"


def output_dir() -> Path:
    videos = Path.home() / "Videos"
    with contextlib.suppress(Exception):
        out = subprocess.run(
            ["xdg-user-dir", "VIDEOS"], capture_output=True, text=True, timeout=3
        ).stdout.strip()
        if out and out != str(Path.home()):
            videos = Path(out)
    return videos / "Jarvis Watch"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True
    return b"watch_mode" in cmd


def running_pid() -> int | None:
    """PID of a live watch process, else None. Never raises."""
    try:
        pid = int(pid_path().read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _pid_alive(pid) else None


def is_running() -> bool:
    return running_pid() is not None


def launch(popen=None) -> dict:
    """Start watch mode as a detached process. Never raises.

    Wrapped in systemd-inhibit so the laptop neither sleeps nor idles
    into the lock screen while it is watching.
    """
    if popen is None:
        popen = subprocess.Popen
    if not enabled():
        return {
            "ok": False,
            "error": "Watch mode is switched off (JARVIS_WATCH_MODE=0).",
        }
    if is_running():
        return {"ok": True, "state": "already watching"}
    argv = [sys.executable, str(Path(__file__).resolve())]
    if shutil.which("systemd-inhibit"):
        argv = [
            "systemd-inhibit",
            "--what=sleep:idle",
            "--who=Jarvis",
            "--why=Watch mode is recording",
            *argv,
        ]
    try:
        enter_guest()
        with contextlib.suppress(OSError):
            stop_path().unlink()  # a stale request must not end the new watch
        log = open(state_dir() / "watch.log", "ab")  # noqa: SIM115
        popen(
            argv,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
        )
    except OSError as exc:
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": True, "state": "watching"}


# --- motion -----------------------------------------------------------------


class MotionDetector:
    """Running-average background subtraction on a small grey frame."""

    def __init__(
        self,
        area: float = MOTION_AREA,
        delta: int = MOTION_DELTA,
        alpha: float = BG_ALPHA,
        width: int = 320,
    ) -> None:
        self.area = area
        self.delta = delta
        self.alpha = alpha
        self.width = width
        self._bg = None
        self.last_fraction = 0.0
        self.target: tuple[float, float] | None = None

    def update(self, frame) -> bool:
        """Feed one BGR frame; True when it differs enough from the scene."""
        import cv2

        self.target = None
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (self.width, max(1, int(h * self.width / w))))
        grey = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (21, 21), 0)
        if self._bg is None or self._bg.shape != grey.shape:
            self._bg = grey.astype("float32")
            self.last_fraction = 0.0
            return False
        diff = cv2.absdiff(grey, cv2.convertScaleAbs(self._bg))
        _, mask = cv2.threshold(diff, self.delta, 255, cv2.THRESH_BINARY)
        mask = cv2.dilate(mask, None, iterations=2)
        self.last_fraction = float(cv2.countNonZero(mask)) / mask.size
        cv2.accumulateWeighted(grey, self._bg, self.alpha)
        moving = self.last_fraction >= self.area
        if moving:
            moments = cv2.moments(mask, binaryImage=True)
            if moments["m00"]:
                self.target = (
                    max(
                        0.0,
                        min(
                            1.0,
                            moments["m10"] / moments["m00"] / max(1, mask.shape[1] - 1),
                        ),
                    ),
                    max(
                        0.0,
                        min(
                            1.0,
                            moments["m01"] / moments["m00"] / max(1, mask.shape[0] - 1),
                        ),
                    ),
                )
        return moving


# --- clips ------------------------------------------------------------------


def _stamp(frame, t: float):
    """Copy of frame with a date/time burn-in, bottom left."""
    import cv2

    out = frame.copy()
    text = time.strftime("%Y-%m-%d  %H:%M:%S", time.localtime(t))
    h = out.shape[0]
    scale = max(0.5, h / 900)
    org = (int(16 * scale), h - int(18 * scale))
    cv2.putText(
        out, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA
    )
    cv2.putText(
        out, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 1, cv2.LINE_AA
    )
    return out


def _cv2_writer(path: Path, fps: float, size: tuple[int, int]):
    import cv2

    return cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)


@dataclass
class ClipRecorder:
    """Motion-triggered clip state machine: pre-roll, record, post-roll.

    `push(frame, moving, t)` returns "start", "stop" or None. Writers come
    from `make_writer(path, fps, (w, h))` (injectable for tests); each
    finished clip path is appended to `clips`.
    """

    folder: Path
    make_writer: object = _cv2_writer
    stamp: object = _stamp
    pre_roll_s: float = PRE_ROLL_S
    post_roll_s: float = POST_ROLL_S
    max_clip_s: float = MAX_CLIP_S
    clips: list[Path] = field(default_factory=list)
    events: int = 0
    #: Asked before each new clip; False = count the event, record nothing.
    can_record: object = None
    skipped: int = 0

    def __post_init__(self) -> None:
        self._buf: collections.deque = collections.deque()
        self._writer = None
        self._path: Path | None = None
        self._started = 0.0
        self._last_motion = 0.0
        self._fps_ema: float | None = None
        self._last_t: float | None = None
        self._continue_until = 0.0

    @property
    def recording(self) -> bool:
        return self._writer is not None

    def fps(self) -> float:
        if not self._fps_ema:
            return WATCH_FPS
        return max(2.0, min(30.0, 1.0 / self._fps_ema))

    def _open(self, t: float, size: tuple[int, int]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self._path = self.folder / f"clip_{len(self.clips):04d}.mp4"
        self._writer = self.make_writer(self._path, round(self.fps(), 2), size)
        self._started = t

    def _close(self) -> None:
        with contextlib.suppress(Exception):
            self._writer.release()
        if self._path is not None:
            self.clips.append(self._path)
        self._writer, self._path = None, None

    def push(self, frame, moving: bool, t: float) -> str | None:
        if self._last_t is not None and t > self._last_t:
            gap = min(1.0, t - self._last_t)
            self._fps_ema = (
                gap if self._fps_ema is None else 0.9 * self._fps_ema + 0.1 * gap
            )
        self._last_t = t
        stamped = self.stamp(frame, t)
        event = None
        if moving:
            self._last_motion = t
        if self._writer is None:
            self._buf.append((t, stamped))
            while self._buf and t - self._buf[0][0] > self.pre_roll_s:
                self._buf.popleft()
            if moving:
                new_event = t > self._continue_until
                if self.can_record is not None and not self.can_record():
                    # Disk nearly full: count it once, record nothing.
                    if new_event:
                        self.events += 1
                        self.skipped += 1
                    self._continue_until = t + self.post_roll_s
                    return None
                h, w = frame.shape[:2]
                self._open(t, (w, h))
                if new_event:
                    self.events += 1
                event = "start"
                for _, old in self._buf:
                    self._writer.write(old)
                self._buf.clear()
            return event
        self._writer.write(stamped)
        if t - self._last_motion > self.post_roll_s:
            self._close()
            return "stop"
        if t - self._started > self.max_clip_s:
            # Long motion: cut here; a clip opened right after is the same
            # event, so the count stays honest.
            self._close()
            self._continue_until = t + self.post_roll_s
            return "stop"
        return None

    def finish(self) -> list[Path]:
        if self._writer is not None:
            self._close()
        return list(self.clips)


def disk_ok(path: Path, min_free: int = MIN_FREE_BYTES) -> bool:
    """Room for more footage under `path`? True when unknown. Never raises."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(path).free >= min_free
    except Exception:
        return True


def stitch(clips: list[Path], out: Path, run=subprocess.run) -> bool:
    """Concatenate clips into one H.264 mp4 with ffmpeg. Never raises."""
    clips = [c for c in clips if c.exists() and c.stat().st_size > 0]
    if not clips:
        return False
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        listing = out.with_suffix(".txt")
        listing.write_text(
            "".join("file '{}'\n".format(str(c).replace("'", "'\\''")) for c in clips)
        )
        proc = run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-an",
                str(out),
            ],
            capture_output=True,
            timeout=1800,
        )
        with contextlib.suppress(OSError):
            listing.unlink()
        return proc.returncode == 0 and out.exists() and out.stat().st_size > 0
    except Exception:
        return False


# --- frames -----------------------------------------------------------------


class FrameSource:
    """Hub frames under a high-fps lease; direct camera if no hub answers."""

    HUB_WAIT_S = 6.0

    def __init__(self) -> None:
        self._cap = None
        self._last_ts = 0.0
        self._lease_at = 0.0
        self._started = time.time()
        self.mode = "hub"

    def _hub(self):
        import camera_hub

        now = time.time()
        if now - self._lease_at > 3.0:
            camera_hub.request(LEASE_WHO, 10.0, fps=WATCH_FPS)
            self._lease_at = now
        meta = camera_hub.latest_meta()
        try:
            ts = float(meta.get("ts", 0.0))
        except (TypeError, ValueError):
            ts = 0.0
        if ts <= self._last_ts or now - ts > 1.5:
            return None
        frame = camera_hub.latest_frame(max_age_s=1.5)
        if frame is None:
            return None
        self._last_ts = ts
        return ts, frame

    def read(self):
        """(timestamp, BGR frame) or None when nothing new yet."""
        if self.mode == "hub":
            got = self._hub()
            if got is not None:
                return got
            if self._last_ts == 0.0 and time.time() - self._started > self.HUB_WAIT_S:
                self.release()
                self.mode = "direct"
            return None
        if self._cap is None:
            import camera_hub

            self._cap = camera_hub._open_capture()
            if self._cap is None:
                return None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            with contextlib.suppress(Exception):
                self._cap.release()
            self._cap = None
            return None
        return time.time(), frame

    def release(self) -> None:
        with contextlib.suppress(Exception):
            import camera_hub

            camera_hub.release(LEASE_WHO)
        if self._cap is not None:
            with contextlib.suppress(Exception):
                self._cap.release()
            self._cap = None


# --- watcher ----------------------------------------------------------------


class Watcher:
    """Capture thread: frames -> motion -> clips. UI reads the fields."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.started = time.time()
        self.armed_at = self.started + ARM_DELAY_S
        self.recorder = ClipRecorder(folder, can_record=self._disk_ok)
        self._disk = (0.0, True)
        self.detector = MotionDetector()
        self.last_motion = 0.0
        self.camera_ok = False
        self.motion_target = None
        self.last_frame = 0.0
        self._status_at = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="watch-capture", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def _disk_ok(self) -> bool:
        """disk_ok(), re-checked at most every 30 s."""
        at, ok = self._disk
        if time.time() - at > 30.0:
            ok = disk_ok(self.folder)
            self._disk = (time.time(), ok)
        return ok

    def armed(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.armed_at

    def _run(self) -> None:
        import cv2

        cv2.setNumThreads(1)
        source = FrameSource()
        gap = 1.0 / WATCH_FPS
        try:
            while not self._stop.is_set():
                got = None
                with contextlib.suppress(Exception):
                    got = source.read()
                if got is None:
                    if self.last_frame and time.time() - self.last_frame > 3.0:
                        self.camera_ok = False
                        self.motion_target = None
                    self._stop.wait(gap / 2 if source.mode == "hub" else 0.5)
                    continue
                if self._stop.is_set():
                    break
                t, frame = got
                self.camera_ok = True
                self.last_frame = t
                try:
                    moving = self.detector.update(frame)
                except Exception:
                    moving = False
                moving = moving and self.armed(t)
                if moving:
                    self.last_motion = t
                    self.motion_target = self.detector.target
                with contextlib.suppress(Exception):
                    self.recorder.push(frame, moving, t)
                self._write_status()
                self._stop.wait(gap)
        finally:
            source.release()

    def _write_status(self) -> None:
        now = time.time()
        if now - self._status_at < 1.0:
            return
        self._status_at = now
        with contextlib.suppress(Exception):
            tmp = status_path().with_suffix(".tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "since": self.started,
                        "events": self.recorder.events,
                        "recording": self.recorder.recording,
                        "last_motion": self.last_motion,
                        "camera_ok": self.camera_ok,
                        "last_frame": self.last_frame,
                        "target": self.motion_target,
                    }
                )
            )
            os.replace(tmp, status_path())

    def stop(self) -> list[Path]:
        self._stop.set()
        self._thread.join(timeout=5.0)
        return self.recorder.finish()


def save_session(watcher: Watcher) -> dict:
    """Stop recording, stitch into one video, clean up. Never raises."""
    clips = watcher.stop()
    events = watcher.recorder.events
    result: dict = {"events": events, "video": None}
    if clips:
        stamp = time.strftime("%Y-%m-%d_%H-%M", time.localtime(watcher.started))
        out = output_dir() / f"watch-{stamp}.mp4"
        if stitch(clips, out):
            result["video"] = str(out)
            shutil.rmtree(watcher.folder, ignore_errors=True)
        else:
            result["video"] = str(watcher.folder)
    else:
        shutil.rmtree(watcher.folder, ignore_errors=True)
    result["skipped"] = watcher.recorder.skipped
    return result


def welcome_line(result: dict) -> str:
    """What Jarvis says when Sir is back. Pure."""
    events = int(result.get("events") or 0)
    if events == 0:
        return "Welcome back, Sir. Nothing moved while you were away."
    times = "once" if events == 1 else f"{events} times"
    line = f"Welcome back, Sir. Something moved {times}"
    if not result.get("video"):
        return line + ", but the disk was too full to record it."
    if result.get("skipped"):
        return line + "; the disk filled up, so the later footage is missing."
    return line + "; the footage is in Videos, Jarvis Watch."


def announce(result: dict, wait_s: float = 8.0) -> None:
    """Speak the welcome line and log the session. Never raises."""
    events = int(result.get("events") or 0)
    with contextlib.suppress(Exception):
        import speak

        speak.speak(
            welcome_line(result), source="watch", min_gap_s=0.0, title="Watch mode"
        )
        time.sleep(wait_s)  # speak runs on a daemon thread; let it finish
    with contextlib.suppress(Exception):
        from system import log_action

        log_action("watch", f"{events} events, {result.get('video') or 'no video'}")


def finish_session(watcher: Watcher) -> dict:
    """save_session + announce (signal / crash path). Never raises."""
    result = save_session(watcher)
    announce(result)
    return result


# --- screen -----------------------------------------------------------------

BG = "#050608"
DIM_TEXT = "#2c323a"
IDLE_TEXT = "#6d9fc4"
ALERT_TEXT = "#b8ddff"
ALERT = "#55b6ff"
ALERT_BAND = "#081b32"
META_TEXT = "#4a525c"
LIVE = "#459cde"


def _mix(a: str, b: str, k: float) -> str:
    """Blend two #rrggbb colours, k=0 -> a, k=1 -> b. Pure."""
    k = max(0.0, min(1.0, k))
    pa = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * k):02x}" for x, y in zip(pa, pb))


def _spaced(text: str) -> str:
    return "   ".join(" ".join(word) for word in text.split())


def _font(root, families: tuple[str, ...], fallback: str = "TkDefaultFont") -> str:
    import tkinter.font as tkfont

    have = set(tkfont.families(root))
    for fam in families:
        if fam in have:
            return fam
    return fallback


_MONITOR = re.compile(r"^\s*\d+:\s+\S+\s+(\d+)/\d+x(\d+)/\d+([+-]\d+)([+-]\d+)")


def parse_monitors(text: str) -> list[tuple[int, int, int, int]]:
    """(x, y, w, h) per monitor from `xrandr --listmonitors`. Pure."""
    out = []
    for line in (text or "").splitlines():
        m = _MONITOR.match(line)
        if m:
            w, h, x, y = (int(v) for v in m.groups())
            out.append((x, y, w, h))
    return out


def monitors(run=subprocess.run) -> list[tuple[int, int, int, int]]:
    """Native KDE logical output layout, then X11/XWayland fallback."""
    with contextlib.suppress(Exception):
        proc = run(["kscreen-doctor", "-j"], capture_output=True, text=True, timeout=2)
        if proc.returncode == 0:
            out = []
            for output in json.loads(proc.stdout).get("outputs", []):
                if not output.get("enabled"):
                    continue
                pos, size = output["pos"], output["size"]
                geom = (
                    int(pos["x"]),
                    int(pos["y"]),
                    int(size["width"]),
                    int(size["height"]),
                )
                if geom[2] > 0 and geom[3] > 0 and geom not in out:
                    out.append(geom)
            if out:
                return out
    with contextlib.suppress(Exception):
        proc = run(
            ["xrandr", "--listmonitors"], capture_output=True, text=True, timeout=2
        )
        if proc.returncode == 0:
            return parse_monitors(proc.stdout)
    return []


def breath(now: float, period: float = 2.8) -> float:
    """0..1..0 smooth cycle for the live dot. Pure."""
    import math

    return 0.5 - 0.5 * math.cos(2 * math.pi * ((now % period) / period))


class _Panel:
    """One monitor's card: a full-screen ASCII frame (src/watch_ascii.py).

    The canvas is opaque on its own, so if the ASCII renderer fails the
    cover stays up and only a plain title remains.
    """

    def __init__(self, win, geom, display: str, mono: str) -> None:
        import tkinter as tk

        x, y, w, h = geom
        self.win = win
        win.configure(bg=BG, cursor="none")
        with contextlib.suppress(Exception):
            win.attributes("-fullscreen", False)
        win.geometry(f"{w}x{h}{x:+d}{y:+d}")
        win.protocol("WM_DELETE_WINDOW", lambda: None)
        win.update_idletasks()
        # Fullscreen lands on the monitor the window sits on.
        win.attributes("-fullscreen", True)
        win.attributes("-topmost", True)
        c = tk.Canvas(
            win, width=w, height=h, bg=BG, highlightthickness=0, cursor="none"
        )
        c.pack(fill="both", expand=True)
        self.c = c
        self.status_text = ""
        self.eyes = None
        self.plain = None
        try:
            from watch_ascii import AsciiCard

            self.eyes = AsciiCard(c, w, h, mono)
        except Exception:
            self._fallback()

    def _fallback(self) -> None:
        with contextlib.suppress(Exception):
            w, h = self.c.winfo_width(), self.c.winfo_height()
            self.plain = self.c.create_text(
                w / 2,
                h / 2,
                text=_spaced(MESSAGE),
                fill=IDLE_TEXT,
                font=("TkFixedFont", max(18, int(w / 60))),
            )

    def _drop_eyes(self) -> None:
        # Only remove the decoration. Never quit, stop capture, or unlock.
        with contextlib.suppress(Exception):
            self.eyes.destroy()
        self.eyes = None
        self._fallback()

    def paint(
        self,
        g: float,
        pulse: float,
        clock: str,
        status: str,
        rec_on: bool,
        recording: bool,
        live: float,
    ) -> None:
        self.status_text = status
        if self.eyes is None:
            return
        try:
            self.eyes.paint(g, pulse, clock, status, rec_on, recording, live)
        except Exception:
            self._drop_eyes()

    def update_eyes(self, now: float, target, active: bool) -> None:
        if self.eyes is None:
            return
        try:
            self.eyes.update(now, target=target, active=active)
        except Exception:
            self._drop_eyes()

    def saving(self, title: str, status: str) -> None:
        self.status_text = status
        if self.eyes is not None:
            try:
                self.eyes.saving(title, status)
                return
            except Exception:
                self._drop_eyes()
        if self.plain is not None:
            with contextlib.suppress(Exception):
                self.c.itemconfigure(self.plain, text=_spaced(title))

    def destroy(self, root) -> None:
        if self.eyes is not None:
            with contextlib.suppress(Exception):
                self.eyes.destroy()
        with contextlib.suppress(Exception):
            self.c.destroy()
        if self.win is not root:
            with contextlib.suppress(Exception):
                self.win.destroy()


class WatchScreen:
    """A fullscreen card on every monitor. Only STOP_KEYBIND closes it.

    Plain always-on-top windows: nothing is suspended, so Claude, builds
    and downloads keep running underneath. On the keybind the cards stay
    up showing "SAVING" while `saver` stitches the footage (off the UI
    thread), then close.
    """

    MONITOR_CHECK_S = 4.0

    def __init__(self, watcher: Watcher, geoms: list | None = None, saver=None) -> None:
        import tkinter as tk

        self.watcher = watcher
        self.saver = saver
        self.result: dict | None = None
        self.root = tk.Tk(className="jarvis-watch")
        self.root.title("Jarvis Watch")
        self.held: set[str] = set()
        self.glow = 0.0
        self.closing = False
        self._stop_polled = 0.0
        self._display = _font(
            self.root, ("Inter Display Light", "Inter Display", "Inter")
        )
        self._mono = _font(
            self.root, ("Adwaita Mono", "DejaVu Sans Mono"), "TkFixedFont"
        )
        self._fixed_geoms = geoms is not None
        self._monitor_at = time.time()
        self.panels: list[_Panel] = []
        self.geoms: list = []
        self._build(geoms if geoms is not None else monitors())
        # bind_all covers every monitor's window.
        self.root.bind_all("<KeyPress>", self._key_down)
        self.root.bind_all("<KeyRelease>", self._key_up)
        for seq in ("<Button>", "<ButtonRelease>", "<MouseWheel>"):
            self.root.bind_all(seq, lambda e: "break")

    def _build(self, geoms: list) -> None:
        """(Re)create one card per monitor."""
        import tkinter as tk

        if not geoms:
            geoms = [
                (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
            ]
        old = self.panels
        fresh = []
        try:
            for geom in geoms:
                win = tk.Toplevel(self.root, class_="jarvis-watch")
                win.title("Jarvis Watch")
                try:
                    fresh.append(_Panel(win, geom, self._display, self._mono))
                except Exception:
                    win.destroy()
                    raise
        except Exception:
            for panel in fresh:
                panel.destroy(self.root)
            raise
        self.panels = fresh
        self.geoms = list(geoms)
        self.root.withdraw()
        for panel in old:
            panel.destroy(self.root)

    def _key_down(self, event) -> str:
        self.held.add(event.keysym)
        if not self.closing and is_stop_combo(event.keysym, event.state, self.held):
            self.close()
        return "break"

    def _key_up(self, event) -> str:
        self.held.discard(event.keysym)
        return "break"

    def _keep_on_top(self) -> None:
        if self.closing:
            return
        # A monitor plugged in or out: re-lay a card on each screen.
        if (
            not self._fixed_geoms
            and time.time() - self._monitor_at > self.MONITOR_CHECK_S
        ):
            self._monitor_at = time.time()
            now_geoms = monitors()
            if now_geoms and now_geoms != self.geoms:
                try:
                    self._build(now_geoms)
                except Exception:
                    # A newly connected output without a cover is a protection failure.
                    fail_safe_lock()
        for p in self.panels:
            with contextlib.suppress(Exception):
                p.win.attributes("-topmost", True)
                p.win.lift()
        # Keep the keyboard on a card so the keybind always lands.
        with contextlib.suppress(Exception):
            if self.root.focus_get() is None and self.panels:
                self.panels[0].win.focus_force()
        self.root.after(1500, self._keep_on_top)

    def _tick(self) -> None:
        if self.closing:
            return
        now = time.time()
        if not self.panels or any(not p.win.winfo_exists() for p in self.panels):
            # Stop heartbeat: the independent guard locks even if Tk swallows this error.
            raise RuntimeError("Watch cover lost")
        if now - self._stop_polled >= 0.25:
            self._stop_polled = now
            with contextlib.suppress(OSError):
                heartbeat_path().touch()
            if stop_path().exists():
                with contextlib.suppress(OSError):
                    stop_path().unlink()
                self.close()
                return
        wt = self.watcher
        live = wt.armed(now) and now - wt.last_motion < HIGHLIGHT_HOLD_S
        target = 1.0 if live else 0.0
        # Snap on fast, fade off slowly.
        rate = 0.45 if target > self.glow else 0.06
        self.glow += (target - self.glow) * rate
        if abs(self.glow - target) < 0.01:
            self.glow = target
        g = self.glow
        pulse = 0.85 + 0.15 * abs(((now * 2.2) % 2) - 1) if g > 0.5 else 1.0
        if not wt.armed(now):
            status = f"A R M I N G   ·   {int(wt.armed_at - now) + 1}"
        elif not wt.camera_ok:
            status = "W A I T I N G   F O R   C A M E R A"
        else:
            n = wt.recorder.events
            since = time.strftime("%H:%M", time.localtime(wt.started))
            status = f"A R M E D   ·   {n}   {'E V E N T' if n == 1 else 'E V E N T S'}   ·   S I N C E   {since}"
        recording = wt.recorder.recording
        rec_on = recording and int(now * 2) % 2 == 0
        clock = time.strftime("%H:%M")
        dot = breath(now)
        gaze = wt.motion_target if live and wt.camera_ok else None
        for p in self.panels:
            with contextlib.suppress(Exception):
                p.paint(g, pulse, clock, status, rec_on, recording, dot)
            p.update_eyes(now, gaze, live)
        self.root.after(50, self._tick)

    def close(self) -> None:
        if self.closing:
            return
        restore_owner()
        self.closing = True
        n = self.watcher.recorder.events
        status = (
            f"{n}   {'E V E N T' if n == 1 else 'E V E N T S'}"
            if n
            else "N O T H I N G   M O V E D"
        )
        for p in self.panels:
            with contextlib.suppress(Exception):
                p.saving("SAVING", status)
        with contextlib.suppress(Exception):
            self.root.update_idletasks()
        if self.saver is None:
            self.root.after(150, self.root.quit)
            return

        def work() -> None:
            try:
                self.result = self.saver()
            except Exception:
                self.result = None

        thread = threading.Thread(target=work, name="watch-save", daemon=True)
        thread.start()
        self._await_save(thread)

    def _await_save(self, thread) -> None:
        if thread.is_alive():
            # Keep the eye animating: stitching a long session takes a
            # while and a static card would look frozen.
            for p in self.panels:
                p.update_eyes(time.time(), None, False)
            self.root.after(50, lambda: self._await_save(thread))
            return
        for p in self.panels:
            p.saving("SAVED", p.status_text)
            p.update_eyes(time.time(), None, False)
        self.root.after(900, self.root.quit)

    def run(self) -> None:
        self.root.after(0, self._tick)
        self.root.after(300, self._keep_on_top)
        self.root.mainloop()
        with contextlib.suppress(Exception):
            self.root.destroy()


def main() -> int:
    if is_running() and running_pid() != os.getpid():
        return 0
    enter_guest()
    with contextlib.suppress(OSError):
        stop_path().unlink()
    pid_path().write_text(str(os.getpid()))
    folder = state_dir() / time.strftime("session-%Y%m%d-%H%M%S")
    watcher = Watcher(folder)
    saved: dict = {}
    lock = threading.RLock()

    def save_once() -> dict:
        with lock:
            if "result" not in saved:
                saved["result"] = save_session(watcher)
            return saved["result"]

    def _term(*_a) -> None:
        # Killed from outside (logout, shutdown): still save the footage.
        if guest_path().exists():
            fail_safe_lock()
        announce(save_once(), wait_s=0.0)
        with contextlib.suppress(OSError):
            pid_path().unlink()
        with contextlib.suppress(OSError):
            status_path().unlink()
        os._exit(0)

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGHUP, _term)
    try:
        watcher.start()
        try:
            subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "guard",
                    str(os.getpid()),
                ],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
            )
        except OSError:
            fail_safe_lock()
        WatchScreen(watcher, saver=save_once).run()
    except Exception:
        fail_safe_lock()
        raise
    finally:
        result = save_once()
        with contextlib.suppress(OSError):
            pid_path().unlink()
        with contextlib.suppress(OSError):
            status_path().unlink()
        announce(result)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    if sys.argv[1:2] == ["guard"]:
        guard_watch(int(sys.argv[2]))
        raise SystemExit(0)
    if sys.argv[1:2] == ["stop"]:
        # `jarvis-watch-stop`: works from a TTY or the global shortcut.
        got = request_stop()
        with (
            contextlib.suppress(OSError),
            open(state_dir() / "stop-requests.log", "a") as fh,
        ):
            fh.write(f"{time.strftime('%F %T')} stop requested running={got}\n")
        raise SystemExit(0 if got else 1)
    raise SystemExit(main())
