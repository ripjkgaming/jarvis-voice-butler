"""Sandbox launch isolation: no browser singleton or live window side effects."""

import asyncio
from types import SimpleNamespace

import pytest

from system import core, launcher


def test_sandbox_browser_has_own_profile_and_bus(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.delenv("JARVIS_SANDBOX_BUS_ADDRESS", raising=False)
    argv, env = core._sandbox_launch(["brave", "https://youtube.com"], ":99")
    assert env["DISPLAY"] == ":99"
    assert env["DBUS_SESSION_BUS_ADDRESS"] != "unix:path=/run/user/1000/bus"
    assert any(arg.startswith(f"--user-data-dir={tmp_path}/") for arg in argv)
    assert "--ozone-platform=x11" in argv


def test_sandbox_url_opener_uses_isolated_browser(monkeypatch):
    monkeypatch.setattr(
        core.shutil, "which", lambda name: "/usr/bin/brave" if name == "brave" else None
    )
    argv, _ = core._sandbox_launch(["xdg-open", "https://example.com"], ":99")
    assert argv[0] == "/usr/bin/brave"
    assert any(arg.startswith("--user-data-dir=") for arg in argv)


@pytest.mark.asyncio
async def test_sandbox_playback_never_reads_live_focus(monkeypatch):
    from system import window_ctl

    monkeypatch.setenv("JARVIS_DESKTOP_SANDBOX", ":99")
    monkeypatch.setattr(core, "require_local", lambda: None)
    monkeypatch.setattr(core.shutil, "which", lambda name: "/usr/bin/brave")
    monkeypatch.setattr(
        window_ctl, "active_title", lambda: pytest.fail("read live focus")
    )
    calls = []

    async def spawn(*argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(pid=123)

    monkeypatch.setattr(core.asyncio, "create_subprocess_exec", spawn)
    await core.SystemTools.play_media(core.SystemTools(), None, query="")
    assert calls[0][1]["env"]["DISPLAY"] == ":99"
    assert any(arg.startswith("--user-data-dir=") for arg in calls[0][0])


@pytest.mark.asyncio
async def test_sandbox_launch_window_followups_stay_on_sandbox(monkeypatch):
    monkeypatch.setenv("JARVIS_DESKTOP_SANDBOX", ":99")
    monkeypatch.setattr(core, "require_local", lambda: None)
    monkeypatch.setattr(core, "_resolve_app", lambda app: "/usr/bin/kcalc")
    monkeypatch.setattr(launcher, "priority_app", lambda *args, **kwargs: None)
    calls = []

    async def run_cmd(*argv, **kwargs):
        calls.append(kwargs)
        return 0, "0x1 0 user Calculator", ""

    async def spawn(*argv, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(pid=123)

    async def no_sleep(*args):
        pass

    monkeypatch.setattr(core, "run_cmd", run_cmd)
    monkeypatch.setattr(core.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(core.asyncio, "sleep", no_sleep)
    instance = core.SystemTools()
    await core.SystemTools.open_app(instance, None, "calculator")
    await asyncio.gather(*instance._tasks)
    assert len(calls) >= 3
    assert all(call.get("env", {}).get("DISPLAY") == ":99" for call in calls)
