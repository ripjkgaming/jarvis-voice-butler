"""Hollow Hands: experimental hand-gesture control (off by default).

The mediapipe GestureRecognizer watches hub frames at ~8 fps while
enabled. A gesture must be held >= 0.8 s (debounce) and each action
cools down 3 s. Closed_Fist held 2 s stops gesture mode.

Default mapping (overridable via $JARVIS_HOME/gestures.json):
- Open_Palm   -> toggle the Jarvis mic (over wake.sock)
- Thumb_Up    -> "confirm" event (logged)
- Victory     -> summon a talk call (over wake.sock)
- Closed_Fist -> stop gesture mode (2 s hold)

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


def recognize_frame(frame, recognizer) -> str | None:
    """One BGR frame -> top gesture name. Fail-soft."""
    try:
        import cv2
        import mediapipe as mp
        import numpy as np

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=np.ascontiguousarray(rgb),
        )
        return top_gesture(recognizer.recognize(image))
    except Exception:
        return None


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
                return
            if self._recognize is None:
                return  # no model: stay quiet, keep the lease warm
            name = None
            with contextlib.suppress(Exception):
                name = self._recognize(snap)
            tripped = self._debounce.update(name, self._now())
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
