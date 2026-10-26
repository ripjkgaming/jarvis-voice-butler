"""Explicit schedules stay available independently of passive proactivity."""

import importlib

import bridge


def test_schedule_worker_runs_when_proactive_notifications_are_disabled(monkeypatch):
    started = []

    class Module:
        def __init__(self, name):
            self.name = name

        def start_thread(self):
            started.append(self.name)

    monkeypatch.setattr(importlib, "import_module", Module)
    monkeypatch.setenv("JARVIS_PROACTIVE_ENGINE", "0")
    monkeypatch.setenv("JARVIS_SCHEDULED_TASKS", "1")
    bridge.start_sidecar_threads()
    assert "scheduled_tasks" in started
    assert "proactive.engine" not in started


def test_main_assistant_exposes_scheduling_and_cancellation(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "synthetic")
    from agent import Assistant

    assistant = Assistant(browser=None, llm=None)
    ids = [tool.id for tool in assistant.tools]
    assert "schedule_task" in ids
    assert "scheduled_tasks" in ids
    assert len(ids) == len(set(ids))
