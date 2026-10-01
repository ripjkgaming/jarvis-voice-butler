"""Guard ownership follows mocked desktop workers, including cancellation."""

import asyncio
import contextvars
import gc
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from types import SimpleNamespace

import pytest

import action_guard
from system import audio_ctl, closer, core, school_tools


@pytest.fixture(params=["volume", "media", "close"])
def guarded_action(request, monkeypatch):
    """Replace every desktop boundary before invoking the public tool."""
    monkeypatch.setattr(core, "require_local", lambda: None)
    monkeypatch.setattr(core, "log_action", lambda *args: None)
    monkeypatch.setattr(school_tools, "loud_guard", lambda *args: None)
    tools = core.SystemTools()
    if request.param == "volume":
        module, attribute = audio_ctl, "volume"
        call = partial(core.SystemTools.set_volume, tools, object(), "up")
        route_tool, route_args = "volume_up", {}
    elif request.param == "media":
        module, attribute = audio_ctl, "media"
        call = partial(core.SystemTools.media_control, tools, object(), "next")
        route_tool, route_args = "media_next", {}
    else:
        module, attribute = closer, "close_target"
        call = partial(core.SystemTools.close_app, tools, object(), "example")
        route_tool, route_args = "close_app", {"name": "example"}

    def result(ok, say):
        if request.param == "close":
            return {"ok": ok, "say": say, "closed": "example"}
        return ok, say

    return SimpleNamespace(
        call=call,
        patch=lambda worker: monkeypatch.setattr(module, attribute, worker),
        claim=lambda source, **kwargs: action_guard.claim(
            route_tool, route_args, source, **kwargs
        ),
        request=(route_tool, route_args),
        result=result,
    )


@pytest.mark.parametrize("outcome", ["success", "failure", "exception"])
async def test_cancelled_action_retains_guard_until_worker_settles(
    guarded_action, monkeypatch, outcome
):
    action = guarded_action
    started, release, finished = (threading.Event() for _ in range(3))
    settled = asyncio.Event()
    loop = asyncio.get_running_loop()
    settlements, effects, errors = [], [], []
    context = contextvars.ContextVar("guarded-worker-context")
    context.set("request context")
    original_settle = action_guard.settle

    def record_settle(*args, **kwargs):
        original_settle(*args, **kwargs)
        settlements.append((args, kwargs))
        loop.call_soon_threadsafe(settled.set)

    def worker(*args):
        assert context.get() == "request context"
        started.set()
        try:
            assert release.wait(5), "test did not release the fake desktop worker"
            effects.append(args)
            if outcome == "exception":
                raise RuntimeError("fake desktop worker failed")
            return action.result(outcome == "success", "actual worker result")
        finally:
            finished.set()

    monkeypatch.setattr(action_guard, "settle", record_settle)
    action.patch(worker)
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, error: errors.append(error))
    pending = asyncio.create_task(action.call())
    try:
        assert await asyncio.to_thread(started.wait, 1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 0.2)
        assert not finished.is_set(), "caller cancellation waited for the worker"
        duplicate = action.claim("route")
        assert duplicate is not None, "a cancelled await released a live worker's guard"
        assert duplicate["ok"] is None
        assert settlements == []

        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
        await asyncio.wait_for(settled.wait(), 1)
        completed = action.claim("fast")
        if outcome == "success":
            assert completed is not None
            assert completed["ok"] is True
            assert completed["say"] == "actual worker result"
        else:
            assert completed is None, "a failed worker must allow a fresh attempt"
            # Release the new probe claim; it did not submit another worker.
            original_settle(*action.request, "fast", False)
        assert len(settlements) == 1
        assert len(effects) == 1
        # A later request from the originating source is still legitimate.
        action.patch(lambda *args: action.result(True, "later request"))
        assert (await action.call())["say"] == "later request"
        assert len(settlements) == 2
        await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)
        assert errors == [], "abandoned worker exceptions must be consumed"
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await asyncio.to_thread(finished.wait, 1)
        loop.set_exception_handler(previous_handler)


async def test_cancelled_queued_action_keeps_its_guard(guarded_action, monkeypatch):
    action = guarded_action
    loop = asyncio.get_running_loop()
    original_run = loop.run_in_executor
    release, finished = threading.Event(), threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    blocker = pool.submit(release.wait, 5)
    submitted = asyncio.Event()

    def run_in_executor(executor, function, *args):
        future = original_run(pool if executor is None else executor, function, *args)
        submitted.set()
        return future

    def worker(*args):
        finished.set()
        return action.result(True, "queued worker finished")

    action.patch(worker)
    monkeypatch.setattr(loop, "run_in_executor", run_in_executor)
    pending = asyncio.create_task(action.call())
    try:
        await asyncio.wait_for(submitted.wait(), 1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 0.2)
        assert not finished.is_set()
        duplicate = action.claim("route")
        assert duplicate is not None and duplicate["ok"] is None
        release.set()
        await original_run(None, blocker.result, 1)
        assert await original_run(None, finished.wait, 1)
        await original_run(pool, lambda: None)
        duplicate = action.claim("fast")
        assert duplicate is not None and duplicate["ok"] is True
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await original_run(None, pool.shutdown, True)


async def test_identical_workers_settle_their_own_claims(guarded_action):
    action = guarded_action
    starts = [threading.Event(), threading.Event()]
    releases = [threading.Event(), threading.Event()]
    lock = threading.Lock()
    calls = []

    def worker(*args):
        with lock:
            index = len(calls)
            calls.append(args)
        starts[index].set()
        assert releases[index].wait(5)
        return action.result(index == 1, f"worker {index}")

    action.patch(worker)
    first = asyncio.create_task(action.call())
    second = None
    try:
        assert await asyncio.to_thread(starts[0].wait, 1)
        second = asyncio.create_task(action.call())
        assert await asyncio.to_thread(starts[1].wait, 1)
        releases[1].set()
        assert (await asyncio.wait_for(second, 1))["say"] == "worker 1"
        duplicate = action.claim("route")
        assert duplicate is not None and duplicate["ok"] is None, (
            "the second worker settled the still-running first worker's claim"
        )
        releases[0].set()
        with pytest.raises(core.ToolError, match="worker 0"):
            await asyncio.wait_for(first, 1)
        duplicate = action.claim("fast")
        assert duplicate is not None and duplicate["ok"] is True
        assert duplicate["say"] == "worker 1"
    finally:
        for release in releases:
            release.set()
        await asyncio.gather(
            first, *([second] if second else []), return_exceptions=True
        )


async def test_executor_submission_failure_releases_guard(guarded_action, monkeypatch):
    def fail_submit(*args):
        raise RuntimeError("executor unavailable")

    monkeypatch.setattr(asyncio.get_running_loop(), "run_in_executor", fail_submit)
    with pytest.raises(RuntimeError, match="executor unavailable"):
        await guarded_action.call()
    assert guarded_action.claim("route") is None


async def test_executor_discards_queued_action_releases_guard(
    guarded_action, monkeypatch
):
    action = guarded_action
    loop = asyncio.get_running_loop()
    original_run = loop.run_in_executor
    release = threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    pool.submit(release.wait, 5)
    submitted = asyncio.Event()
    calls = []

    def run_in_executor(executor, function, *args):
        future = original_run(pool if executor is None else executor, function, *args)
        submitted.set()
        return future

    action.patch(lambda *args: calls.append(args))
    monkeypatch.setattr(loop, "run_in_executor", run_in_executor)
    pending = asyncio.create_task(action.call())
    try:
        await asyncio.wait_for(submitted.wait(), 1)
        pool.shutdown(wait=False, cancel_futures=True)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 1)
        assert calls == []
        assert action.claim("route") is None
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await original_run(None, pool.shutdown, True)


def test_owned_claim_survives_legacy_abandoned_work_timeout():
    lease = object()
    assert action_guard.claim("volume_up", {}, "model", now=0, lease=lease) is None
    late = action_guard.IN_FLIGHT_S + 1
    duplicate = action_guard.claim("volume_up", {}, "route", now=late)
    assert duplicate is not None and duplicate["ok"] is None
    action_guard.settle("volume_up", {}, "model", True, now=late, lease=lease)
    assert action_guard.claim("volume_up", {}, "fast", now=late + 1)
    assert (
        action_guard.claim(
            "volume_up", {}, "another", now=late + action_guard.WINDOW_S + 1
        )
        is None
    )


def test_settlement_cannot_release_another_workers_lease():
    lease = object()
    action_guard.claim("volume_up", {}, "model", lease=lease)
    action_guard.settle("volume_up", {}, "model", False)
    action_guard.settle("volume_up", {}, "model", False, lease=object())
    duplicate = action_guard.claim("volume_up", {}, "route")
    assert duplicate is not None and duplicate["ok"] is None
    action_guard.settle("volume_up", {}, "model", False, lease=lease)
    assert action_guard.claim("volume_up", {}, "fast") is None


def test_owned_settlement_cannot_release_a_legacy_claim():
    action_guard.claim("volume_up", {}, "model")
    action_guard.settle("volume_up", {}, "model", False, lease=object())
    duplicate = action_guard.claim("volume_up", {}, "route")
    assert duplicate is not None and duplicate["ok"] is None
