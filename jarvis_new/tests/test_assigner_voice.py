import asyncio
import importlib.util
import types
from pathlib import Path

import pytest
from jarvis_rt import ToolError

import projects

source = Path(projects.__file__).parent
source = (
    source / "projects_tools.py"
    if (source / "projects_tools.py").exists()
    else source / "system/projects_tools.py"
)
spec = importlib.util.spec_from_file_location("staged_projects_tools", source)
voice = importlib.util.module_from_spec(spec)
spec.loader.exec_module(voice)


async def test_code_project_launches_without_bridge(monkeypatch):
    """code_project starts a Claude Code job directly; it must never hit the bridge."""
    import claude_code_launch

    launched = []
    monkeypatch.setattr(
        voice, "_bridge_call", lambda *a, **k: pytest.fail("bridge used")
    )
    monkeypatch.setattr(
        claude_code_launch,
        "launch",
        lambda task, directory="": launched.append(task) or {"ok": True, "id": "cc-x"},
    )
    out = await voice.ProjectTools.code_project(
        voice.ProjectTools(), types.SimpleNamespace(session=None), task="make hello"
    )
    assert launched == ["make hello"] and out["job_id"] == "cc-x"


async def test_private_call_refuses_detached_work(monkeypatch):
    import phone_session

    monkeypatch.setattr(
        phone_session,
        "private_tool_error",
        lambda _: "private call refuses detached work",
    )
    monkeypatch.setattr(
        voice, "_bridge_call", lambda *a: pytest.fail("private task leaked")
    )
    with pytest.raises(ToolError, match="private call"):
        await voice.ProjectTools.code_project(
            voice.ProjectTools(), types.SimpleNamespace(session=None), task="make hello"
        )


async def test_reports_only_terminal_and_claims_once(monkeypatch):
    import projects

    monkeypatch.setattr(voice, "POLL_S", 0.001)
    states = iter(["pending", "planning", "working", "reporting", "done", "done"])

    def fake(*args):
        return {
            "project": {
                "id": "p",
                "engine": "assigner",
                "kind": "code",
                "title": "hello",
                "status": next(states),
            }
        }

    monkeypatch.setattr(voice, "_bridge_call", fake)
    claimed = []
    monkeypatch.setattr(
        projects,
        "claim_completion",
        lambda _: not claimed.append(True) and len(claimed) == 1,
    )
    said = []

    class Session:
        async def say(self, text):
            said.append(text)

    tools = voice.ProjectTools()
    context = types.SimpleNamespace(session=Session())
    tools._report_when_done(context, "p")
    await asyncio.wait_for(asyncio.gather(*tools._tasks), 2)
    tools._report_when_done(context, "p")
    await asyncio.wait_for(asyncio.gather(*tools._tasks), 2)
    assert len(said) == 1
