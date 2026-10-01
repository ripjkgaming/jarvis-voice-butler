"""Window switching + background playback (system/window_ctl.py)."""

from __future__ import annotations

import subprocess

import pytest
from livekit.agents.llm import ToolError

from system import kwin_windows, window_ctl


def test_candidates_aliases_and_filler() -> None:
    assert window_ctl.candidates("the browser window") == [
        "browser",
        "brave",
        "firefox",
        "chrome",
        "chromium",
    ]
    assert window_ctl.candidates("Roblox")[:2] == ["roblox", "sober"]
    assert window_ctl.candidates("  ") == []


def _run_ok(argv, **kw):
    return subprocess.CompletedProcess(argv, 0, "", "")


def test_focus_via_wmctrl_when_not_plasma(monkeypatch) -> None:
    monkeypatch.setattr(kwin_windows, "available", lambda env=None: False)
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return _run_ok(argv)

    ok, say = window_ctl.focus(
        "spotify",
        run=run,
        which=lambda n: "/usr/bin/wmctrl",
        active=lambda: "Spotify Premium",
        sleep=lambda s: None,
    )
    assert ok and calls == [["wmctrl", "-a", "spotify"]] and "in front" in say


def test_focus_unknown_window_is_a_failure(monkeypatch) -> None:
    monkeypatch.setattr(kwin_windows, "available", lambda env=None: False)

    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, "", "")

    ok, say = window_ctl.focus(
        "nothing", run=run, which=lambda n: "/usr/bin/wmctrl", sleep=lambda s: None
    )
    assert ok is False and "can't find" in say


def test_focus_that_did_not_come_forward_is_reported(monkeypatch) -> None:
    monkeypatch.setattr(kwin_windows, "available", lambda env=None: False)
    ok, say = window_ctl.focus(
        "spotify",
        run=lambda a, **k: _run_ok(a),
        which=lambda n: "/x",
        active=lambda: "Chemistry notes - Kate",
        sleep=lambda s: None,
    )
    assert ok is False and "still in front" in say


def test_focus_via_kwin(monkeypatch) -> None:
    monkeypatch.setattr(kwin_windows, "available", lambda env=None: True)
    monkeypatch.setattr(
        kwin_windows, "act", lambda a, q, run=None: ("Spotify Premium", 1)
    )
    ok, _ = window_ctl.focus("spotify", active=lambda: "", sleep=lambda s: None)
    assert ok


@pytest.mark.asyncio
async def test_focus_window_tool_raises_when_it_fails(monkeypatch) -> None:
    from system.window_tools import WindowTools

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(
        window_ctl, "focus", lambda q, **k: (False, "I can't find a window, Sir.")
    )
    with pytest.raises(ToolError):
        await WindowTools.focus_window(WindowTools(), None, "zzz")  # type: ignore[arg-type]
    monkeypatch.setattr(
        window_ctl, "focus", lambda q, **k: (True, "Spotify is in front.")
    )
    out = await WindowTools.focus_window(WindowTools(), None, "spotify")  # type: ignore[arg-type]
    assert out["say"] == "Spotify is in front."


@pytest.mark.asyncio
async def test_play_media_returns_focus_to_previous_window(monkeypatch) -> None:
    import asyncio

    from system import core

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(
        core.shutil, "which", lambda n: "/usr/bin/brave" if "brave" in n else None
    )

    class P:
        pid = 1

    async def fake_exec(*a, **k):
        return P()

    monkeypatch.setattr(core.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(window_ctl, "active_title", lambda: "Chemistry notes - Kate")
    restored = []
    monkeypatch.setattr(
        window_ctl, "restore", lambda t, **k: restored.append(t) or True
    )
    real_sleep = asyncio.sleep
    monkeypatch.setattr(core.asyncio, "sleep", lambda s: real_sleep(0))
    # The player window has grabbed focus.
    shown = {"t": "Despacito - YouTube - Brave"}
    monkeypatch.setattr(
        window_ctl, "active_title", lambda: shown["t"] if restored == [] else "x"
    )
    tools = core.SystemTools()
    # previous window is read before launch, so make that call return Kate once.
    calls = {"n": 0}

    def title():
        calls["n"] += 1
        return (
            "Chemistry notes - Kate"
            if calls["n"] == 1
            else "Despacito - YouTube - Brave"
        )

    monkeypatch.setattr(window_ctl, "active_title", title)
    out = await core.SystemTools.play_media(tools, None, "")  # type: ignore[arg-type]
    for t in list(tools._tasks):
        await t
    assert "Playing" in out["say"]
    assert restored and restored[0] == "Chemistry notes - Kate"


@pytest.mark.asyncio
async def test_play_media_show_leaves_window_in_front(monkeypatch) -> None:
    from system import core

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(
        core.shutil, "which", lambda n: "/usr/bin/brave" if "brave" in n else None
    )

    class P:
        pid = 1

    async def fake_exec(*a, **k):
        return P()

    monkeypatch.setattr(core.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(window_ctl, "active_title", lambda: "Kate")
    restored = []
    monkeypatch.setattr(
        window_ctl, "restore", lambda t, **k: restored.append(t) or True
    )
    tools = core.SystemTools()
    await core.SystemTools.play_media(tools, None, "", show=True)  # type: ignore[arg-type]
    assert restored == [] and not tools._tasks
