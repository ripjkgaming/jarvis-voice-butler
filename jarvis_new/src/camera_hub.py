"""Shared webcam hub: one reader for every feature that needs frames.

v4l2 allows a single reader, but presence snapshots, eyes and gestures
all want frames. Features take short leases instead of opening the
camera themselves; the hub loop (hosted by the bridge) keeps one
cv2.VideoCapture open while any lease is live and publishes the newest
frame atomically to $JARVIS_HOME/cam/latest.jpg (+ latest.json).

Lease files: $JARVIS_HOME/cam/leases/<who>.json {"until": <epoch>}.
Fail-soft throughout: open failures back off, nothing here ever raises
out of the loop.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
from pathlib import Path

#: Grab rate while any lease is live.
FPS = 6.0

#: Ceiling for a lease's own fps request (watch mode records video).
MAX_FPS = 20.0

#: Camera released after this long with no live lease (LED off).
IDLE_RELEASE_S = 5.0

#: Backoff after a failed open, doubled up to this cap.
OPEN_RETRY_S = 1.0
OPEN_RETRY_MAX_S = 10.0

CAMERA_INDEX = 0
FRAME_W = 1280
FRAME_H = 720

_WHO_OK = re.compile(r"^[a-z0-9_-]{1,40}$")


def jarvis_home() -> Path:
    """Base dir honoring $JARVIS_HOME (default ~/.jarvis). Pure (env)."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def cam_dir() -> Path:
    """$JARVIS_HOME/cam (created on demand). Never raises."""
    try:
        path = jarvis_home() / "cam"
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return jarvis_home() / "cam"


def leases_dir() -> Path:
    """Lease directory (created on demand). Never raises."""
    try:
        path = cam_dir() / "leases"
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return cam_dir() / "leases"


def latest_path() -> Path:
    """Newest hub frame. Pure (env)."""
    return cam_dir() / "latest.jpg"


def latest_meta_path() -> Path:
    """Sidecar {ts, w, h} for the newest frame. Pure (env)."""
    return cam_dir() / "latest.json"


def clean_who(who: str) -> str | None:
    """Safe lease name, or None (guards against directory traversal). Pure."""
    who = (who or "").strip().lower()
    return who if _WHO_OK.match(who) else None


def _lease_path(who: str) -> Path | None:
    safe = clean_who(who)
    return leases_dir() / f"{safe}.json" if safe else None


def request(who: str, seconds: float, fps: float | None = None) -> bool:
    """Take (or refresh) a camera lease for `seconds`. Never raises.

    `fps` asks the hub to grab faster while this lease lives (the hub
    runs at the highest rate any live lease wants, capped at MAX_FPS).
    """
    path = _lease_path(who)
    if path is None:
        return False
    try:
        seconds = max(0.5, min(3600.0, float(seconds)))
    except (TypeError, ValueError):
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        rec: dict = {"until": time.time() + seconds}
        if fps is not None:
            with contextlib.suppress(TypeError, ValueError):
                rec["fps"] = max(0.5, min(MAX_FPS, float(fps)))
        tmp.write_text(json.dumps(rec))
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def release(who: str) -> None:
    """Drop one lease. Never raises."""
    try:
        path = _lease_path(who)
        if path is not None:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def lease_until(who: str, now: float | None = None) -> float:
    """Lease expiry epoch for `who`, 0 when none/expired/unreadable. Pure-ish."""
    _ = now
    path = _lease_path(who)
    if path is None:
        return 0.0
    try:
        data = json.loads(path.read_text())
        until = float(data.get("until", 0.0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0
    return until


def active_leases(now: float | None = None) -> list[str]:
    """Lease names still unexpired. Never raises."""
    now = time.time() if now is None else now
    try:
        files = list(leases_dir().glob("*.json"))
    except OSError:
        return []
    live = []
    for path in files:
        if not _WHO_OK.match(path.stem):
            continue
        try:
            until = float(json.loads(path.read_text()).get("until", 0.0))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if until > now:
            live.append(path.stem)
    return sorted(live)


def wanted_fps(now: float | None = None, default: float = FPS) -> float:
    """Highest fps any live lease asks for (at least `default`). Never raises."""
    now = time.time() if now is None else now
    best = default
    try:
        files = list(leases_dir().glob("*.json"))
    except OSError:
        return best
    for path in files:
        if not _WHO_OK.match(path.stem):
            continue
        try:
            data = json.loads(path.read_text())
            if float(data.get("until", 0.0)) > now and "fps" in data:
                best = max(best, min(MAX_FPS, float(data["fps"])))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return best


def has_lease(now: float | None = None) -> bool:
    """Any live lease? Never raises."""
    return bool(active_leases(now))


def latest_meta() -> dict:
    """Sidecar {ts, w, h} of the newest frame, {} when missing. Never raises."""
    try:
        data = json.loads(latest_meta_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def latest_frame(max_age_s: float = 1.5):
    """Newest hub frame as a BGR array, or None when missing/stale. Fail-soft."""
    try:
        meta = latest_meta()
        try:
            age = time.time() - float(meta.get("ts", 0.0))
        except (TypeError, ValueError):
            return None
        if age > max_age_s:
            return None
        import cv2

        frame = cv2.imread(str(latest_path()))
        return frame if frame is not None else None
    except Exception:
        return None


def snapshot(who: str, timeout: float = 4.0):
    """Fresh hub frame: lease briefly, wait for a frame newer than the call.

    Returns a BGR array, or None on timeout. Fail-soft, never raises.
    """
    try:
        if not request(who, max(2.0, timeout + 1.0)):
            return None
        # A frame already fresh counts — no need to wait for the next.
        frame = latest_frame(max_age_s=1.5)
        if frame is not None:
            return frame
        before = latest_meta().get("ts", 0.0)
        try:
            before = float(before)
        except (TypeError, ValueError):
            before = 0.0
        deadline = time.time() + max(0.5, timeout)
        while time.time() < deadline:
            frame = latest_frame(max_age_s=timeout + 2.0)
            if frame is not None:
                try:
                    fresh = float(latest_meta().get("ts", 0.0)) > before
                except (TypeError, ValueError):
                    fresh = False
                if fresh or before == 0.0:
                    return frame
            time.sleep(0.15)
        return None
    except Exception:
        return None


def _open_capture():
    """1280x720 MJPG capture, or None. Fail-soft."""
    try:
        import cv2

        cap = cv2.VideoCapture(CAMERA_INDEX)
        if not cap.isOpened():
            with contextlib.suppress(Exception):
                cap.release()
            return None
        with contextlib.suppress(Exception):
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        return cap
    except Exception:
        return None


def _publish(frame) -> bool:
    """Atomically write latest.jpg + latest.json. Never raises."""
    try:
        import cv2

        path, meta = latest_path(), latest_meta_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Keep a .jpg extension on the temp file: OpenCV picks the
        # writer from it.
        tmp = path.with_name(path.stem + ".tmp.jpg")
        if not cv2.imwrite(str(tmp), frame):
            with contextlib.suppress(OSError):
                tmp.unlink()
            return False
        os.replace(tmp, path)
        h, w = frame.shape[:2]
        meta_tmp = meta.with_suffix(".tmp")
        meta_tmp.write_text(json.dumps({"ts": time.time(), "w": w, "h": h}))
        os.replace(meta_tmp, meta)
        return True
    except Exception:
        return False


class CameraHub:
    """One shared reader; inject a capture factory for tests."""

    def __init__(self, open_capture=None) -> None:
        self._open = open_capture or _open_capture

    def run(self, stop: threading.Event, fps: float = FPS) -> None:
        """Grab while leases live; release the camera when idle. Never raises."""
        cap = None
        last_lease_seen = 0.0
        retry_s = OPEN_RETRY_S
        try:
            while not stop.is_set():
                try:
                    live = has_lease()
                except Exception:
                    live = False
                now = time.time()
                if live:
                    last_lease_seen = now
                    if cap is None:
                        cap = self._safe_open()
                        if cap is None:
                            stop.wait(retry_s)
                            retry_s = min(OPEN_RETRY_MAX_S, retry_s * 2)
                            continue
                        retry_s = OPEN_RETRY_S
                    try:
                        ok, frame = cap.read()
                    except Exception:
                        ok, frame = False, None
                    if ok and frame is not None:
                        _publish(frame)
                    else:
                        # Dead stream: drop it so the next pass reopens.
                        with contextlib.suppress(Exception):
                            cap.release()
                        cap = None
                    try:
                        rate = wanted_fps(now, fps)
                    except Exception:
                        rate = fps
                    # Pace from the loop start so read/encode time counts.
                    stop.wait(max(0.0, 1.0 / max(0.5, rate) - (time.time() - now)))
                else:
                    if cap is not None and now - last_lease_seen >= IDLE_RELEASE_S:
                        with contextlib.suppress(Exception):
                            cap.release()
                        cap = None
                    stop.wait(0.5)
        finally:
            if cap is not None:
                with contextlib.suppress(Exception):
                    cap.release()

    def _safe_open(self):
        try:
            return self._open()
        except Exception:
            return None


def start_thread(fps: float = FPS) -> tuple[threading.Thread, threading.Event]:
    """Run a hub in a daemon thread. Returns (thread, stop event)."""
    hub = CameraHub()
    stop = threading.Event()

    def _loop() -> None:
        with contextlib.suppress(Exception):
            hub.run(stop, fps)

    thread = threading.Thread(target=_loop, name="camera-hub", daemon=True)
    thread.start()
    return thread, stop
