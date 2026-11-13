"""Hermetic Pass 4 recovery/approval safeguards; no external tools run."""

import time

import agent_tasks as tasks
from power_foundation.recovery import reconcile


def test_atomic_bounded_checkpoint_and_unique_attempts(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_POWER_TASK_CHECKPOINTS", "1")
    state = {
        "id": "task-attempts",
        "status": "running",
        "created": 1,
        "executor_started": False,
        "goal": "SECRET",
        "steps": [
            {
                "kind": "write",
                "title": "private",
                "detail": "PRIVATE",
                "status": "pending",
            }
        ],
    }
    assert tasks.save(state)
    for _ in range(55):
        assert tasks.save(state)
    read = tasks.get(state["id"])
    assert len(read["checkpoint_history"]) == 48
    assert read["checkpoint_history"][-1]["revision"] == read["revision"]
    assert "SECRET" not in str(read["checkpoint_history"])
    assert "PRIVATE" not in str(read["checkpoint_history"])
    assert not tasks.save({**state, "revision": 1})


def test_expired_and_changed_approval_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    base = {
        "id": "task-ticket",
        "status": "waiting",
        "created": 1,
        "executor_started": False,
        "steps": [
            {
                "kind": "consequential",
                "title": "send",
                "detail": "draft",
                "status": "waiting",
                "approval_request_id": "request-1",
                "approval_fingerprint": tasks._step_fingerprint(
                    {"kind": "consequential", "title": "send", "detail": "draft"}
                ),
                "approval_expires_at": time.time() + 100,
            }
        ],
    }
    assert tasks.save(base)
    tampered = tasks.get("task-ticket")
    tampered["steps"][0]["detail"] = "different destination"
    assert tasks.save(tampered)
    assert tasks.approve("task-ticket", background=False) is None
    assert tasks.get("task-ticket")["status"] == "waiting"
    expired = tasks.get("task-ticket")
    expired["steps"][0]["detail"] = "draft"
    expired["steps"][0]["approval_expires_at"] = time.time() - 1
    assert tasks.save(expired)
    assert tasks.approve("task-ticket", background=False) is None


def test_crash_before_result_requires_review_no_replay(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_POWER_TASK_CHECKPOINTS", "1")
    task = {
        "id": "task-interrupted",
        "status": "running",
        "created": 1,
        "executor_started": True,
        "steps": [
            {"kind": "research", "status": "running", "attempt_id": "old-attempt"}
        ],
    }
    assert tasks.save(task)
    assert reconcile()[0]["status"] == "recovery_required"
    assert tasks.get("task-interrupted")["steps"][0]["attempt_id"] == "old-attempt"
    assert reconcile() == []
    assert tasks.advance("task-interrupted") == tasks.get("task-interrupted")
