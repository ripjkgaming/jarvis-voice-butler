"""Tests for src/system/focus_tools.py: registration, gating, delegation."""

import pytest
from livekit.agents.llm import ToolError

import focus
from system.focus_tools import FocusTools


def test_tools_register_expected_ids() -> None:
    ids = [tool.id for tool in FocusTools().tools]
    assert ids == ["lock_on", "focus_status", "end_focus", "review_my_work"]


@pytest.mark.asyncio
async def test_tools_refuse_when_not_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JARVIS_LOCAL", raising=False)
    tools = FocusTools()
    with pytest.raises(ToolError, match="only available"):
        await FocusTools.lock_on(tools, None)  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="only available"):
        await FocusTools.focus_status(tools, None)  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="only available"):
        await FocusTools.end_focus(tools, None)  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="only available"):
        await FocusTools.review_my_work(tools, None)  # type: ignore[arg-type]


class FakeTracker:
    def __init__(self, lock=None, end=None, look=None):
        self._lock, self._end, self._look = lock, end, look

    def lock(self, kind, mins, label):
        return self._lock

    def end(self, reason="stopped"):
        return self._end

    def look(self, question=""):
        return self._look


@pytest.mark.asyncio
async def test_lock_on_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    fake = FakeTracker(lock={"ok": True, "say": "Locked on, Sir for ten minutes."})
    monkeypatch.setattr(focus, "get_tracker", lambda: fake)
    got = await FocusTools.lock_on(FocusTools(), None, "auto", 10, "thesis")  # type: ignore[arg-type]
    assert "Locked on" in got["say"]


@pytest.mark.asyncio
async def test_lock_on_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    tools = FocusTools()
    with pytest.raises(ToolError, match="window or a tab"):
        await FocusTools.lock_on(tools, None, "banana")  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="How many minutes"):
        await FocusTools.lock_on(tools, None, "auto", "lots")  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="eight hours"):
        await FocusTools.lock_on(tools, None, "auto", 999)  # type: ignore[arg-type]
    fake = FakeTracker(lock={"ok": False, "say": "I can't see any active window, Sir."})
    monkeypatch.setattr(focus, "get_tracker", lambda: fake)
    with pytest.raises(ToolError, match="can't see"):
        await FocusTools.lock_on(tools, None, "window", 10)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_focus_status_and_end(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(focus, "spoken_status", lambda: "Ten minutes on thesis, Sir.")
    got = await FocusTools.focus_status(FocusTools(), None)  # type: ignore[arg-type]
    assert "thesis" in got["say"]
    fake = FakeTracker(end={"ok": True, "say": "Focus over, Sir. Flawless."})
    monkeypatch.setattr(focus, "get_tracker", lambda: fake)
    got = await FocusTools.end_focus(FocusTools(), None)  # type: ignore[arg-type]
    assert "Focus over" in got["say"]


@pytest.mark.asyncio
async def test_review_my_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    tools = FocusTools()
    fake = FakeTracker(look={"ok": True, "say": "Looking sharp, Sir."})
    monkeypatch.setattr(focus, "get_tracker", lambda: fake)
    got = await FocusTools.review_my_work(tools, None, "is this right?")  # type: ignore[arg-type]
    assert "sharp" in got["say"]
    fake = FakeTracker(look={"ok": False, "say": "That's not in front of me, Sir."})
    monkeypatch.setattr(focus, "get_tracker", lambda: fake)
    with pytest.raises(ToolError, match="not in front"):
        await FocusTools.review_my_work(tools, None)  # type: ignore[arg-type]
