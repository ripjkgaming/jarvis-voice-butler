import asyncio

import pytest
from types import SimpleNamespace

from system import look_tools


def test_look_at_screen_pushes_window_frame_to_gemini_video(monkeypatch):
    monkeypatch.delenv("JARVIS_AUTO_APPROVE", raising=False)
    monkeypatch.setattr(look_tools, "_window_jpeg", lambda: b"jpeg")
    pushed = []
    backend = SimpleNamespace(push_video=lambda f, ts: pushed.append(f))
    session = SimpleNamespace(backend=backend)

    async def fast(_):
        return None

    monkeypatch.setattr(look_tools.asyncio, "sleep", fast)
    out = asyncio.run(look_tools._send_window_frames(session))
    assert out["status"] == "complete"
    assert pushed == [b"jpeg", b"jpeg"]


def test_window_frames_refused_when_session_video_input_disabled(monkeypatch):
    import pytest

    from jarvis_rt import ToolError

    monkeypatch.delenv("JARVIS_AUTO_APPROVE", raising=False)
    captured = []
    monkeypatch.setattr(
        look_tools, "_window_jpeg", lambda: captured.append(1) or b"jpeg"
    )
    pushed = []
    session = SimpleNamespace(
        backend=SimpleNamespace(push_video=lambda f, ts: pushed.append(f)),
        input=SimpleNamespace(video_enabled=False),
    )
    with pytest.raises(ToolError):
        asyncio.run(look_tools._send_window_frames(session))
    assert pushed == [] and captured == []


def test_window_frames_enabled_video_input_still_pushes(monkeypatch):
    monkeypatch.delenv("JARVIS_AUTO_APPROVE", raising=False)
    monkeypatch.setattr(look_tools, "_window_jpeg", lambda: b"jpeg")
    pushed = []
    session = SimpleNamespace(
        backend=SimpleNamespace(push_video=lambda f, ts: pushed.append(f)),
        input=SimpleNamespace(video_enabled=True),
    )

    async def fast(_):
        return None

    monkeypatch.setattr(look_tools.asyncio, "sleep", fast)
    assert asyncio.run(look_tools._send_window_frames(session))["status"] == "complete"
    assert pushed == [b"jpeg", b"jpeg"]


def test_corrupt_window_capture_raises_tool_error(monkeypatch):
    from jarvis_rt import ToolError

    import look

    monkeypatch.setattr(look, "capture_window", lambda: b"\x89PNG truncated")
    with pytest.raises(ToolError):
        look_tools._window_jpeg()
