import pytest

from system.computer_use import _worker, task_model


@pytest.mark.parametrize(
    "task", ["Open Files", "Find the downloads folder", "Type hello in the draft"]
)
def test_simple_tasks_use_luna(task):
    assert task_model(task) == "gpt-6-luna"


@pytest.mark.parametrize(
    "task",
    [
        "Compare the two reports and draft a summary",
        "Debug the failing build",
        "Refactor the project",
        "Open Files then find the report and then compare it with the previous report",
        "x" * 401,
    ],
)
def test_complex_tasks_use_sol(task):
    assert task_model(task) == "gpt-6.1-sol"


def test_worker_routes_task_at_medium(monkeypatch):
    monkeypatch.delenv("JARVIS_COMPUTER_USE_MODEL", raising=False)
    monkeypatch.delenv("JARVIS_COMPUTER_USE_EFFORT", raising=False)
    worker = _worker("Compare the two reports")
    assert worker.model == "gpt-6.1-sol"
    assert worker.effort == "medium"


def test_explicit_overrides_win(monkeypatch):
    monkeypatch.setenv("JARVIS_COMPUTER_USE_MODEL", "gpt-6-luna")
    monkeypatch.setenv("JARVIS_COMPUTER_USE_EFFORT", "low")
    worker = _worker("Compare the two reports")
    assert worker.model == "gpt-6-luna"
    assert worker.effort == "low"
