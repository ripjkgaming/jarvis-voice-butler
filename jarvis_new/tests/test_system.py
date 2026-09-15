import os

import pytest
from livekit.agents.llm import ToolError

from system import LOG_PATH, LocalSystemError, require_local
from system.core import (
    DATA_DIR,
    SHORTCUTS_PATH,
    TODOS_PATH,
    SystemTools,
    _resolve_user_path,
    evaluate_math,
)


def test_evaluate_math_allows_arithmetic() -> None:
    assert evaluate_math("2 + 3 * 4") == 14
    assert evaluate_math("(10 - 4) / 3") == 2.0
    assert evaluate_math("2 ** 8") == 256


def test_evaluate_math_blocks_non_arithmetic() -> None:
    with pytest.raises(ValueError):
        evaluate_math("__import__('os').system('id')")
    with pytest.raises(ValueError):
        evaluate_math("open('/etc/passwd').read()")


def test_require_local_refuses_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JARVIS_LOCAL", raising=False)
    with pytest.raises(LocalSystemError):
        require_local()


def test_require_local_passes_with_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    require_local()


def test_resolve_user_path_confines_to_home() -> None:
    assert _resolve_user_path("/home/ripjk/Documents") is not None
    assert _resolve_user_path("~/Downloads") is not None
    assert _resolve_user_path("/tmp/jarvis-test") is not None
    assert _resolve_user_path("/etc/passwd") is None
    assert _resolve_user_path("/") is None


def test_system_tools_register_expected_ids() -> None:
    ids = [tool.id for tool in SystemTools().tools]
    for expected in (
        "tell_time",
        "do_math",
        "read_clipboard",
        "take_os_screenshot",
        "read_screen_text",
        "set_volume",
        "media_control",
        "now_playing",
        "set_brightness",
        "battery_status",
        "disk_space",
        "find_files",
        "list_dir",
        "read_file",
        "write_file",
        "move_file",
        "copy_file",
        "delete_file",
        "recent_downloads",
        "manage_todo",
        "remember_alias",
        "run_alias",
        "list_aliases",
        "open_app",
        "window_action",
        "confirm_power_action",
        "power_control",
    ):
        assert expected in ids


@pytest.mark.asyncio
async def test_system_tools_refuse_when_not_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JARVIS_LOCAL", raising=False)
    tools = SystemTools()
    with pytest.raises(ToolError, match="only available"):
        await SystemTools.tell_time(tools, None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_todo_add_list_done_roundtrip(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr("system.core.TODOS_PATH", tmp_path / "todos.json")
    tools = SystemTools()

    added = await SystemTools.manage_todo(
        tools,
        None,
        action="add",
        text="revise biology chapter",  # type: ignore[arg-type]
    )
    assert "Added" in added["say"]

    listed = await SystemTools.manage_todo(tools, None, action="list")  # type: ignore[arg-type]
    assert "biology" in listed["say"]

    # Fuzzy paraphrase still matches.
    done = await SystemTools.manage_todo(
        tools,
        None,
        action="done",
        text="study biology",  # type: ignore[arg-type]
    )
    assert "Done" in done["say"]

    cleared = await SystemTools.manage_todo(tools, None, action="clear")  # type: ignore[arg-type]
    assert "Cleared" in cleared["say"]


@pytest.mark.asyncio
async def test_alias_save_run_roundtrip(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr("system.core.SHORTCUTS_PATH", tmp_path / "shortcuts.json")
    tools = SystemTools()

    saved = await SystemTools.remember_alias(
        tools,
        None,
        name="study mode",
        command="open youtube lofi",  # type: ignore[arg-type]
    )
    assert "Remembered" in saved["say"]

    ran = await SystemTools.run_alias(tools, None, name="study mode")  # type: ignore[arg-type]
    assert ran["command"] == "open youtube lofi"


@pytest.mark.asyncio
async def test_file_write_read_delete_roundtrip(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    target = tmp_path / "note.txt"
    # tmp_path is outside /home/ripjk; patch the root for this test only.
    monkeypatch.setattr("system.core.HOME_ROOT", tmp_path.resolve())
    tools = SystemTools()

    written = await SystemTools.write_file(
        tools,
        None,
        path=str(target),
        text="hello jarvis",  # type: ignore[arg-type]
    )
    assert "Wrote" in written["say"]

    read = await SystemTools.read_file(tools, None, path=str(target))  # type: ignore[arg-type]
    assert "hello jarvis" in read["text"]

    monkeypatch.setattr(
        "system.core._trash_path", lambda p: tmp_path / "trash" / p.name
    )
    deleted = await SystemTools.delete_file(tools, None, path=str(target))  # type: ignore[arg-type]
    assert "trash" in deleted["say"]
    assert not target.exists()


@pytest.mark.asyncio
async def test_power_shutdown_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    tools = SystemTools()

    with pytest.raises(ToolError, match="confirmation"):
        await SystemTools.power_control(tools, None, action="shutdown")  # type: ignore[arg-type]

    confirmed = await SystemTools.confirm_power_action(
        tools,
        None,
        action="shutdown",  # type: ignore[arg-type]
    )
    assert "confirmed" in confirmed

    calls: list[tuple[str, ...]] = []

    async def fake_run(*argv: str, timeout: float = 10.0):
        calls.append(argv)
        return 0, "", ""

    monkeypatch.setattr("system.core.run_cmd", fake_run)
    result = await SystemTools.power_control(tools, None, action="shutdown")  # type: ignore[arg-type]
    assert "initiated" in result["say"]
    assert calls and calls[0][:2] == ("systemctl", "poweroff")

    # Confirmation is single-use.
    with pytest.raises(ToolError, match="confirmation"):
        await SystemTools.power_control(tools, None, action="shutdown")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_power_lock_runs_without_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    tools = SystemTools()
    calls: list[tuple[str, ...]] = []

    async def fake_run(*argv: str, timeout: float = 10.0):
        calls.append(argv)
        return 0, "", ""

    monkeypatch.setattr("system.core.run_cmd", fake_run)
    result = await SystemTools.power_control(tools, None, action="lock")  # type: ignore[arg-type]
    assert "Locked" in result["say"]
    assert calls and calls[0][:2] == ("loginctl", "lock-session")


def test_data_paths_stay_under_jarvis_home() -> None:
    assert str(TODOS_PATH).startswith(str(DATA_DIR))
    assert str(SHORTCUTS_PATH).startswith(str(DATA_DIR))
    assert os.environ.get("HOME", "/home/ripjk") in str(DATA_DIR)
    assert ".jarvis" in str(LOG_PATH)


def test_resolve_user_path_accepts_bare_tmp(tmp_path=None) -> None:
    from system.core import _resolve_user_path

    assert _resolve_user_path("/tmp") is not None
    assert _resolve_user_path("/tmp/x") is not None
    assert _resolve_user_path("/etc/passwd") is None


def test_remember_alias_description_covers_preferences() -> None:
    from system.core import SystemTools

    tools = {tool.id: tool for tool in SystemTools().tools}
    remember_info = tools["remember_alias"].info
    for trigger in ("remember that", "favourite", "always call this"):
        assert trigger in remember_info.description
