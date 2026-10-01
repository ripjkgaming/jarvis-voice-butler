"""Camera presence gatekeeper: is anybody actually at the desk?

The "other model" that permits conversation. A Haar-cascade face model
ships inside opencv-python-headless (no downloads, no network, CPU-only)
and answers one question per snapshot: face in frame or not.

Outcomes are tri-state on purpose:
- "present": at least one face → Jarvis may engage,
- "absent": camera works, no face → one dry remark, then silence,
- "unknown": camera unreadable → silent standby, never mistaken for
  absence (a broken camera must not mute the butler forever).

Snapshots land at /tmp/jarvis-desk.jpg, the same path the
`webcam_snapshot` tool reads, so the gate doubles as the feed writer.
Best-effort throughout: every failure returns "unknown", never raises.
"""

from __future__ import annotations

import time
from pathlib import Path

SNAP_PATH = Path("/tmp/jarvis-desk.jpg")

#: Ignore tiny detections (posters, background screens, noise).
MIN_FACE_PX = 60

#: First frames after open are usually dark; settle exposure first.
WARMUP_FRAMES = 5


CASCADE_URL = (
    "https://raw.githubusercontent.com/opencv/opencv/master"
    "/data/haarcascades/haarcascade_frontalface_default.xml"
)


def _cascade_path() -> Path:
    """Local Haar model: bundled copy first, else ~/.jarvis/models.

    (opencv-python-headless wheels no longer ship the XML, so first use
    downloads it once; afterwards detection is fully offline.)
    """
    try:
        import cv2  # noqa: F401
        import cv2 as _cv

        bundled = Path(_cv.data.haarcascades) / "haarcascade_frontalface_default.xml"
        if bundled.is_file():
            return bundled
    except Exception:
        pass
    return Path.home() / ".jarvis" / "models" / "haarcascade_frontalface_default.xml"


def _cascade() -> object | None:
    """Haar frontal-face model. None when unusable."""
    try:
        import cv2

        path = _cascade_path()
        if not path.is_file():
            return None
        model = cv2.CascadeClassifier(str(path))
        return model if not model.empty() else None
    except Exception:
        return None


def _hub_frame():
    """Fresh frame from the shared camera hub, or None. Fail-soft."""
    try:
        import camera_hub

        return camera_hub.snapshot("presence", timeout=4.0)
    except Exception:
        return None


def _write_frame(frame, dest: Path = SNAP_PATH) -> Path | None:
    """Write a BGR frame to dest. Returns the path, or None on failure."""
    try:
        import cv2

        dest.parent.mkdir(parents=True, exist_ok=True)
        return dest if cv2.imwrite(str(dest), frame) else None
    except Exception:
        return None


def snapshot(dest: Path = SNAP_PATH, camera: int = 0) -> Path | None:
    """Grab one settled frame. Returns the path, or None on failure.

    Prefers the shared camera hub (one v4l2 reader); falls back to a
    direct capture only when the hub produced nothing.
    """
    frame = _hub_frame()
    if frame is not None:
        written = _write_frame(frame, dest)
        if written is not None:
            return written
    try:
        import cv2

        cap = cv2.VideoCapture(camera)
        frame = None
        try:
            if not cap.isOpened():
                return None
            for _ in range(WARMUP_FRAMES):
                ok, frame = cap.read()
                if not ok:
                    frame = None
                    break
                time.sleep(0.05)
        finally:
            cap.release()
        if frame is None:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(dest), frame):
            return None
        return dest
    except Exception:
        return None


def count_faces(image_path: Path) -> int | None:
    """Faces in a snapshot file. None when unreadable (unknown, not zero)."""
    try:
        import cv2

        model = _cascade()
        if model is None:
            return None
        img = cv2.imread(str(image_path))
        if img is None:
            return None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        found = model.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(MIN_FACE_PX, MIN_FACE_PX)
        )
        return len(found)
    except Exception:
        return None


def check_presence() -> dict[str, object]:
    """One full gate evaluation. Never raises; always a valid shape."""
    snap = snapshot()
    if snap is None:
        return {
            "status": "unknown",
            "faces": -1,
            "snapshot": "",
            "say": "Camera unreadable.",
        }
    faces = count_faces(snap)
    if faces is None:
        return {
            "status": "unknown",
            "faces": -1,
            "snapshot": str(snap),
            "say": "Camera snapshot unreadable.",
        }
    if faces >= 1:
        return {
            "status": "present",
            "faces": faces,
            "snapshot": str(snap),
            "say": f"{faces} face{'s' if faces != 1 else ''} at the desk.",
        }
    return {
        "status": "absent",
        "faces": 0,
        "snapshot": str(snap),
        "say": "Desk empty, camera works.",
    }
