"""Hermetic tests for multi-step background tasks (IRONMAN_SPEC §5.1)."""

from __future__ import annotations

import json
import subprocess

import pytest

import agent_tasks as at
import claude_cli
from system.task_tools import TaskTools

PLAN = {
    "title": "Laptop email",
    "steps": [
        {
            "title": "Send the email to dad",
            "kind": "consequential",
            "detail": "email dad",
        },
        {"title": "Find laptops", "kind": "research", "detail": "under 1000"},
        {"title": "Draft the email", "kind": "write", "detail": "to dad"},
    ],
}


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_CLAUDE", "1")
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(claude_cli.shutil, "which", lambda n: "/usr/bin/claude")
    return tmp_path


def fake_claude(plan=PLAN, fail_on=None):
    def run(argv, **kw):
        prompt, system = kw["input"], argv[argv.index("--system-prompt") + 1]
        run.calls.append((system[:20], argv[argv.index("--tools") + 1], prompt))
        if system.startswith("You plan"):
            return subprocess.CompletedProcess(argv, 0, json.dumps(plan), "")
        if fail_on and fail_on in prompt:
            return subprocess.CompletedProcess(argv, 1, "", "rate limited")
        step = prompt.rsplit("THIS STEP", 1)[1]
        return subprocess.CompletedProcess(argv, 0, f"result for{step[:40]}", "")

    run.calls = []
    return run


def test_parse_plan_puts_acts_last_and_caps(monkeypatch) -> None:
    plan = at.parse_plan(json.dumps(PLAN))
    assert [s["kind"] for s in plan["steps"]] == ["research", "write", "consequential"]
    monkeypatch.setenv("JARVIS_TASK_MAX_STEPS", "2")
    assert len(at.parse_plan(json.dumps(PLAN))["steps"]) == 2
    assert at.parse_plan("nope") is None
    weird = at.parse_plan(json.dumps({"steps": [{"title": "x", "kind": "hack"}]}))
    assert weird["steps"][0]["kind"] == "think"


def test_runs_pauses_before_acting_then_finishes(home) -> None:
    told = []
    run = fake_claude()
    task = at.start(
        "research laptops under 1000 and email dad", runner=run, background=False
    )
    task = at.advance(
        task["id"], run, tell=lambda t, k, urgent=False: told.append((t, urgent))
    )
    task = at.get(task["id"])
    assert task["status"] == "waiting"
    assert [s["status"] for s in task["steps"]] == ["done", "done", "waiting"]
    tools_used = [c[1] for c in run.calls]
    assert tools_used == ["", "WebSearch,WebFetch", ""]  # plan, research, write
    assert "<<<RESULT 1" in run.calls[2][2]  # write step saw the research
    n_calls = len(run.calls)
    task = at.approve(task["id"], run, background=False)
    assert task["status"] == "done"
    assert len(run.calls) == n_calls  # approving never runs the act itself
    assert (
        (at.tasks_dir() / f"{task['id']}.md").read_text().startswith("# Laptop email")
    )


def test_failure_and_cancel(home) -> None:
    run = fake_claude(fail_on="Find laptops")
    task = at.start("research laptops and email dad", runner=run, background=False)
    assert at.get(task["id"])["status"] == "failed"
    t2 = at.start("another job to do today", runner=fake_claude(), background=False)
    assert at.get(t2["id"])["status"] == "waiting"
    assert at.cancel()["id"] == t2["id"]
    assert at.cancel() is None
    assert at.approve() is None


def test_resume_after_restart(home) -> None:
    at.save(
        {
            "id": "task-1",
            "goal": "g",
            "title": "g",
            "status": "running",
            "steps": [
                {
                    "title": "Think",
                    "kind": "think",
                    "detail": "",
                    "status": "done",
                    "output": "x",
                },
                {
                    "title": "Write it",
                    "kind": "write",
                    "detail": "",
                    "status": "pending",
                    "output": "",
                },
            ],
            "created": 1.0,
        }
    )
    at.advance("task-1", fake_claude(), tell=lambda *a, **k: None)
    assert at.get("task-1")["status"] == "done"


@pytest.mark.asyncio
async def test_tools(home, monkeypatch) -> None:
    monkeypatch.setattr(at, "start", lambda goal: {"id": "task-x"})
    tools = TaskTools()
    said = await TaskTools.start_task(tools, None, goal="research something useful")  # type: ignore[arg-type]
    assert "background" in said["say"]
    assert "No background tasks" in (await TaskTools.task_status(tools, None))["say"]  # type: ignore[arg-type]
    at.save(
        {
            "id": "task-2",
            "goal": "g",
            "title": "Email",
            "status": "waiting",
            "created": 2.0,
            "steps": [
                {
                    "title": "Draft",
                    "kind": "write",
                    "detail": "",
                    "status": "done",
                    "output": "Hi dad",
                },
                {
                    "title": "Send the email",
                    "kind": "consequential",
                    "detail": "to dad",
                    "status": "waiting",
                    "output": "",
                },
            ],
        }
    )
    out = await TaskTools.approve_task(tools, None)  # type: ignore[arg-type]
    assert out["prepared"] == "Hi dad" and "send the email" in out["say"]
    assert at.get("task-2")["status"] == "done"
