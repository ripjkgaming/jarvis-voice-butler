"""Injected metadata store and fake computer service; no effects."""

import importlib.util
import os
import threading

import pytest

import agent_tasks as at
from cancellation_adapters import TaskCancellationAdapter


@pytest.fixture
def adapter_home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(at, "_hud", lambda *a: None)
    monkeypatch.setattr(at, "_tell", lambda *a, **k: None)
    return tmp_path


@pytest.fixture
def registry(adapter_home):
    path = os.environ.get("JARVIS_SHARED_JOBS_TEST_MODULE")
    if not path:
        pytest.skip("shared jobs v1 module must be explicitly supplied")
    spec = importlib.util.spec_from_file_location("shared_jobs_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Registry(adapter_home / "jobs")


def seed(status="running"):
    task = {
        "id": "task-example",
        "goal": "synthetic",
        "title": "Synthetic",
        "status": status,
        "steps": [
            {
                "title": "Prepare",
                "kind": "think",
                "detail": "",
                "status": "pending",
                "output": "",
            }
        ],
        "created": 1,
    }
    at.save(task)
    return at.get(task["id"])


def test_monitor_and_unmapped_ids_never_reach_task_cancel(
    adapter_home, registry, monkeypatch
):
    monitor = registry.register(
        "build-monitor", "observer", "monitor_observation", "Build", {}
    )
    adapter = TaskCancellationAdapter(registry)
    monkeypatch.setattr(
        at, "cancel", lambda *a: pytest.fail("monitor gained task authority")
    )
    assert adapter.cancel(monitor["id"]) is None
    assert adapter.cancel("job-" + "f" * 32) is None
    assert adapter.cancel("build-example") is None
    assert adapter.cancel("12345") is None
    assert registry.get(monitor["id"])["lifecycle"] == "running"


def test_canonical_mapping_and_settlement_are_distinct(
    adapter_home, registry, monkeypatch
):
    task = seed()
    adapter = TaskCancellationAdapter(registry)
    job = adapter.register(task)
    started, release = threading.Event(), threading.Event()
    results = []

    def backend(*a, **k):
        started.set()
        assert release.wait(2)
        return "late preparation", None

    monkeypatch.setattr(at, "_chatgpt", backend)
    worker = threading.Thread(target=lambda: results.append(at.advance(task["id"])))
    worker.start()
    try:
        assert started.wait(1)
        receipt = adapter.cancel(job["id"])
        assert receipt["accepted"] and not receipt["settled"]
        assert receipt["lifecycle"] == "cancellation_requested"
        assert registry.get(job["id"])["outcome"] is None
        release.set()
        worker.join(2)
        assert not worker.is_alive()
        assert results[0]["status"] == "cancelled"
        receipt = adapter.receipt(at.get(task["id"]))
        assert receipt["settled"]
        assert receipt["job_outcome"] == "cancelled"
        assert receipt["job_id"] == job["id"]
        assert len(registry.list("agent-tasks")) == 1
        assert registry.get(job["id"])["payload"] == {}
    finally:
        release.set()
        worker.join(2)


def test_completed_task_does_not_claim_cancelled(adapter_home, registry):
    task = seed("done")
    result = TaskCancellationAdapter(registry).cancel(task["id"])
    assert not result["accepted"]
    assert result["state"] == "done" and result["job_outcome"] == "succeeded"


def test_explicit_tool_owner_only(adapter_home):
    class Service:
        calls = 0

        def status(self):
            return {"id": "computer-fixture", "status": "running"}

        def cancel(self):
            self.calls += 1
            return {"status": "cancelling", "say": "Stopping after input settles."}

    service = Service()
    adapter = TaskCancellationAdapter()
    assert adapter.cancel_tool("wrong-id", service) is None
    assert service.calls == 0
    result = adapter.cancel_tool("computer-fixture", service)
    assert service.calls == 1 and result["accepted"] and not result["settled"]


def test_terminal_computer_status_waits_for_cleanup_receipt(adapter_home):
    class Service:
        settled = False

        def status(self):
            return {"id": "computer-fixture", "status": "cancelled"}

        def cancellation_settled(self):
            return self.settled

    service = Service()
    adapter = TaskCancellationAdapter()
    assert not adapter.cancel_tool("computer-fixture", service)["settled"]
    service.settled = True
    assert adapter.cancel_tool("computer-fixture", service)["settled"]


def test_registration_failure_prevents_native_backend_dispatch(
    adapter_home, monkeypatch
):
    def refuse(task):
        raise OSError("mock registry unavailable")

    monkeypatch.setattr(
        at, "_chatgpt", lambda *a, **k: pytest.fail("backend dispatched")
    )
    with pytest.raises(OSError, match="unavailable"):
        at.start("prepare synthetic report", background=False, on_created=refuse)
    assert at.all_tasks()[0]["status"] == "failed"
