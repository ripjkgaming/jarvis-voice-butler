"""Structured Project Archive navigation: the model drives the window with
one validated step per call, no regex grammar needed."""

import types

import pytest
from livekit.agents.llm import ToolError

import projects
from projects import build_nav_commands
from system import projects_tools
from system.projects_tools import ProjectTools

PROJECTS = [
    {"id": "p-battery", "title": "Battery chemistry deep dive"},
    {"id": "p-solar", "title": "Solar panel comparison"},
]


def _ctx():
    return types.SimpleNamespace(session=None)


@pytest.mark.parametrize("a", ["show", "hide", "back", "abort", "close_document"])
def test_plain_actions(a):
    assert build_nav_commands(a) == ([{"action": a}], "")


def test_filter():
    for word, want in [("research", "research"), ("coding", "code"), ("code", "code"),
                       ("all", "all"), ("everything", "all")]:
        assert build_nav_commands("filter", project=word)[0] == [
            {"action": "filter", "filter": want}
        ]
    assert build_nav_commands("filter", project="banana")[0] is None


def test_open_document_needs_number():
    assert build_nav_commands("open_document")[0] is None
    assert build_nav_commands("open_document", number=2)[0] == [
        {"action": "open_document", "index": 2}
    ]


def test_scroll():
    assert build_nav_commands("scroll")[0] == [
        {"action": "scroll", "mode": "start", "speed": "slow"}
    ]
    assert build_nav_commands("scroll", mode="start", speed="quickly")[0][0]["speed"] == "fast"
    assert build_nav_commands("scroll", mode="top")[0] == [{"action": "scroll", "mode": "top"}]
    assert build_nav_commands("scroll", mode="sideways")[0] is None


def test_select_by_name_partial_and_missing():
    cmds, err = build_nav_commands("select", project="the battery one", projects=PROJECTS)
    assert cmds == [{"action": "select", "project_id": "p-battery",
                     "title": "Battery chemistry deep dive"}]
    cmds, err = build_nav_commands("select", project="quantum", projects=PROJECTS)
    assert cmds is None and "quantum" in err


def test_delete_by_name_partial_and_missing():
    cmds, err = build_nav_commands("delete", project="battery", projects=PROJECTS)
    assert cmds == [{"action": "delete", "project_id": "p-battery",
                     "title": "Battery chemistry deep dive"}]
    cmds, err = build_nav_commands("delete", project="quantum", projects=PROJECTS)
    assert cmds is None and "quantum" in err


def test_select_and_delete_by_number_or_selected():
    assert build_nav_commands("select", number=3)[0] == [{"action": "select", "index": 3}]
    assert build_nav_commands("select", number=-1)[0] == [{"action": "select", "index": -1}]
    assert build_nav_commands("select")[0] is None
    assert build_nav_commands("delete")[0] == [{"action": "delete"}]
    assert build_nav_commands("delete", number=2)[0] == [{"action": "delete", "index": 2}]


def test_no_confirm_delete_and_unknown_actions():
    assert "confirm_delete" not in projects.NAV_ACTIONS
    assert build_nav_commands("confirm_delete")[0] is None
    assert build_nav_commands("explode")[0] is None


def test_tools_registered():
    names = {getattr(t, "info", None) and t.info.name for t in ProjectTools().tools}
    assert {"list_archive", "navigate_archive"} <= names


async def test_navigate_posts_projects_ui(monkeypatch):
    sent = []

    def fake(method, path, body=None):
        sent.append((method, path, body))
        return {"ok": True}

    monkeypatch.setattr(projects_tools, "_bridge_call", fake)
    out = await ProjectTools.navigate_archive(
        ProjectTools(), _ctx(), action="open_document", number=1
    )
    method, path, body = sent[-1]
    assert (method, path, body["tool"]) == ("POST", "/tool", "projects_ui")
    assert body["args"]["commands"] == [{"action": "open_document", "index": 1}]
    assert out["say"]


async def test_navigate_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(projects_tools, "_bridge_call", lambda *a, **k: calls.append(a))
    with pytest.raises(ToolError):
        await ProjectTools.navigate_archive(ProjectTools(), _ctx(), action="explode")
    assert not calls  # invalid input never reaches the bridge
    for reply in (None, {"ok": False, "error": "nope"}):
        monkeypatch.setattr(projects_tools, "_bridge_call", lambda *a, r=reply, **k: r)
        with pytest.raises(ToolError):
            await ProjectTools.navigate_archive(ProjectTools(), _ctx(), action="back")


async def test_list_archive(monkeypatch):
    monkeypatch.setattr(
        projects_tools, "_bridge_call",
        lambda *a, **k: {"ok": True, "projects": [
            {"title": "A", "kind": "research", "status": "done"},
            {"title": "B", "kind": "code", "status": "running"}]},
    )
    out = await ProjectTools.list_archive(ProjectTools(), _ctx())
    assert [r["number"] for r in out["projects"]] == [1, 2]
    assert out["projects"][1]["title"] == "B"
    monkeypatch.setattr(projects_tools, "_bridge_call", lambda *a, **k: {"ok": True, "projects": []})
    assert "empty" in (await ProjectTools.list_archive(ProjectTools(), _ctx()))["say"]
    monkeypatch.setattr(projects_tools, "_bridge_call", lambda *a, **k: None)
    with pytest.raises(ToolError):
        await ProjectTools.list_archive(ProjectTools(), _ctx())
