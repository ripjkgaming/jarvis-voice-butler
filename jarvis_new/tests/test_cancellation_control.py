import pytest

from cancellation_control import classify_control, resolve_task, task_receipt


@pytest.mark.parametrize("text", ["stop", "stop talking", "be quiet"])
def test_speech_stop_never_selects_task(text):
    assert classify_control(text).action == "speech"


@pytest.mark.parametrize(
    "text",
    [
        "stop playback",
        "what about weather",
        "cancel my meeting",
        "the message says cancel task",
    ],
)
def test_noncontrol_does_not_cancel_work(text):
    assert classify_control(text) is None


def test_explicit_task_control():
    command = classify_control("cancel task task-123")
    assert (command.action, command.target) == ("task", "task-123")
    assert classify_control("cancel this tool").action == "tool"


def test_ambiguous_target_selects_nothing():
    rows = [{"id": "a", "status": "running"}, {"id": "b", "status": "waiting"}]
    assert resolve_task(rows, "") is None
    assert resolve_task(rows, "b")["id"] == "b"
    assert resolve_task(rows, "unknown") is None


def test_receipt_preserves_effect_uncertainty():
    pending = task_receipt({"id": "a", "status": "cancelling", "title": "Task"})
    assert pending["settled"] is False
    assert "requested" in pending["say"].lower()
    done = task_receipt({"id": "a", "status": "cancelled", "title": "Task"})
    assert done["settled"] is True
    assert "undone" in done["say"]
