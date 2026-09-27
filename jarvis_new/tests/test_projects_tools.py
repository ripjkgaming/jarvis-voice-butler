"""Voice tools for background projects: start over the bridge, return at
once, speak the result when it lands (while the call is live)."""

import asyncio
import types

import pytest
from livekit.agents.llm import ToolError

from system import projects_tools
from system.projects_tools import ProjectTools, spoken_result


class _Session:
    def __init__(self):
        self.said = []

    async def say(self, text):
        self.said.append(text)


def _ctx():
    return types.SimpleNamespace(session=_Session())


async def test_research_project_starts_and_reports(monkeypatch):
    monkeypatch.setattr(projects_tools, "POLL_S", 0.01)
    calls = []
    states = iter(["running", "done"])

    def fake(method, path, body=None):
        calls.append((method, path, body))
        if method == "POST":
            return {"ok": True, "project": {"id": "p1", "status": "running"}}
        return {
            "ok": True,
            "project": {
                "id": "p1",
                "kind": "research",
                "title": "batteries",
                "status": next(states),
                "summary": "They are close.",
            },
        }

    monkeypatch.setattr(projects_tools, "_bridge_call", fake)
    tools, ctx = ProjectTools(), _ctx()
    out = await ProjectTools.research_project(tools, ctx, topic="solid state batteries")
    assert "underway" in out["say"]
    assert calls[0] == (
        "POST",
        "/projects",
        {"kind": "research", "topic": "solid state batteries"},
    )
    await asyncio.wait_for(asyncio.gather(*tools._tasks), 2)
    assert ctx.session.said == [
        "Research on batteries is complete, Sir. They are close."
    ]


async def test_code_project_passes_effort_and_dir(monkeypatch):
    seen = {}

    def fake(method, path, body=None):
        seen["body"] = body
        return {"ok": True, "project": {"id": "c1", "status": "running"}}

    monkeypatch.setattr(projects_tools, "_bridge_call", fake)
    tools = ProjectTools()
    out = await ProjectTools.code_project(
        tools,
        types.SimpleNamespace(session=None),
        task="refactor the api",
        directory="~/code/app",
        effort="MAX",
    )
    assert "coding engine" in out["say"]
    assert seen["body"] == {
        "kind": "code",
        "task": "refactor the api",
        "directory": "~/code/app",
        "variant": "max",
    }
    for t in tools._tasks:
        t.cancel()


async def test_unreachable_bridge_is_a_tool_error(monkeypatch):
    monkeypatch.setattr(projects_tools, "_bridge_call", lambda *a, **k: None)
    with pytest.raises(ToolError):
        await ProjectTools.research_project(
            ProjectTools(), _ctx(), topic="anything at all"
        )
    with pytest.raises(ToolError):
        await ProjectTools.open_projects(ProjectTools(), _ctx())


def test_spoken_result():
    assert "failed" in spoken_result(
        {"status": "failed", "title": "x", "error": "rate limited"}
    )
    assert "done" in spoken_result({"status": "done", "kind": "code", "title": "x"})


async def test_open_projects_carries_navigation(monkeypatch):
    sent = []

    def fake(method, path, body=None):
        sent.append(body)
        return {"ok": True}

    monkeypatch.setattr(projects_tools, "_bridge_call", fake)
    monkeypatch.setattr("projects.BUS.active", lambda: True, raising=False)
    out = await ProjectTools.open_projects(
        ProjectTools(), _ctx(), command="can you do project two"
    )
    assert sent[-1]["args"]["commands"] == [{"action": "select", "index": 2}]
    assert out["say"]
    with pytest.raises(ToolError):
        await ProjectTools.open_projects(ProjectTools(), _ctx(), command="make me a sandwich")
