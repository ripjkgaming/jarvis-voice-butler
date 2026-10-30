"""Owner-authorized filesystem and shell access; no real commands or desktop input."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jarvis_rt import ToolError
from system import core, files_tools


@pytest.fixture(autouse=True)
def owner(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_FULL_FILESYSTEM", "1")
    monkeypatch.setenv("JARVIS_COMMAND_ACCESS", "1")
    monkeypatch.setattr(core.phone_session, "guest_required", lambda: False)


def test_all_drives_are_resolvable():
    for path in (
        "/",
        "/mnt/data/Finished projects",
        "/run/media/ripjk/USB/video.mp4",
        "/etc/hosts",
    ):
        assert str(core._resolve_user_path(path)) == path
        assert str(files_tools.resolve_safe(path)) == path


async def test_shell_commands_need_no_extra_approval_and_preserve_quoting(monkeypatch):
    run = AsyncMock(return_value=(0, "hello world", ""))
    monkeypatch.setattr(core, "run_cmd", run)
    tools = core.SystemTools()
    command = 'printf "hello  world" | cat'
    for _ in range(2):
        assert (await tools.run_command(None, command))["rc"] == "0"
    assert run.call_args.args == ("/bin/bash", "-lc", command)


async def test_commands_still_refuse_guest_and_cloud(monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(core, "run_cmd", run)
    monkeypatch.setattr(core.phone_session, "guest_required", lambda: True)
    with pytest.raises(ToolError, match="Guest mode"):
        await core.SystemTools().run_command(None, "echo hello")
    monkeypatch.setattr(core.phone_session, "guest_required", lambda: False)
    monkeypatch.setenv("JARVIS_LOCAL", "0")
    with pytest.raises(ToolError, match="own machine"):
        await core.SystemTools().run_command(None, "echo hello")
    run.assert_not_called()


def test_command_is_direct_even_with_adaptive_tiering(monkeypatch):
    import agent

    monkeypatch.setattr(
        agent.tool_usage, "plan_cold", lambda ids, protected: set(ids) - set(protected)
    )
    assistant = agent.Assistant(llm="openai/gpt-4.1-mini")
    assert "run_command" in {t.id for t in assistant.tools}
    assert "Finished projects" in assistant.instructions
    assert "video editing output folder" in assistant.instructions


async def test_watch_start_does_not_report_failure_after_entering_guest(monkeypatch):
    import agent

    guest = [False]
    monkeypatch.setattr(core.phone_session, "guest_required", lambda: guest[0])

    async def start():
        guest[0] = True
        return {"say": "Watching the laptop, Sir."}

    tool = SimpleNamespace(id="start_watch_mode", _func=start)
    agent._wrap_tools_with_timing([tool])
    result = await tool._func()
    assert "Watching" in result["say"]


async def test_media_player_launch_is_detached_not_killed_at_command_timeout(
    monkeypatch,
):
    launch = AsyncMock(return_value=(0, "", ""))
    run = AsyncMock()
    monkeypatch.setattr(core, "launch_gui", launch, raising=False)
    monkeypatch.setattr(core, "run_cmd", run)
    result = await core.SystemTools().run_command(
        None, 'vlc "/mnt/data/Finished projects/latest video.mp4"'
    )
    launch.assert_awaited_once_with(
        "vlc", "/mnt/data/Finished projects/latest video.mp4"
    )
    run.assert_not_called()
    assert result["launch_requested"] is True


async def test_listing_latest_returns_exact_paths_and_timestamps(tmp_path):
    import os

    old = tmp_path / "old.mp4"
    new = tmp_path / "latest video.mp4"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    os.utime(old, (100, 100))
    os.utime(new, (200, 200))
    result = await core.SystemTools().list_dir(None, str(tmp_path), sort_by="newest")
    assert result["entries"][0]["path"] == str(new)
    assert result["entries"][0]["modified_at"] == 200


async def test_detached_gui_launch_returns_without_waiting_for_exit(monkeypatch):
    import asyncio
    from unittest.mock import Mock

    import system
    import system.launcher

    exited = asyncio.Event()
    proc = SimpleNamespace(wait=exited.wait, kill=Mock())
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(system.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(system.launcher, "require_launch_allowed", lambda argv: None)
    assert (await system.launch_gui("vlc", "test.mp4"))[0] == 0
    assert spawn.call_args.kwargs["start_new_session"] is True
    proc.kill.assert_not_called()
    exited.set()
    await asyncio.gather(*list(system._GUI_REAPERS))


async def test_broken_symlink_does_not_break_directory_listing(tmp_path):
    (tmp_path / "broken").symlink_to(tmp_path / "missing")
    result = await core.SystemTools().list_dir(None, str(tmp_path))
    assert result["entries"][0]["name"] == "broken"
