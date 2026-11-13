"""Hollow Hands: experimental hand-gesture control (off by default).

The mediapipe GestureRecognizer watches hub frames at ~8 fps while
enabled. A gesture must be held >= 0.8 s (debounce) and each action
cools down 3 s. Closed_Fist held 2 s stops gesture mode.

Default mapping (overridable via $JARVIS_HOME/gestures.json):
- Open_Palm   -> toggle the Jarvis mic (over wake.sock)
- Thumb_Up    -> "confirm" event (logged)
- Victory     -> summon a talk call (over wake.sock)
- Closed_Fist -> stop gesture mode (2 s hold)
- Two-finger pose (index+middle up, ring+pinky curled) -> HOLD-TO-MUTE:
  mic mutes while the pose is held, unmutes when it is dropped. Level
  triggered, not debounced/toggled; overrides Victory.

Recognised actions are logged via system.log_action; nothing else.
Fail-soft throughout: no camera, no model, no wake listener — no crash.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import threading
import time
from pathlib import Path

#: A gesture must be held this long to count (Closed_Fist: FIST_HOLD_S).
HOLD_S = 0.8
FIST_HOLD_S = 2.0

#: Per-action cooldown after firing.
COOLDOWN_S = 3.0

#: Monitor cadence.
FPS = 8.0

#: Recognised canned gestures -> action names.
DEFAULT_MAPPING = {
    "Open_Palm": "mic_toggle",
    "Thumb_Up": "confirm",
    "Victory": "talk",
    "Closed_Fist": "stop",
}

#: Pseudo-gesture name for the hold-to-mute pose (landmark geometry).
MUTE_POSE = "Mute_Hold"
#: Pose must be seen this long before muting / absent this long before unmuting.
MUTE_ON_S = 0.25
MUTE_OFF_S = 0.6

ACTIONS = frozenset({"mic_toggle", "confirm", "talk", "stop"})


def jarvis_home() -> Path:
    """Base dir honoring $JARVIS_HOME (default ~/.jarvis). Pure (env)."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def config_path() -> Path:
    """Gesture mapping override file. Pure (env)."""
    return jarvis_home() / "gestures.json"


def load_mapping() -> dict[str, str]:
    """Canned-gesture -> action, defaults + valid file overrides. Never raises."""
    mapping = dict(DEFAULT_MAPPING)
    try:
        data = json.loads(config_path().read_text())
        if isinstance(data, dict):
            for gesture, action in data.items():
                if gesture in DEFAULT_MAPPING and action in ACTIONS:
                    mapping[gesture] = action
    except (OSError, ValueError, AttributeError):
        pass
    return mapping


class GestureDebounce:
    """Hold-to-fire + cooldown per gesture. Pure (clock injected)."""

    def __init__(
        self,
        hold_s: float = HOLD_S,
        holds: dict[str, float] | None = None,
        cooldown_s: float = COOLDOWN_S,
    ) -> None:
        self.hold_s = hold_s
        self.holds = dict(holds or {})
        self.holds.setdefault("Closed_Fist", FIST_HOLD_S)
        self.cooldown_s = cooldown_s
        self._candidate: str | None = None
        self._since = 0.0
        self._last_fired: dict[str, float] = {}

    def update(self, name: str | None, now: float) -> str | None:
        """Feed one frame's top gesture; the tripped name, or None. Pure."""
        if not name:
            self._candidate = None
            return None
        if name != self._candidate:
            self._candidate = name
            self._since = now
            return None
        need = self.holds.get(name, self.hold_s)
        if now - self._since < need:
            return None
        if now - self._last_fired.get(name, float("-inf")) < self.cooldown_s:
            return None
        self._last_fired[name] = now
        self._candidate = None  # fresh hold required for the next fire
        return name


def wake_socket_path() -> Path:
    """Unix socket of the wake_client mic-control listener. Pure (env)."""
    return jarvis_home() / "wake.sock"


def send_wake(payload: dict, timeout: float = 2.0) -> dict | None:
    """One JSON message to wake.sock; the reply, or None. Fail-soft."""
    try:
        raw = (json.dumps(payload) + "\n").encode()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as cli:
            cli.settimeout(timeout)
            cli.connect(str(wake_socket_path()))
            cli.sendall(raw)
            chunks = []
            while True:
                data = cli.recv(4096)
                if not data:
                    break
                chunks.append(data)
                if len(b"".join(chunks)) > 65536:
                    return None
        reply = json.loads(b"".join(chunks).decode())
        return reply if isinstance(reply, dict) else None
    except Exception:
        return None


def make_recognizer(model_path: str | None = None):
    """GestureRecognizer (IMAGE mode), or None when unavailable."""
    try:
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        home = os.environ.get("JARVIS_VISION_MODELS", "").strip()
        default = (
            Path(home) / "gesture_recognizer.task"
            if home
            else jarvis_home() / "models" / "vision" / "gesture_recognizer.task"
        )
        path = model_path or str(default)
        if not Path(path).is_file():
            return None
        base = mp_python.BaseOptions(model_asset_path=path)
        opts = mp_vision.GestureRecognizerOptions(
            base_options=base, running_mode=mp_vision.RunningMode.IMAGE
        )
        return mp_vision.GestureRecognizer.create_from_options(opts)
    except Exception:
        return None


def _d(a, b) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2) ** 0.5


def is_mute_pose(landmarks) -> bool:
    """Index+middle extended, ring+pinky curled (thumb ignored). Pure.

    Rotation-invariant: a finger is extended when its tip is clearly
    farther from the wrist than its PIP joint, curled when not.
    """
    try:
        wrist = landmarks[0]

        def ext(tip: int, pip: int) -> float:
            return _d(wrist, landmarks[tip]) / max(_d(wrist, landmarks[pip]), 1e-6)

        return (
            ext(8, 6) > 1.15
            and ext(12, 10) > 1.15
            and ext(16, 14) < 1.0
            and ext(20, 18) < 1.0
        )
    except (AttributeError, TypeError, IndexError):
        return False


def top_gesture(result) -> str | None:
    """Best canned-gesture name, or None when no hand seen. Pure-ish."""
    try:
        groups = result.gestures
        if not groups or not groups[0]:
            return None
        name = groups[0][0].category_name or ""
        return name if name and name != "None" else None
    except (AttributeError, TypeError, IndexError):
        return None


def recognize_hand(frame, recognizer, max_w: int = 0) -> tuple[str | None, bool]:
    """One BGR frame -> (gesture name or MUTE_POSE, hand seen). Fail-soft.

    `max_w` > 0 downscales wide frames first (inference cost ~ pixels).
    """
    try:
        import cv2
        import mediapipe as mp
        import numpy as np

        h, w = frame.shape[:2]
        if max_w and w > max_w:
            frame = cv2.resize(frame, (max_w, int(h * max_w / w)), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=np.ascontiguousarray(rgb),
        )
        result = recognizer.recognize(image)
        hands = getattr(result, "hand_landmarks", None)
        if hands and is_mute_pose(hands[0]):
            return MUTE_POSE, True
        return top_gesture(result), bool(hands)
    except Exception:
        return None, False


def recognize_frame(frame, recognizer) -> str | None:
    """One BGR frame -> top gesture name (or MUTE_POSE). Fail-soft."""
    return recognize_hand(frame, recognizer)[0]


class GestureMonitor:
    """8-fps gesture watch; inject everything for tests."""

    def __init__(
        self,
        *,
        frame=None,
        recognize=None,
        sender=None,
        speaker=None,
        mapping: dict[str, str] | None = None,
        debounce: GestureDebounce | None = None,
        now=None,
    ) -> None:
        self._frame = frame
        self._recognize = recognize
        self._send = sender or send_wake
        self._speak = speaker or (
            lambda text, source: __import__("speak").speak(
                text, source=source, min_gap_s=10
            )
        )
        self._mapping = mapping or load_mapping()
        self._debounce = debounce or GestureDebounce()
        self._now = now or time.time
        self.should_stop = False
        self._pose_since: float | None = None
        self._pose_gone: float | None = None
        self._holding_mute = False

    def _hold_mute(self, seen: bool, now: float) -> None:
        """Level-triggered mute: mute while pose seen, unmute on release."""
        if seen:
            self._pose_gone = None
            if self._pose_since is None:
                self._pose_since = now
            if not self._holding_mute and now - self._pose_since >= MUTE_ON_S:
                reply = None
                with contextlib.suppress(Exception):
                    reply = self._send({"mute": True})
                if isinstance(reply, dict) and reply.get("ok"):
                    self._holding_mute = True
        else:
            self._pose_since = None
            if not self._holding_mute:
                return
            if self._pose_gone is None:
                self._pose_gone = now
            if now - self._pose_gone >= MUTE_OFF_S:
                reply = None
                with contextlib.suppress(Exception):
                    reply = self._send({"mute": False})
                if isinstance(reply, dict) and reply.get("ok"):
                    self._holding_mute = False
                    self._pose_gone = None

    def _log(self, detail: str) -> None:
        with contextlib.suppress(Exception):
            from system import log_action

            log_action("gesture", detail)

    def fire(self, gesture: str) -> None:
        """Run the mapped action for a debounced gesture. Never raises."""
        action = self._mapping.get(gesture)
        if action == "mic_toggle":
            try:
                import speak

                status = speak.wake_status()
                muted = bool(status.get("muted"))
            except Exception:
                muted = False
            reply = None
            with contextlib.suppress(Exception):
                reply = self._send({"mute": not muted})
            ok = isinstance(reply, dict) and reply.get("ok")
            self._log(f"{gesture} mic_toggle -> muted={not muted} ok={ok}")
            with contextlib.suppress(Exception):
                self._speak("Mic off, Sir." if not muted else "Mic on, Sir.", "gesture")
        elif action == "confirm":
            self._log(f"{gesture} confirm")
            with contextlib.suppress(Exception):
                self._speak("Confirmed, Sir.", "gesture")
        elif action == "talk":
            reply = None
            with contextlib.suppress(Exception):
                reply = self._send({"talk": True})
            ok = isinstance(reply, dict) and reply.get("ok")
            self._log(f"{gesture} talk ok={ok}")
        elif action == "stop":
            self._log(f"{gesture} stop")
            self.should_stop = True

    def poll(self) -> None:
        """One frame: recognize, debounce, fire. Never raises."""
        try:
            snap = self._frame() if self._frame else None
            if snap is None:
                import camera_hub

                snap = camera_hub.latest_frame()
            if snap is None:
                self._debounce.update(None, self._now())
                self._hold_mute(False, self._now())
                return
            if self._recognize is None:
                return  # no model: stay quiet, keep the lease warm
            name = None
            with contextlib.suppress(Exception):
                name = self._recognize(snap)
            now = self._now()
            self._hold_mute(name == MUTE_POSE, now)
            if name == MUTE_POSE:
                name = None  # handled level-triggered, never debounced
            tripped = self._debounce.update(name, now)
            if tripped:
                self.fire(tripped)
        except Exception:
            pass


_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop: threading.Event | None = None


def is_running() -> bool:
    """Gesture mode active?"""
    with _lock:
        return _thread is not None and _thread.is_alive()


def start() -> bool:
    """Begin gesture watch (holds a hub lease). False if already running."""
    import camera_hub

    global _thread, _stop
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False
        stop = threading.Event()

        def _loop() -> None:
            recognizer = make_recognizer()
            mon = GestureMonitor(
                recognize=(
                    (lambda frame: recognize_frame(frame, recognizer))
                    if recognizer is not None
                    else None
                )
            )
            try:
                while not stop.is_set() and not mon.should_stop:
                    try:
                        camera_hub.request("gestures", 30.0)
                        mon.poll()
                    except Exception:
                        pass
                    stop.wait(1.0 / FPS)
            finally:
                camera_hub.release("gestures")
                with contextlib.suppress(Exception):
                    if recognizer is not None:
                        recognizer.close()

        _stop = stop
        _thread = threading.Thread(target=_loop, name="gestures", daemon=True)
        _thread.start()
        return True


def stop() -> None:
    """End gesture mode. Never raises."""
    global _thread, _stop
    with _lock:
        stop, _stop = _stop, None
        thread, _thread = _thread, None
    if stop is not None:
        with contextlib.suppress(Exception):
            stop.set()
    if thread is not None:
        with contextlib.suppress(Exception):
            thread.join(timeout=3.0)


# --- always-on hold-to-mute (bridge sidecar), tuned for ~zero resources ---

#: Idle poll rate (no hand in view) vs. active rate (hand seen recently).
IDLE_FPS = 2.0
ACTIVE_FPS = 10.0
#: Stay at ACTIVE_FPS this long after the last hand sighting.
ACTIVE_LINGER_S = 4.0
#: Inference width: 480 px is plenty for a hand held up to the webcam.
INFER_W = 480
#: Screen-lock check cadence (camera fully released while locked).
LOCK_CHECK_S = 10.0


def screen_locked() -> bool:
    """True when the login session is locked. Fail-open (False)."""
    try:
        import subprocess

        sid = os.environ.get("XDG_SESSION_ID", "").strip() or "self"
        out = subprocess.run(
            ["loginctl", "show-session", sid, "-p", "LockedHint", "--value"],
            capture_output=True, text=True, timeout=2.0,
        ).stdout.strip()
        return out == "yes"
    except Exception:
        return False


#: Motion gate: mean abs diff of a 48x27 gray thumbnail that wakes inference,
#: and a heartbeat so a perfectly still scene is still checked now and then.
MOTION_THR = 1.5
HEARTBEAT_S = 3.0


def _thumb(frame):
    """48x27 gray thumbnail for the motion gate. Fail-soft (None)."""
    try:
        import cv2

        return cv2.resize(
            cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (48, 27), interpolation=cv2.INTER_AREA
        ).astype("int16")
    except Exception:
        return None


def _mute_watch(stop: threading.Event) -> None:
    """Adaptive-rate watch that does hold-to-mute and nothing else."""
    import camera_hub

    recognizer = None
    seen = False
    last_hand = 0.0
    locked = False
    next_lock_check = 0.0

    def _recognize(frame):
        nonlocal seen
        name, seen = recognize_hand(frame, recognizer, INFER_W)
        return name

    mon = None
    prev_thumb = None
    last_infer = 0.0
    try:
        recognizer = make_recognizer()
        if recognizer is None:
            return  # no model: stay off entirely
        mon = GestureMonitor(
            recognize=_recognize,
            frame=lambda: camera_hub.latest_frame(reduced=True),
            mapping={"__none__": "noop"},  # mute pose only; no other actions
            speaker=lambda *a: None,
        )
        while not stop.is_set():
            now = time.time()
            if now >= next_lock_check:
                locked = screen_locked()
                next_lock_check = now + LOCK_CHECK_S
            if locked:
                camera_hub.release("handmute")
                mon._hold_mute(False, now)
                mon._hold_mute(False, now + MUTE_OFF_S + 1)  # never stay muted
                stop.wait(LOCK_CHECK_S / 2)
                continue
            active = now - last_hand < ACTIVE_LINGER_S
            rate = ACTIVE_FPS if active else IDLE_FPS
            with contextlib.suppress(Exception):
                camera_hub.request("handmute", 15.0, fps=rate)
            run = active or mon._holding_mute
            if not run:
                # Idle: only pay for inference when the picture changed.
                frame = camera_hub.latest_frame(reduced=True)
                th = _thumb(frame) if frame is not None else None
                if th is not None:
                    moved = prev_thumb is None or float(abs(th - prev_thumb).mean()) > MOTION_THR
                    prev_thumb = th
                    run = moved or now - last_infer >= HEARTBEAT_S
                else:
                    run = True  # let poll() handle the no-frame path
            if run:
                seen = False
                last_infer = now
                mon.poll()
                if seen:
                    last_hand = time.time()
            stop.wait(max(0.05, 1.0 / rate - (time.time() - now)))
    except Exception:
        pass
    finally:
        with contextlib.suppress(Exception):
            if mon is not None and mon._holding_mute:
                mon._send({"mute": False})  # never leave the mic muted
        with contextlib.suppress(Exception):
            camera_hub.release("handmute")
        with contextlib.suppress(Exception):
            if recognizer is not None:
                recognizer.close()


def start_thread() -> tuple[threading.Thread, threading.Event]:
    """Bridge sidecar entry: always-on hold-to-mute (kill: JARVIS_HAND_MUTE=0)."""
    stop = threading.Event()
    thread = threading.Thread(target=_mute_watch, args=(stop,), name="hand-mute", daemon=True)
    thread.start()
    return thread, stop
