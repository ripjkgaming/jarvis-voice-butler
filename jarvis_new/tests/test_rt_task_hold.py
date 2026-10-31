"""A call must stay open while a background task started in it is still working."""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import agent
import agent_tasks


def _tasks(monkeypatch, rows):
    monkeypatch.setattr(agent_tasks, "all_tasks", lambda: rows)


def _row(status, age_s):
    return {"id": "t", "status": status, "created": str(time.time() - age_s)}


def test_running_task_holds_call_open(monkeypatch):
    _tasks(monkeypatch, [_row("running", 30)])
    assert agent._task_work_pending(SimpleNamespace())


def test_planning_task_holds_call_open(monkeypatch):
    _tasks(monkeypatch, [_row("planning", 5)])
    assert agent._task_work_pending(SimpleNamespace())


def test_finished_or_waiting_task_does_not_hold(monkeypatch):
    _tasks(monkeypatch, [_row("done", 5), _row("waiting", 5), _row("failed", 5)])
    assert not agent._task_work_pending(SimpleNamespace())


def test_stale_running_task_cannot_hold_forever(monkeypatch):
    _tasks(monkeypatch, [_row("running", 3600)])
    assert not agent._task_work_pending(SimpleNamespace())
