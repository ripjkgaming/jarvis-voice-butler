"""Watch mode: guard the laptop while Sir is away.

"Jarvis, I'm leaving" / "Jarvis, watch the laptop" (bridge voice route
"watch_mode") launches this file as its own process. It:

1. watches the webcam (a high-fps lease on the shared camera hub, or the
   camera directly when no hub is running) and records a clip every time
   something moves, with a short pre-roll so the start isn't lost;
2. covers the screen with a fullscreen "WORK ONGOING" card that lights
   up while motion is being seen;
3. ignores every key, click and voice command except STOP_KEYBIND. On
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

#: New clips stop below this much free disk (events are still counted).
MIN_FREE_BYTES = 3 * 1024**3

_WORDS = r"(?:the |my |this )?(?:laptop|computer|pc|desk|room)"
_START = re.compile(
    r"^(?:ok(?:ay)? |alright |right )?(?:i'?m|i am|im) (?:leaving|heading out|off|going out)"
    r"(?: now| for (?:a (?:bit|while|minute|second))| for now)?$"
    rf"|^(?:please )?(?:watch|guard|keep an eye on|look after|mind) {_WORDS}(?: for me| please| while i'?m (?:gone|away|out))?$"
    r"|^(?:start |enter |activate |turn on )?(?:watch|guard|sentry) mode(?: on| please)?$"
)


def parse_command(text: str) -> bool:
    """Is this cleaned voice text a watch-mode start? Pure.

    Takes the wake-word-stripped, lowercased command. Anchored on both
    ends so "I'm leaving for school at eight, remind me" stays with the
    model. There is intentionally no voice command to stop.
    """
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


def launch(popen=subprocess.Popen) -> dict:
    """Start watch mode as a detached process. Never raises.

    Wrapped in systemd-inhibit so the laptop neither sleeps nor idles
    into the lock screen while it is watching.
    """
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
        state_dir().mkdir(parents=True, exist_ok=True)
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

    def update(self, frame) -> bool:
        """Feed one BGR frame; True when it differs enough from the scene."""
        import cv2

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
        return self.last_fraction >= self.area


# --- clips ------------------------------------------------------------------


def _stamp(frame, t: float):
    """Copy of frame with a date/time burn-in, bottom left."""
    import cv2

    out = frame.copy()
    text = time.strftime("%Y-%m-%d  %H:%M:%S", time.localtime(t))
    h = out.shape[0]
    scale = max(0.5, h / 900)
    org = (int(16 * scale), h - int(18 * scale))
    cv2.putText(out, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(out, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 1, cv2.LINE_AA)
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
            self._fps_ema = gap if self._fps_ema is None else 0.9 * self._fps_ema + 0.1 * gap
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
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(listing),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
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
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="watch-capture", daemon=True)

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
        source = FrameSource()
        gap = 1.0 / WATCH_FPS
        try:
            while not self._stop.is_set():
                got = None
                with contextlib.suppress(Exception):
                    got = source.read()
                if got is None:
                    self._stop.wait(gap / 2 if source.mode == "hub" else 0.5)
                    continue
                t, frame = got
                self.camera_ok = True
                try:
                    moving = self.detector.update(frame)
                except Exception:
                    moving = False
                moving = moving and self.armed(t)
                if moving:
                    self.last_motion = t
                with contextlib.suppress(Exception):
                    self.recorder.push(frame, moving, t)
                self._write_status()
                if source.mode == "hub":
                    self._stop.wait(gap / 3)
        finally:
            source.release()

    def _write_status(self) -> None:
        with contextlib.suppress(Exception):
            tmp = status_path().with_suffix(".tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "since": self.started,
                        "events": self.recorder.events,
                        "recording": self.recorder.recording,
                        "last_motion": self.last_motion,
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

        speak.speak(welcome_line(result), source="watch", min_gap_s=0.0, title="Watch mode")
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
IDLE_TEXT = "#8a939e"
ALERT_TEXT = "#ffffff"
ALERT = "#ff3b30"
ALERT_BAND = "#2a0906"
META_TEXT = "#4a525c"
LIVE = "#5fb3a1"


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
    """Every connected monitor (XWayland/X11 layout). [] when unknown."""
    with contextlib.suppress(Exception):
        proc = run(["xrandr", "--listmonitors"], capture_output=True, text=True, timeout=3)
        if proc.returncode == 0:
            return parse_monitors(proc.stdout)
    return []


def breath(now: float, period: float = 2.8) -> float:
    """0..1..0 smooth cycle for the live dot. Pure."""
    import math

    return 0.5 - 0.5 * math.cos(2 * math.pi * ((now % period) / period))


class _Panel:
    """One monitor's card: canvas items the shared tick recolours."""

    def __init__(self, win, geom, display: str, mono: str) -> None:
        import tkinter as tk

        x, y, w, h = geom
        self.win = win
        win.configure(bg=BG, cursor="none")
        with contextlib.suppress(Exception):
            win.attributes("-fullscreen", False)
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.protocol("WM_DELETE_WINDOW", lambda: None)
        win.update_idletasks()
        # Fullscreen lands on the monitor the window sits on.
        win.attributes("-fullscreen", True)
        win.attributes("-topmost", True)
        c = tk.Canvas(win, width=w, height=h, bg=BG, highlightthickness=0, cursor="none")
        c.pack(fill="both", expand=True)
        self.c = c
        size = max(28, int(w / 26))
        meta = max(10, int(w / 150))
        band_h = int(size * 2.6)
        self.band = c.create_rectangle(0, h / 2 - band_h / 2, w, h / 2 + band_h / 2, fill=BG, outline="")
        self.frame = c.create_rectangle(3, 3, w - 3, h - 3, outline=BG, width=6)
        self.title = c.create_text(w / 2, h / 2, text=_spaced(MESSAGE), fill=IDLE_TEXT, font=(display, size))
        self.rule = c.create_line(
            w / 2 - size * 1.5, h / 2 + size * 1.1, w / 2 + size * 1.5, h / 2 + size * 1.1,
            fill=DIM_TEXT, width=2,
        )
        pad = int(w / 40)
        c.create_text(pad, pad, anchor="nw", text="J A R V I S   ·   W A T C H", fill=META_TEXT, font=(mono, meta))
        self.clock = c.create_text(w - pad, pad, anchor="ne", text="", fill=META_TEXT, font=(mono, meta))
        self.status_y = h - pad * 1.4
        self.status = c.create_text(w / 2, self.status_y, text="", fill=META_TEXT, font=(mono, meta))
        # Breathing dot left of the status line: the card is live, not frozen.
        self.live_r = max(3.0, meta * 0.32)
        self.live = c.create_oval(0, 0, 0, 0, fill=BG, outline="")
        self.rec = c.create_oval(0, 0, 0, 0, fill=BG, outline="")
        self.rec_label = c.create_text(pad + meta * 2.2, self.status_y, anchor="w", text="", fill=ALERT, font=(mono, meta))
        self.rec_xy = (pad, self.status_y, meta * 0.55)

    def paint(self, g: float, pulse: float, clock: str, status: str, rec_on: bool, recording: bool, live: float) -> None:
        c = self.c
        c.itemconfigure(self.title, fill=_mix(IDLE_TEXT, ALERT_TEXT, g))
        c.itemconfigure(self.band, fill=_mix(BG, ALERT_BAND, g * pulse))
        c.itemconfigure(self.frame, outline=_mix(BG, ALERT, g * pulse))
        c.itemconfigure(self.rule, fill=_mix(DIM_TEXT, ALERT, g))
        c.itemconfigure(self.clock, text=clock)
        c.itemconfigure(self.status, text=status)
        box = c.bbox(self.status)
        if box and status:
            r = self.live_r
            cx, cy = box[0] - r * 7, self.status_y
            c.coords(self.live, cx - r, cy - r, cx + r, cy + r)
            c.itemconfigure(self.live, fill=_mix(DIM_TEXT, LIVE, live))
        else:
            c.itemconfigure(self.live, fill=BG)
        x, y, r = self.rec_xy
        c.coords(self.rec, x, y - r, x + 2 * r, y + r)
        c.itemconfigure(self.rec, fill=ALERT if rec_on else BG)
        c.itemconfigure(self.rec_label, text="R E C" if recording else "")

    def saving(self, title: str, status: str) -> None:
        c = self.c
        c.itemconfigure(self.band, fill=BG)
        c.itemconfigure(self.rec, fill=BG)
        c.itemconfigure(self.live, fill=BG)
        c.itemconfigure(self.frame, outline=BG)
        c.itemconfigure(self.rule, fill=DIM_TEXT)
        c.itemconfigure(self.title, text=_spaced(title), fill=IDLE_TEXT)
        c.itemconfigure(self.status, text=status)
        c.itemconfigure(self.rec_label, text="")

    def destroy(self, root) -> None:
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
        self._display = _font(self.root, ("Inter Display Light", "Inter Display", "Inter"))
        self._mono = _font(self.root, ("Adwaita Mono", "DejaVu Sans Mono"), "TkFixedFont")
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
            geoms = [(0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())]
        for p in self.panels:
            p.destroy(self.root)
        self.panels = []
        self.geoms = list(geoms)
        for i, geom in enumerate(self.geoms):
            win = self.root if i == 0 else tk.Toplevel(self.root, class_="jarvis-watch")
            if i:
                win.title("Jarvis Watch")
            self.panels.append(_Panel(win, geom, self._display, self._mono))

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
        if not self._fixed_geoms and time.time() - self._monitor_at > self.MONITOR_CHECK_S:
            self._monitor_at = time.time()
            now_geoms = monitors()
            if now_geoms and now_geoms != self.geoms:
                with contextlib.suppress(Exception):
                    self._build(now_geoms)
        for p in self.panels:
            with contextlib.suppress(Exception):
                p.win.attributes("-topmost", True)
                p.win.lift()
        # Keep the keyboard on a card so the keybind always lands.
        with contextlib.suppress(Exception):
            if self.root.focus_get() is None:
                self.root.focus_force()
        self.root.after(1500, self._keep_on_top)

    def _tick(self) -> None:
        if self.closing:
            return
        now = time.time()
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
        for p in self.panels:
            p.paint(g, pulse, clock, status, rec_on, recording, dot)
        self.root.after(33, self._tick)

    def close(self) -> None:
        self.closing = True
        n = self.watcher.recorder.events
        status = f"{n}   {'E V E N T' if n == 1 else 'E V E N T S'}" if n else "N O T H I N G   M O V E D"
        for p in self.panels:
            p.saving("SAVING", status)
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
            # Breathing underline: stitching a long session takes a while
            # and a static card would look frozen.
            k = breath(time.time(), 1.6)
            for p in self.panels:
                with contextlib.suppress(Exception):
                    p.c.itemconfigure(p.rule, fill=_mix(DIM_TEXT, IDLE_TEXT, k))
            self.root.after(50, lambda: self._await_save(thread))
            return
        for p in self.panels:
            with contextlib.suppress(Exception):
                p.saving("SAVED", p.c.itemcget(p.status, "text"))
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
    state_dir().mkdir(parents=True, exist_ok=True)
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
        WatchScreen(watcher, saver=save_once).run()
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
    raise SystemExit(main())
