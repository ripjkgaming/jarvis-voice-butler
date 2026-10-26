"""No models/processes: cancellation races use blocked fake backends."""

import json
import threading

import pytest

import agent_tasks as at


@pytest.fixture
def task_home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(at, "_hud", lambda *a: None)
    monkeypatch.setattr(at, "_tell", lambda *a, **k: None)
    return tmp_path


def seed(status="planning"):
    task = {
        "id": "task-fixture",
        "goal": "prepare a synthetic report",
        "title": "Synthetic",
        "status": status,
        "created": 1.0,
        "steps": []
        if status == "planning"
        else [
            {
                "title": "Prepare",
                "kind": "think",
                "detail": "",
                "status": "pending",
                "output": "",
            }
        ],
    }
    at.save(task)
    return at.get(task["id"])


@pytest.mark.parametrize("phase", ["planning", "running"])
@pytest.mark.parametrize("failure", [False, True])
def test_cancel_during_backend_wins_over_late_result(
    task_home, monkeypatch, phase, failure
):
    task = seed(phase)
    started, release = threading.Event(), threading.Event()
    notices, errors = [], []

    def backend(*a, **k):
        started.set()
        assert release.wait(2)
        if failure:
            return "", "fake failure"
        return (
            json.dumps(
                {"title": "Late", "steps": [{"title": "Prepare", "kind": "think"}]}
            )
            if phase == "planning"
            else "late result"
        ), None

    def run():
        try:
            at.advance(task["id"], tell=lambda *a, **k: notices.append(a))
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(at, "_chatgpt", backend)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert started.wait(1)
        receipt = at.cancel(task["id"])
        assert receipt["status"] == "cancelling"
        assert at.approve(task["id"], background=False) is None
        release.set()
        worker.join(2)
        assert not worker.is_alive()
        assert not errors
        final = at.get(task["id"])
        assert final["status"] == "cancelled"
        assert final["settled_at"] >= final["cancel_requested_at"]
        assert not any(s.get("output") == "late result" for s in final["steps"])
        assert not notices
        assert not (at.tasks_dir() / f"{task['id']}.md").exists()
    finally:
        release.set()
        worker.join(2)


@pytest.mark.parametrize("terminal", ["cancelled", "done", "failed"])
def test_stale_snapshot_cannot_resurrect_terminal(task_home, terminal):
    task = seed()
    stale = dict(task)
    task["status"] = terminal
    assert at.save(task)
    stale["status"] = "running"
    assert not at.save(stale)
    assert at.get(task["id"])["status"] == terminal


def test_stale_revision_cannot_overwrite_newer_nonterminal(task_home):
    task = seed()
    stale = dict(task)
    task["title"] = "Newer"
    assert at.save(task)
    stale["title"] = "Older"
    assert not at.save(stale)
    assert at.get(task["id"])["title"] == "Newer"


def test_waiting_cancel_revokes_approval_and_keeps_completed_work(task_home):
    task = seed("running")
    task["status"] = "waiting"
    task["steps"][0].update(status="done", output="prepared result")
    task["steps"].append(
        {
            "title": "Send",
            "kind": "consequential",
            "status": "waiting",
            "approved": True,
            "output": "",
        }
    )
    assert at.save(task)
    cancelled = at.cancel(task["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["steps"][0]["output"] == "prepared result"
    assert not cancelled["steps"][1].get("approved")
    assert at.approve(task["id"], background=False) is None
    assert at.cancel(task["id"]) is None


def test_resume_does_not_replay_cancelling_task(task_home, monkeypatch):
    task = seed()
    task["status"] = "cancelling"
    assert at.save(task)
    monkeypatch.setattr(at, "advance", lambda *a: pytest.fail("cancel replayed"))
    assert at.resume_all() == []


def test_persistence_failure_does_not_dispatch(task_home, monkeypatch):
    monkeypatch.setattr(
        at.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk"))
    )
    monkeypatch.setattr(
        at.threading, "Thread", lambda *a, **k: pytest.fail("started after failure")
    )
    with pytest.raises(OSError, match="disk"):
        at.start("prepare synthetic report")


def test_corrupt_record_is_never_overwritten(task_home):
    task = seed()
    path = at._path(task["id"])
    path.write_text("corrupt fixture")
    with pytest.raises(ValueError):
        at.save(task)
    assert path.read_text() == "corrupt fixture"


def test_cancel_after_final_step_commit_prevents_done_report(task_home, monkeypatch):
    task = seed("running")
    monkeypatch.setattr(at, "_chatgpt", lambda *a, **k: ("prepared result", None))
    notices = []

    def hud(snapshot):
        if snapshot["steps"][0]["status"] == "done":
            at.cancel(task["id"])

    monkeypatch.setattr(at, "_hud", hud)
    result = at.advance(task["id"], tell=lambda *a, **k: notices.append(a))
    assert result["status"] == "cancelled"
    assert result["steps"][0]["output"] == "prepared result"
    assert not notices
    assert not (at.tasks_dir() / f"{task['id']}.md").exists()


def test_cancellation_before_approve_commit_wins(task_home, monkeypatch):
    task = seed("waiting")
    task["steps"][0].update(status="waiting", kind="consequential")
    at.save(task)
    original = at.save

    def cancel_before_save(snapshot):
        at.cancel(task["id"])
        return original(snapshot)

    monkeypatch.setattr(at, "save", cancel_before_save)
    assert at.approve(task["id"], background=False) is None
    assert at.get(task["id"])["status"] == "cancelled"


def test_duplicate_executor_does_not_dispatch(task_home, monkeypatch):
    task = seed("running")
    with at._execution_lock(task["id"]) as acquired:
        assert acquired
        monkeypatch.setattr(
            at, "_chatgpt", lambda *a, **k: pytest.fail("duplicate dispatch")
        )
        assert at.advance(task["id"])["status"] == "running"


def test_redirect_is_new_task_with_no_approval_and_replay_is_idempotent(
    task_home, monkeypatch
):
    task = seed("cancelled")
    monkeypatch.setattr(
        at.threading,
        "Thread",
        lambda *a, **k: type("T", (), {"start": lambda self: None})(),
    )
    replacement = at.redirect(task["id"], "write a new synthetic report", "turn-new")
    assert replacement["id"] != task["id"]
    assert replacement["supersedes"] == task["id"]
    assert replacement["steps"] == []
    replay = at.redirect(task["id"], "write a new synthetic report", "turn-new")
    assert replay["id"] == replacement["id"]
    assert len(at.all_tasks()) == 2
    with pytest.raises(ValueError, match="already has"):
        at.redirect(task["id"], "write a different report", "another-turn")


def test_redirect_waits_for_old_work_to_stop(task_home):
    task = seed("cancelling")
    with pytest.raises(ValueError, match="finish stopping"):
        at.redirect(task["id"], "prepare a replacement report", "new-turn")
    assert len(at.all_tasks()) == 1


def test_failed_redirect_registration_leaves_pending_unresumable(
    task_home, monkeypatch
):
    task = seed("cancelled")

    def fail(record):
        raise OSError("mock register failure")

    with pytest.raises(OSError, match="register failure"):
        at.redirect(
            task["id"], "prepare replacement report", "new-turn", on_created=fail
        )
    assert at.get(task["id"])["status"] == "cancelled"
    monkeypatch.setattr(
        at, "advance", lambda *a: pytest.fail("partial redirect replayed")
    )
    assert at.resume_all() == []


def test_missing_executor_lease_does_not_prove_orphan_stopped(task_home, monkeypatch):
    task = seed("running")
    task["executor_started"] = True
    at.save(task)
    monkeypatch.setattr(at, "_chatgpt", lambda *a, **k: pytest.fail("orphan replay"))
    assert at.resume_all() == []
    result = at.cancel(task["id"])
    assert result["status"] == "cancelling" and not result.get("settled_at")
    assert at.advance(task["id"])["status"] == "cancelling"


def test_known_queued_task_cancels_without_backend(task_home, monkeypatch):
    task = seed()
    task["executor_started"] = False
    at.save(task)
    monkeypatch.setattr(at, "_chatgpt", lambda *a, **k: pytest.fail("queued dispatch"))
    assert at.cancel(task["id"])["status"] == "cancelled"
