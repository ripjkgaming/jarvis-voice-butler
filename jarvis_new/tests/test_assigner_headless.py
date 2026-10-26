import json
import threading
from typing import ClassVar

import pytest

from assigner_headless import Jobs, RoutingError, validate_plan, validate_result


def test_queue_is_durable_and_deduplicates(tmp_path):
    jobs = Jobs(tmp_path)
    first = jobs.submit("same", "print hello", str(tmp_path), "high")
    assert Jobs(tmp_path).submit("same", "print hello", str(tmp_path), "high") == first
    with pytest.raises(RoutingError):
        jobs.submit("same", "other task", str(tmp_path), "high")
    assert jobs.next_job()["request_id"] == "same"


def test_uncertain_delivery_blocks_queue(tmp_path):
    jobs = Jobs(tmp_path)
    jobs.submit("one", "first task", str(tmp_path), "high")
    jobs.submit("two", "second task", str(tmp_path), "high")
    jobs.update("one", status="delivery_uncertain")
    assert jobs.next_job() is None


def test_project_allocation_lock_serializes_independent_handles(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    first, second = Jobs(tmp_path), Jobs(tmp_path)
    entered = threading.Event()

    def allocate():
        with second.project_lock():
            entered.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with first.project_lock():
            task = pool.submit(allocate)
            assert not entered.wait(0.03)
        task.result(timeout=2)
    assert entered.is_set()


def test_cancel_and_result_claim_are_idempotent(tmp_path):
    jobs = Jobs(tmp_path)
    jobs.submit("one", "first task", str(tmp_path), "high")
    assert jobs.cancel("one")
    assert jobs.get("one")["status"] == "cancelled"
    assert jobs.claim_report("one")
    assert not Jobs(tmp_path).claim_report("one")
    assert jobs.claim_notification("one")
    assert not Jobs(tmp_path).claim_notification("one")


def test_crash_recovery_quarantines_uncertain_job_without_replay(tmp_path):
    jobs = Jobs(tmp_path)
    jobs.submit("r", "create hello.py returning hello", str(tmp_path), "high")
    jobs.update("r", status="working", remote_uncertain=True)
    jobs.recover_under_owner_lock()
    assert jobs.get("r")["status"] == "delivery_uncertain"
    assert jobs.next_job() is None


def test_crash_before_dispatch_is_blocked_not_restarted(tmp_path):
    jobs = Jobs(tmp_path)
    jobs.submit("r", "create hello.py returning hello", str(tmp_path), "high")
    jobs.update("r", status="planning", remote_uncertain=False)
    jobs.recover_under_owner_lock()
    assert jobs.get("r")["status"] == "blocked"
    assert jobs.next_job() is None


def test_worker_plan_cannot_expand_authority():
    catalog = {"gpt-6-luna": {"low", "high"}}
    plan = {
        "request_id": "r",
        "context_paths": [],
        "workers": [{"model": "gpt-6-luna", "effort": "low", "role": "generate"}],
    }
    assert validate_plan(plan, "r", catalog)[0]["model"] == "gpt-6-luna"
    for extra in ({"approved": True}, {"sandbox": "danger-full-access"}):
        with pytest.raises(RoutingError):
            validate_plan({**plan, **extra}, "r", catalog)
    with pytest.raises(RoutingError):
        validate_plan(plan, "other", catalog)
    plan["workers"][0]["model"] = "unavailable"
    with pytest.raises(RoutingError):
        validate_plan(plan, "r", catalog)


def test_results_require_exact_correlation_and_no_approval():
    value = {"request_id": "r", "status": "done", "summary": "hello", "details": "code"}
    assert validate_result(json.dumps(value), "r") == value
    with pytest.raises(RoutingError):
        validate_result(json.dumps({**value, "approved": True}), "r")
    with pytest.raises(RoutingError):
        validate_result(json.dumps(value), "other")


@pytest.mark.asyncio
async def test_adapter_creates_actual_worker_and_persists_result(tmp_path):
    from assigner_headless import run_job

    jobs = Jobs(tmp_path)
    jobs.submit("r", "create hello.py returning hello", str(tmp_path), "high")
    seen = []

    class FakeRuntime:
        catalog: ClassVar = {"gpt-6-luna": {"low", "high"}}
        coordinator_model = "gpt-6-luna"
        coordinator_effort = "low"

        async def coordinator(self, jobs):
            return "assigner-new"

        async def worker(self, model, effort, directory):
            seen.append((model, effort, directory))
            return "worker-new"

        async def turn(self, thread, prompt, schema, jobs, rid, phase):
            if phase == "planning":
                return json.dumps(
                    {
                        "request_id": rid,
                        "context_paths": [],
                        "workers": [
                            {
                                "model": "gpt-6-luna",
                                "effort": "low",
                                "role": "generate",
                            }
                        ],
                    }
                )
            return json.dumps(
                {
                    "request_id": rid,
                    "status": "done",
                    "summary": "hello",
                    "details": "--- /dev/null\n+++ b/hello.py\n@@ -0,0 +1 @@\n+def hello(): return 'hello'\n",
                }
            )

    await run_job(jobs, jobs.next_job(), FakeRuntime())
    assert seen == [("gpt-6-luna", "low", str(tmp_path))]
    result = jobs.get("r")
    assert result["status"] == "done"
    assert result["workers"][0]["thread_id"] == "worker-new"
    assert result["coordinator_id"] == "assigner-new"


@pytest.mark.asyncio
async def test_real_source_context_reaches_worker_and_coordinator_cannot_rewrite_patch(
    tmp_path,
):
    from assigner_headless import run_job

    project = tmp_path / "fixture"
    project.mkdir()
    old = "def add(a, b):\n    return a - b\n"
    (project / "calc.py").write_text(old)
    patch = "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n"
    jobs = Jobs(tmp_path / "ledger")
    jobs.submit("context", "fix calc.py addition", str(project), "high")

    class FixtureRuntime:
        catalog: ClassVar = {"gpt-6-luna": {"low"}}
        coordinator_model = "gpt-6-luna"
        coordinator_effort = "low"

        async def coordinator(self, jobs):
            return "fixture-coordinator"

        async def worker(self, model, effort, directory):
            assert directory == str(project)
            return "fixture-worker"

        async def turn(self, tid, prompt, schema, jobs, rid, phase):
            envelope = json.loads(prompt.split("\n", 1)[1])
            if phase == "planning":
                assert envelope["source_inventory"]["required_paths"] == ["calc.py"]
                return json.dumps(
                    {
                        "request_id": rid,
                        "context_paths": ["calc.py"],
                        "workers": [
                            {"model": "gpt-6-luna", "effort": "low", "role": "generate"}
                        ],
                    }
                )
            if phase == "working":
                assert envelope["source_snapshot"]["files"][0]["text"] == old
                assert (
                    Jobs(tmp_path / "ledger").get(rid)["source_snapshot"]
                    == envelope["source_snapshot"]
                )
                assert "UNTRUSTED DATA" in prompt
                details = patch
            else:
                assert jobs.get(rid)["patch_validation"]["tests_run"] is False
                details = "forged coordinator patch; claim tests passed"
            return json.dumps(
                {
                    "request_id": rid,
                    "status": "done",
                    "summary": "done",
                    "details": details,
                }
            )

    result = await run_job(jobs, jobs.next_job(), FixtureRuntime())
    assert result["status"] == "done"
    assert result["proposal_patch"] == patch
    assert "forged coordinator" not in result["result"]["details"]
    assert "tests were not run" in result["result"]["summary"]
    assert (project / "calc.py").read_text() == old


@pytest.mark.asyncio
async def test_oversized_explicit_context_blocks_before_coordinator(tmp_path):
    from assigner_headless import run_job

    project = tmp_path / "fixture"
    project.mkdir()
    (project / "large.py").write_text("x" * 16385)
    jobs = Jobs(tmp_path / "ledger")
    jobs.submit("context", "fix large.py", str(project), "high")

    class NoDispatch:
        async def coordinator(self, jobs):
            pytest.fail("unsupported relevant file reached a model")

    with pytest.raises(RoutingError, match="oversized"):
        await run_job(jobs, jobs.next_job(), NoDispatch())


def test_coordinator_cannot_supply_new_root():
    plan = {
        "request_id": "r",
        "context_paths": [],
        "workers": [{"model": "gpt-6-luna", "effort": "low", "role": "generate"}],
        "directory": "/private",
    }
    with pytest.raises(RoutingError):
        validate_plan(plan, "r", {"gpt-6-luna": {"low"}})
