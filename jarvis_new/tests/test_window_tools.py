"""Hermetic tests for the window voice tools."""

from __future__ import annotations

import pytest

import active_window
from jarvis_rt import ToolError
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
    from system import window_ctl

    monkeypatch.setattr(kwin_windows, "available", lambda env=None: True)
    monkeypatch.setattr(window_ctl, "active_title", lambda: "Spotify Premium")
    monkeypatch.setattr(window_ctl.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        kwin_windows,
        "act",
        lambda action, q, run=None: (
            ("Spotify Premium", 1) if action == "focus" else None
        ),
    )
    said = await WindowTools.focus_window(WindowTools(), None, title="spotify")  # type: ignore[arg-type]
    assert said["say"] == "Spotify Premium is in front."
    monkeypatch.setattr(kwin_windows, "act", lambda action, q, run=None: None)
    monkeypatch.setattr(window_ctl.shutil, "which", lambda n: None)
    with pytest.raises(ToolError):
        await WindowTools.focus_window(WindowTools(), None, title="nothing")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_hide_jarvis_requests_animated_shell_fade(monkeypatch) -> None:
    calls = []
    hangups = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("subprocess.run", run)

    class EndCall:
        def __init__(self, *, end_instructions):
            hangups.append(end_instructions)

        async def _end_call(self, context):
            return "goodbye before disconnect"

    monkeypatch.setattr("system.window_tools.EndCallTool", EndCall)
    out = await WindowTools.hide_jarvis(WindowTools(), None)  # type: ignore[arg-type]
    assert calls and calls[0][0][-1] == "hide"
    assert calls[0][0][0].endswith("jarvis-shell")
    assert out == "goodbye before disconnect"
    assert hangups == [
        "Say a brief goodbye as Jarvis fades into the wallpaper, then end the call."
    ]


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
    assert ids == [
        "list_windows",
        "focus_window",
        "maximize_window",
        "restore_window",
        "bring_to_attention",
        "move_window_to_monitor",
        "arrange_windows",
        "undo_layout",
        "hide_jarvis",
    ]
