"""Tests for the camera presence gatekeeper (src/presence.py)."""

import numpy as np

from presence import SNAP_PATH, check_presence, count_faces, snapshot


def test_snapshot_writes_a_readable_file(tmp_path) -> None:
    dest = tmp_path / "desk.jpg"
    out = snapshot(dest)
    if out is None:
        # No camera in this environment: must fail soft, never raise.
        assert True
        return
    assert out.is_file() and out.stat().st_size > 0


def test_blank_image_has_no_faces(tmp_path) -> None:
    import cv2

    blank = tmp_path / "blank.jpg"
    cv2.imwrite(str(blank), np.zeros((480, 640, 3), dtype=np.uint8))
    assert count_faces(blank) == 0


def test_missing_image_is_unknown_not_absent(tmp_path) -> None:
    assert count_faces(tmp_path / "nope.jpg") is None


def test_check_presence_always_valid_shape() -> None:
    result = check_presence()
    assert result["status"] in ("present", "absent", "unknown")
    assert isinstance(result["faces"], int)
    assert isinstance(result["say"], str) and result["say"]


def test_gate_feeds_shared_desk_snapshot() -> None:
    # The gate writes the same path the webcam_snapshot tool reads.
    assert str(SNAP_PATH) == "/tmp/jarvis-desk.jpg"
