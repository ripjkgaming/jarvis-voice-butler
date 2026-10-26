"""No real desktop processes: regressions for launch ownership and policy."""

import pytest

import action_guard
from system import launcher


def test_response_identity_blocks_changed_launch_target_after_success():
    action_guard.reset()
    with action_guard.request_scope("response-1"):
        assert action_guard.claim("open_app", {"app": "sober"}, "model", now=0) is None
        action_guard.settle(
            "open_app", {"app": "sober"}, "model", True, "Opened Sober", now=1
        )
        dup = action_guard.claim(
            "open_app", {"url": "https://sober.example"}, "model", now=30
        )
        assert dup and dup["say"] == "Opened Sober"
    with action_guard.request_scope("response-2"):
        assert action_guard.claim("open_app", {"app": "sober"}, "model", now=31) is None


def test_pending_response_does_not_start_second_launch():
    action_guard.reset()
    with action_guard.request_scope("response-1"):
        action_guard.claim("open_app", {"app": "sober"}, "model")
        dup = action_guard.claim("open_app", {"app": "roblox website"}, "model")
        assert dup and "in progress" in action_guard.duplicate_reply(dup, "Done")["say"]


def test_failed_response_can_retry():
    action_guard.reset()
    with action_guard.request_scope("response-1"):
        action_guard.claim("open_app", {"app": "sober"}, "model")
        action_guard.settle("open_app", {"app": "sober"}, "model", False)
        assert action_guard.claim("open_app", {"app": "sober"}, "model") is None


@pytest.mark.parametrize(
    "name", ["Waydroid", "way droid", "android apps", "Play Store"]
)
def test_waydroid_names_do_not_reach_web_fallback(name):
    def unexpected(*a, **kw):
        pytest.fail("blocked request reached resolver I/O")

    decision = launcher.resolve_launch(
        name, apps=[], history=[], lucky=unexpected, llm=unexpected
    )
    assert decision.kind == "blocked" and not decision.argv


def test_waydroid_desktop_alias_blocked(tmp_path):
    import taskbar

    path = tmp_path / "camera.desktop"
    path.write_text(
        "[Desktop Entry]\nName=Camera\nExec=waydroid app launch org.lineageos.aperture\n"
    )
    assert not taskbar.launch_desktop(
        "camera.desktop",
        dirs=(tmp_path,),
        which=lambda _: "/usr/bin/gtk-launch",
        popen=lambda *a, **kw: pytest.fail("launched Waydroid"),
    )


@pytest.mark.asyncio
async def test_run_cmd_rejects_waydroid_before_process(monkeypatch):
    import asyncio

    import system

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        lambda *a, **kw: pytest.fail("launched Waydroid"),
    )
    rc, _, error = await system.run_cmd("env", "waydroid", "show-full-ui")
    assert rc != 0 and "Waydroid" in error


def test_realtime_transcript_does_not_execute(monkeypatch):
    import agent
    import bridge

    monkeypatch.setenv("JARVIS_PIPELINE", "realtime")
    monkeypatch.setattr(
        bridge, "handle_route", lambda *a: pytest.fail("second executor")
    )
    assert agent.desktop_fast_command("open sober") is None


@pytest.mark.asyncio
async def test_realtime_turn_hook_has_no_executor(monkeypatch):
    from livekit.agents import llm

    import agent
    from intent import fast_path

    monkeypatch.setenv("JARVIS_PIPELINE", "realtime")
    monkeypatch.setattr(
        fast_path, "match_async", lambda *a: pytest.fail("second executor")
    )
    assistant = agent.Assistant(browser=None, llm=None)
    await assistant.on_user_turn_completed(
        None, llm.ChatMessage(role="user", content=["open sober"])
    )


def test_wrapper_and_symlink_aliases_blocked(tmp_path):
    binary = tmp_path / "waydroid"
    binary.write_text("#!/bin/sh\nexit 0\n")
    alias = tmp_path / "camera"
    alias.symlink_to(binary)
    wrapper = tmp_path / "android-camera"
    wrapper.write_text("#!/bin/sh\nexec waydroid app launch camera\n")
    for path in (alias, wrapper):
        with pytest.raises(ValueError, match="Waydroid"):
            launcher.require_launch_allowed([str(path)])


def test_bridge_app_map_cannot_hide_waydroid(monkeypatch):
    import bridge

    monkeypatch.setattr(
        bridge, "_app_map", lambda: {"camera": ["waydroid", "show-full-ui"]}
    )
    monkeypatch.setattr(bridge, "_which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(launcher, "priority_app", lambda *a, **kw: None)
    monkeypatch.setattr(
        bridge.subprocess, "Popen", lambda *a, **kw: pytest.fail("spawned")
    )
    result = bridge.run_phone_tool("open_app", {"app": "camera"})
    assert not result["ok"] and "Waydroid" in result["error"]


@pytest.mark.asyncio
async def test_desktop_success_blocks_browser_fallback_in_same_response():
    from types import SimpleNamespace

    from tools import BrowserTools

    action_guard.reset()
    ctx = SimpleNamespace(speech_handle=SimpleNamespace(id="response-1"))
    action_guard.claim("open_app", {"app": "sober"}, "model", request_id="response-1")
    action_guard.settle(
        "open_app",
        {"app": "sober"},
        "model",
        True,
        "Opened Sober",
        request_id="response-1",
    )
    result = await BrowserTools.open_url(
        SimpleNamespace(browser=None), ctx, "https://sober.example"
    )
    assert "not verified" in result["say"]
    assert "launch" in result["say"]


@pytest.mark.asyncio
async def test_identity_follows_async_worker_and_scope_resets():
    import asyncio

    action_guard.reset()
    with action_guard.request_scope("worker-response"):
        await asyncio.to_thread(
            action_guard.claim, "open_app", {"app": "sober"}, "model"
        )
        await asyncio.to_thread(
            action_guard.settle, "open_app", {"app": "sober"}, "model", True, "Opened"
        )
        assert action_guard.launch_for_request()["say"] == "Opened"
    assert action_guard.launch_for_request() is None


@pytest.mark.asyncio
async def test_open_app_launches_once_per_response_with_real_tool_flow(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from system import core

    action_guard.reset()
    monkeypatch.setattr(core, "require_local", lambda: None)
    monkeypatch.setattr(core, "_resolve_app", lambda _: "/usr/bin/kcalc")
    monkeypatch.setattr(launcher, "priority_app", lambda *a, **kw: None)
    monkeypatch.setattr(core, "run_cmd", AsyncMock(return_value=(0, "", "")))
    monkeypatch.setattr(core, "_maximize_new_windows", AsyncMock())
    spawn = AsyncMock(return_value=SimpleNamespace(pid=123))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    owner = core.SystemTools()
    first = SimpleNamespace(speech_handle=SimpleNamespace(id="first"))
    second = SimpleNamespace(speech_handle=SimpleNamespace(id="second"))
    await core.SystemTools.open_app(owner, first, "calculator")
    duplicate = await core.SystemTools.open_app(
        owner, first, "website", "https://calculator.example"
    )
    assert "Do not launch again" in duplicate["note"]
    assert "not verified" in duplicate["say"]
    assert spawn.await_count == 1
    await core.SystemTools.open_app(owner, second, "calculator")
    assert spawn.await_count == 2
    await asyncio.gather(*owner._tasks)


@pytest.mark.asyncio
async def test_monitor_launcher_cannot_hide_waydroid(monkeypatch, tmp_path):
    import asyncio
    from unittest.mock import AsyncMock
    from livekit.agents.llm import ToolError
    from system import devices
    wrapper = tmp_path / "camera"
    wrapper.write_text("#!/bin/sh\nexec waydroid show-full-ui\n")
    monkeypatch.setattr(devices, "require_local", lambda: None)
    monkeypatch.setattr(devices, "run_cmd", AsyncMock(return_value=(0, "", "")))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **kw: pytest.fail("spawned"))
    action_guard.reset()
    with pytest.raises(ToolError, match="Waydroid"):
        await devices.DeviceTools.open_on_monitor(devices.DeviceTools(), None, str(wrapper))
