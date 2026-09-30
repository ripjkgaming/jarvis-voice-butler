"""Hermetic tests for the window voice tools."""

from __future__ import annotations

import pytest
from livekit.agents.llm import ToolError

import active_window
from system import kwin_windows, window_layout
from system.window_tools import WindowTools, split_names


@pytest.fixture(autouse=True)
def local(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))


def test_split_names() -> None:
    assert split_names("firefox and code, spotify") == ["firefox", "code", "spotify"]
    assert split_names("") == []


@pytest.mark.asyncio
async def test_list_and_focus(monkeypatch) -> None:
    monkeypatch.setattr(
        active_window,
        "windows",
        lambda: [{"title": "Spotify", "active": True}, {"title": "Code"}],
    )
    out = await WindowTools.list_windows(WindowTools(), None)  # type: ignore[arg-type]
    assert out["say"] == "2 open: Spotify (active); Code"
    monkeypatch.setattr(
        kwin_windows,
        "act",
        lambda action, q: ("Spotify Premium", 1) if action == "focus" else None,
    )
    said = await WindowTools.focus_window(WindowTools(), None, title="spotify")  # type: ignore[arg-type]
    assert said["say"] == "Spotify Premium is in front."
    monkeypatch.setattr(kwin_windows, "act", lambda action, q: None)
    with pytest.raises(ToolError):
        await WindowTools.focus_window(WindowTools(), None, title="nothing")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_arrange_and_undo(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        window_layout,
        "arrange",
        lambda layout, names: calls.append((layout, names)) or True,
    )
    said = await WindowTools.arrange_windows(
        WindowTools(), None, layout="side by side", windows="firefox and code"
    )  # type: ignore[arg-type]
    assert calls == [("split", ["firefox", "code"])]
    assert "undo layout" in said["say"]
    with pytest.raises(ToolError):
        await WindowTools.arrange_windows(WindowTools(), None, layout="spiral")  # type: ignore[arg-type]
    monkeypatch.setattr(window_layout, "undo", lambda: {"windows": [{}, {}]})
    assert (await WindowTools.undo_layout(WindowTools(), None))[
        "say"
    ] == "Restored 2 windows."  # type: ignore[arg-type]
    monkeypatch.setattr(window_layout, "undo", lambda: None)
    assert "no layout" in (await WindowTools.undo_layout(WindowTools(), None))["say"]  # type: ignore[arg-type]


def test_nothing_destructive_here() -> None:
    ids = [t.id for t in WindowTools().tools]
    assert ids == ["list_windows", "focus_window", "arrange_windows", "undo_layout"]
