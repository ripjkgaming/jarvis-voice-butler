"""Camera presence checks use generated frames and never open the real camera."""

import numpy as np
import pytest

import presence
from presence import SNAP_PATH, check_presence, count_faces, snapshot


@pytest.fixture(autouse=True)
def _no_camera(monkeypatch):
    import cv2

    monkeypatch.setattr(presence, "_hub_frame", lambda: None)

    def forbidden_capture(*args, **kwargs):
        raise AssertionError("tests must provide a fake capture")

    monkeypatch.setattr(cv2, "VideoCapture", forbidden_capture)


def test_snapshot_writes_a_readable_file(tmp_path, monkeypatch) -> None:
    import cv2

    frame = np.full((480, 640, 3), 127, dtype=np.uint8)
    monkeypatch.setattr(presence, "_hub_frame", lambda: frame)
    dest = tmp_path / "desk.jpg"
    assert snapshot(dest) == dest
    assert cv2.imread(str(dest)).shape == frame.shape


def test_snapshot_releases_unopened_capture(tmp_path, monkeypatch) -> None:
    import cv2

    class ClosedCamera:
        released = False

        def isOpened(self):  # noqa: N802 - OpenCV capture interface
            return False

        def release(self):
            self.released = True

    camera = ClosedCamera()
    monkeypatch.setattr(cv2, "VideoCapture", lambda _: camera)
    assert snapshot(tmp_path / "desk.jpg") is None
    assert camera.released


def test_blank_image_has_no_faces(tmp_path) -> None:
    import cv2

    blank = tmp_path / "blank.jpg"
    cv2.imwrite(str(blank), np.zeros((480, 640, 3), dtype=np.uint8))
    assert count_faces(blank) == 0


def test_missing_image_is_unknown_not_absent(tmp_path) -> None:
    assert count_faces(tmp_path / "nope.jpg") is None


@pytest.mark.parametrize(
    ("has_snapshot", "detected", "status", "faces"),
    [
        (False, None, "unknown", -1),
        (True, None, "unknown", -1),
        (True, 0, "absent", 0),
        (True, 2, "present", 2),
    ],
)
def test_check_presence_distinguishes_absence_from_failure(
    tmp_path, monkeypatch, has_snapshot, detected, status, faces
) -> None:
    dest = tmp_path / "desk.jpg"
    monkeypatch.setattr(presence, "snapshot", lambda: dest if has_snapshot else None)
    monkeypatch.setattr(presence, "count_faces", lambda _: detected)
    result = check_presence()
    assert result["status"] == status
    assert result["faces"] == faces
    assert result["snapshot"] == (str(dest) if has_snapshot else "")
    assert isinstance(result["say"], str) and result["say"]


def test_gate_feeds_shared_desk_snapshot() -> None:
    # The gate writes the same path the webcam_snapshot tool reads.
    assert str(SNAP_PATH) == "/tmp/jarvis-desk.jpg"
