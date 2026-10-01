"""Hermetic tests for workshop mode (IRONMAN_SPEC §6)."""

from __future__ import annotations

import json
import subprocess

import pytest

import claude_cli
import workshop
from system.task_tools import WorkshopTools


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_CLAUDE", "1")
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(claude_cli.shutil, "which", lambda n: "/usr/bin/claude")
    return tmp_path


def fake(merge_ok=True):
    def run(argv, **kw):
        system = argv[argv.index("--system-prompt") + 1]
        tools = argv[argv.index("--tools") + 1]
        run.calls.append((system[:30], tools))
        if system.startswith("You are Jarvis, merging"):
            out = (
                json.dumps({"status": "Sir, the team suggests X.", "report": "full"})
                if merge_ok
                else "nope"
            )
            return subprocess.CompletedProcess(argv, 0, out, "")
        return subprocess.CompletedProcess(argv, 0, f"report from {system[:30]}", "")

    run.calls = []
    return run


def test_pick_roles() -> None:
    assert workshop.pick_roles("") == ["research", "code", "ops"]
    assert workshop.pick_roles("research and ops, research") == ["research", "ops"]
    assert workshop.pick_roles("marketing") == ["research", "code", "ops"]


def test_roles_get_scoped_read_only_tools(home) -> None:
    run = fake()
    told = []
    ws = workshop.run(
        "add a calendar panel",
        ["research", "code", "ops"],
        run,
        tell=lambda t, k: told.append(t),
    )
    tools = dict(run.calls)
    assert "WebSearch,WebFetch" in tools.values()
    assert "Read,Grep,Glob" in tools.values()
    for t in tools.values():
        assert not {"Bash", "Edit", "Write"} & set(t.split(","))
    assert ws["status"] == "done" and told == ["Sir, the team suggests X."]
    assert (workshop.workshops_dir() / f"{ws['id']}.md").exists()


def test_merge_failure(home) -> None:
    told = []
    ws = workshop.run(
        "goal goal goal",
        ["ops"],
        fake(merge_ok=False),
        tell=lambda t, k: told.append(t),
    )
    assert ws["status"] == "failed" and "couldn't pull" in told[0]


@pytest.mark.asyncio
async def test_tools(home, monkeypatch) -> None:
    started = []
    monkeypatch.setattr(
        workshop, "start", lambda goal, roles: started.append((goal, roles))
    )
    tools = WorkshopTools()
    said = await WorkshopTools.start_workshop(
        tools, None, goal="plan my revision week", roles="ops"
    )  # type: ignore[arg-type]
    assert started == [("plan my revision week", ["ops"])] and "ops team" in said["say"]
    assert "No workshop" in (await WorkshopTools.workshop_status(tools, None))["say"]  # type: ignore[arg-type]
    workshop.run("plan my revision week", ["ops"], fake(), tell=lambda t, k: None)
    assert (await WorkshopTools.workshop_status(tools, None))[
        "say"
    ] == "Sir, the team suggests X."  # type: ignore[arg-type]
