"""Tests for VisionTools (src/system/vision_tools.py) + the presence
hub patch. Hermetic: stubbed eyes/gestures/camera_hub, no hardware."""

import pytest
from livekit.agents.llm import ToolError

import presence
from system.vision_tools import VisionTools


@pytest.fixture()
def home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis"))


@pytest.mark.asyncio()
async def test_tools_refuse_when_not_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JARVIS_LOCAL", raising=False)
    tools = VisionTools()
    assert len(tools.tools) == 5
    with pytest.raises(ToolError, match="only available"):
        await VisionTools.look_at_me(tools, None)
    with pytest.raises(ToolError, match="only available"):
        await VisionTools.set_hand_gestures(tools, None, True)


@pytest.mark.asyncio()
async def test_look_at_me(monkeypatch: pytest.MonkeyPatch) -> None:
    import eyes

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(eyes, "describe_appearance", lambda q="": "Sharp, Sir.")
    result = await VisionTools.look_at_me(VisionTools(), None, question="tie?")
    assert result["say"] == "Sharp, Sir."


@pytest.mark.asyncio()
async def test_look_at_me_surfaces_speakable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import eyes

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(
        eyes, "describe_appearance", lambda q="": "My eyes blurred just now, Sir."
    )
    with pytest.raises(ToolError, match="blurred"):
        await VisionTools.look_at_me(VisionTools(), None)


@pytest.mark.asyncio()
async def test_posture_watch_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    import eyes

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    calls: list = []
    monkeypatch.setattr(
        eyes,
        "calibrate",
        lambda seconds=3.0: (
            calls.append("cal")
            or {"head_forward": 0.1, "shoulder_slope": 0.0, "nose_y": 0.3}
        ),
    )
    monkeypatch.setattr(eyes, "is_running", lambda: False)
    monkeypatch.setattr(eyes, "start", lambda: True)
    result = await VisionTools.start_posture_watch(VisionTools(), None)
    assert "Posture watch on" in result["say"]

    monkeypatch.setattr(eyes, "is_running", lambda: True)
    result = await VisionTools.start_posture_watch(VisionTools(), None)
    assert "Already watching" in result["say"]

    monkeypatch.setattr(eyes, "stop", lambda: calls.append("stop"))
    result = await VisionTools.stop_posture_watch(VisionTools(), None)
    assert "off" in result["say"]

    monkeypatch.setattr(eyes, "is_running", lambda: False)
    result = await VisionTools.stop_posture_watch(VisionTools(), None)
    assert "wasn't running" in result["say"]


@pytest.mark.asyncio()
async def test_posture_watch_needs_a_face(monkeypatch: pytest.MonkeyPatch) -> None:
    import eyes

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(eyes, "calibrate", lambda seconds=3.0: None)
    monkeypatch.setattr(eyes, "is_running", lambda: False)
    with pytest.raises(ToolError, match="couldn't see you"):
        await VisionTools.start_posture_watch(VisionTools(), None)


@pytest.mark.asyncio()
async def test_posture_report(monkeypatch: pytest.MonkeyPatch) -> None:
    import eyes

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(
        eyes,
        "status",
        lambda: {
            "slouch_alerts": 2,
            "phone_alerts": 1,
            "minutes_monitored": 12.5,
            "calibrated": True,
            "running": True,
        },
    )
    result = await VisionTools.posture_report(VisionTools(), None)
    assert "2 slouch" in result["say"] and "1 phone" in result["say"]


@pytest.mark.asyncio()
async def test_hand_gestures_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    import gestures

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(gestures, "is_running", lambda: False)
    monkeypatch.setattr(gestures, "start", lambda: True)
    result = await VisionTools.set_hand_gestures(VisionTools(), None, True)
    assert "Hollow Hands on" in result["say"]

    monkeypatch.setattr(gestures, "is_running", lambda: True)
    monkeypatch.setattr(gestures, "stop", lambda: None)
    result = await VisionTools.set_hand_gestures(VisionTools(), None, False)
    assert "off" in result["say"]


# --- presence hub patch --------------------------------------------------


def test_presence_prefers_hub(home, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import cv2
    import numpy as np

    import camera_hub

    frame = np.full((240, 320, 3), 150, dtype=np.uint8)
    monkeypatch.setattr(camera_hub, "snapshot", lambda who, timeout=4.0: frame)
    dest = tmp_path / "desk.jpg"
    assert presence.snapshot(dest) == dest
    assert cv2.imread(str(dest)) is not None


def test_presence_falls_back_to_direct(
    home, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import camera_hub

    monkeypatch.setattr(camera_hub, "snapshot", lambda who, timeout=4.0: None)

    class _Cap:
        def isOpened(self) -> bool:  # noqa: N802 - mirrors cv2 API
            return True

        def read(self):
            import numpy as np

            return True, np.zeros((240, 320, 3), dtype=np.uint8)

        def release(self) -> None:
            pass

    import cv2

    monkeypatch.setattr(cv2, "VideoCapture", lambda camera=0: _Cap())
    dest = tmp_path / "desk.jpg"
    assert presence.snapshot(dest) == dest


def test_presence_unknown_when_both_fail(home, monkeypatch: pytest.MonkeyPatch) -> None:
    import camera_hub

    monkeypatch.setattr(camera_hub, "snapshot", lambda who, timeout=4.0: None)

    class _DeadCap:
        def isOpened(self) -> bool:  # noqa: N802 - mirrors cv2 API
            return False

        def release(self) -> None:
            pass

    import cv2

    monkeypatch.setattr(cv2, "VideoCapture", lambda camera=0: _DeadCap())
    assert presence.snapshot() is None
    result = presence.check_presence()
    assert result["status"] == "unknown"
