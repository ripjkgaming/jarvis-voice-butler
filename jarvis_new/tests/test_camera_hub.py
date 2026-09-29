"""Tests for the shared webcam hub (src/camera_hub.py). Hermetic: fake
captures and throwaway $JARVIS_HOME, never the real camera."""

import json
import time

import numpy as np
import pytest

import camera_hub


@pytest.fixture()
def home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis"))


def _frame(color: int = 127):
    return np.full((720, 1280, 3), color, dtype=np.uint8)


def test_clean_who_rejects_traversal() -> None:
    assert camera_hub.clean_who("eyes") == "eyes"
    assert camera_hub.clean_who("../evil") is None
    assert camera_hub.clean_who("") is None
    assert camera_hub.clean_who("a/b") is None


def test_request_release_expiry(home) -> None:
    assert camera_hub.request("eyes", 60.0)
    assert camera_hub.active_leases() == ["eyes"]
    assert camera_hub.has_lease()
    camera_hub.release("eyes")
    assert camera_hub.active_leases() == []
    # Expired leases don't count.
    assert camera_hub.request("eyes", 0.5)
    assert camera_hub.lease_until("eyes") > time.time()
    future = time.time() + 3600.0
    assert camera_hub.active_leases(now=future) == []
    assert not camera_hub.has_lease(now=future)


def test_request_rejects_bad_who(home) -> None:
    assert not camera_hub.request("../evil", 10.0)
    assert not camera_hub.request("", 10.0)


def test_latest_frame_roundtrip_and_staleness(home) -> None:
    import cv2

    assert camera_hub.latest_frame() is None  # nothing published yet
    assert camera_hub._publish(_frame())
    got = camera_hub.latest_frame()
    assert got is not None and got.shape == (720, 1280, 3)
    meta = camera_hub.latest_meta()
    assert meta["w"] == 1280 and meta["h"] == 720
    # Stale sidecar -> None even though the jpg is there.
    stale = dict(meta, ts=time.time() - 60.0)
    camera_hub.latest_meta_path().write_text(json.dumps(stale))
    assert camera_hub.latest_frame(max_age_s=1.5) is None
    # Missing sidecar -> None.
    camera_hub.latest_meta_path().unlink()
    assert camera_hub.latest_frame() is None
    assert cv2.imread(str(camera_hub.latest_path())) is not None  # jpg remains


def test_snapshot_returns_fresh_frame(home, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(camera_hub, "request", lambda who, s: True)
    assert camera_hub._publish(_frame(200))
    got = camera_hub.snapshot("eyes", timeout=1.0)
    assert got is not None and got.shape == (720, 1280, 3)


def test_snapshot_timeout_when_no_frame(home, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(camera_hub, "request", lambda who, s: True)
    assert camera_hub.snapshot("eyes", timeout=0.4) is None


class _FakeCap:
    def __init__(self, frames: int = 10, fail_read: bool = False) -> None:
        self.left = frames
        self.fail_read = fail_read
        self.released = False

    def read(self):
        if self.fail_read or self.left <= 0:
            return False, None
        self.left -= 1
        return True, _frame()

    def release(self) -> None:
        self.released = True


def _run_briefly(hub: camera_hub.CameraHub, seconds: float = 0.8) -> None:
    import threading

    stop = threading.Event()
    thread = threading.Thread(target=hub.run, args=(stop,), daemon=True)
    thread.start()
    time.sleep(seconds)
    stop.set()
    thread.join(timeout=5.0)
    assert not thread.is_alive()


def test_hub_publishes_while_leased(home) -> None:
    caps = [_FakeCap()]

    def factory():
        cap = caps[0]
        return cap

    assert camera_hub.request("eyes", 30.0)
    _run_briefly(camera_hub.CameraHub(open_capture=factory))
    assert camera_hub.latest_path().is_file()
    assert camera_hub.latest_frame() is not None
    assert caps[0].released  # camera released on shutdown


def test_hub_survives_open_failure(home) -> None:
    assert camera_hub.request("eyes", 30.0)
    _run_briefly(
        camera_hub.CameraHub(open_capture=lambda: None), seconds=0.6
    )  # must not raise


def test_hub_releases_camera_when_idle(home, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(camera_hub, "IDLE_RELEASE_S", 0.3)
    caps = [_FakeCap(frames=1000)]

    def factory():
        return caps[0]

    assert camera_hub.request("eyes", 0.6)
    _run_briefly(camera_hub.CameraHub(open_capture=factory), seconds=1.6)
    assert caps[0].released  # lease lapsed mid-run -> camera let go


def test_request_stores_fps(home) -> None:
    assert camera_hub.request("eyes", 10, fps=15)
    data = json.loads((camera_hub.leases_dir() / "eyes.json").read_text())
    assert data["fps"] == 15


def test_request_fps_capped_at_max(home) -> None:
    assert camera_hub.request("eyes", 10, fps=999)
    data = json.loads((camera_hub.leases_dir() / "eyes.json").read_text())
    assert data["fps"] == camera_hub.MAX_FPS


def test_wanted_fps_default_max_and_expiry(home) -> None:
    assert camera_hub.wanted_fps() == camera_hub.FPS  # no leases
    assert camera_hub.request("low", 60, fps=8)
    assert camera_hub.request("high", 60, fps=15)
    assert camera_hub.wanted_fps() == 15  # max of live leases
    assert camera_hub.request("plain", 60)  # no fps ask: ignored
    assert camera_hub.wanted_fps() == 15
    future = time.time() + 3600.0
    assert camera_hub.wanted_fps(now=future) == camera_hub.FPS  # expired ignored
