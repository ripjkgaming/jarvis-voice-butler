""" "Jarvis, let's play chess" launches chess (not system control) and hangs up."""

import subprocess
import sys
import types

import pytest

import agent as agent_mod
import games_launch
import prompts
from agent import RARE_SYSTEM_TOOL_IDS, Assistant


def _assistant():
    return Assistant(browser=None, llm=None)


def test_launch_chess_is_a_direct_tool_not_a_handoff():
    ids = [t.id for t in _assistant().tools]
    assert "launch_chess" in ids
    assert "launch_chess" not in RARE_SYSTEM_TOOL_IDS
    assert not "launch_chess".startswith("transfer_")
    assert agent_mod._never_demote("launch_chess")


def test_prompt_routes_chess_to_launch_chess_not_system_control():
    text = prompts.AGENT_INSTRUCTIONS
    line = next(ln for ln in text.splitlines() if "launch_chess" in ln)
    assert "chess" in line.lower()
    assert "never to transfer_to_system_control" in line
    assert "Very good, Sir. The board awaits." in line
    assert "ends this call" in line


def test_system_control_handoff_says_chess_is_not_its_job():
    doc = Assistant.transfer_to_system_control.__doc__ or ""
    assert "launch_chess" in doc
    tool = next(t for t in _assistant().tools if t.id == "transfer_to_system_control")
    assert "launch_chess" in str(tool.info.description)


def test_tool_description_mentions_the_trigger_phrases():
    tool = next(t for t in _assistant().tools if t.id == "launch_chess")
    desc = str(tool.info.description).lower()
    assert "let's play chess" in desc and "ends this call" in desc


class _FakeCtx:
    def __init__(self):
        self.session = types.SimpleNamespace()


async def test_tool_spawns_chess_voice_and_ends_the_call(monkeypatch):
    a = _assistant()
    spawned, ended = [], []
    monkeypatch.setattr(
        games_launch,
        "launch_chess_detached",
        lambda argv=None: spawned.append(1) or (True, "chess --voice"),
    )

    async def fake_end(ctx):
        ended.append(ctx)
        return "bye"

    monkeypatch.setattr(a._end_call_tool, "_end_call", fake_end)
    ctx = _FakeCtx()
    out = await a.launch_chess(ctx)
    assert spawned == [1] and ended == [ctx]
    assert "Very good, Sir. The board awaits." in out


async def test_tool_does_not_hang_up_if_chess_failed_to_start(monkeypatch):
    a = _assistant()
    ended = []
    monkeypatch.setattr(
        games_launch, "launch_chess_detached", lambda argv=None: (False, "no such file")
    )

    async def fake_end(ctx):
        ended.append(1)

    monkeypatch.setattr(a._end_call_tool, "_end_call", fake_end)
    out = await a.launch_chess(_FakeCtx())
    assert "could not start chess" in out and ended == []


def test_spawn_is_detached_and_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    marker = tmp_path / "ran.txt"
    ok, detail = games_launch.launch_chess_detached(
        [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('hi')"]
    )
    assert ok and sys.executable in detail
    for _ in range(100):
        if marker.exists():
            break
        import time

        time.sleep(0.05)
    assert marker.read_text() == "hi"
    assert (tmp_path / "logs" / "chess.log").exists()


def test_spawn_failure_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    ok, detail = games_launch.launch_chess_detached(["/definitely/not/a/binary"])
    assert not ok and detail


def test_chess_command_prefers_path_then_repo_launcher(monkeypatch):
    monkeypatch.setattr(games_launch.shutil, "which", lambda n: "/usr/local/bin/chess")
    assert games_launch.chess_command() == ["/usr/local/bin/chess", "--voice"]
    monkeypatch.setattr(games_launch.shutil, "which", lambda n: None)
    cmd = games_launch.chess_command()
    assert cmd[-1] == "--voice" and (
        cmd[0].endswith("scripts/chess") or cmd[0] == sys.executable
    )


def test_launcher_script_exists_and_is_executable():
    launcher = games_launch.REPO / "scripts" / "chess"
    assert launcher.is_file() and launcher.stat().st_mode & 0o111
    text = launcher.read_text()
    assert "games.cli" in text and "uv run" not in text.replace("(no uv sync", "")


def test_games_launch_has_no_livekit_import():
    src = (games_launch.REPO / "src" / "games_launch.py").read_text()
    assert "livekit" not in src and "genai" not in src
    _ = (pytest, subprocess)
