import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

import system.inbox as inbox
from latency import install_session_timing


@pytest.mark.asyncio
async def test_metadata_reads_are_bounded_preserve_order_and_verify_only_full_success(
    monkeypatch,
):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(inbox, "log_action", lambda *_: None)
    monkeypatch.setattr(inbox, "_gmail_access_token", lambda: "fake-token")
    active = peak = 0
    lock = threading.Lock()
    failures = set()

    def api(path, token, params):
        nonlocal active, peak
        if path == "/messages":
            return {"messages": [{"id": str(i)} for i in range(10)]}
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.005)
            key = path.rsplit("/", 1)[1]
            if key in failures:
                raise RuntimeError("missing metadata")
            return {
                "id": key,
                "payload": {"headers": [{"name": "Subject", "value": f"Mail {key}"}]},
            }
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(inbox, "_gmail_api", api)
    session = SimpleNamespace(on=lambda *_: None)
    logs = []
    observer = install_session_timing(session, lambda *line: logs.append(line))
    observer.user_state_changed("speaking")
    observer.user_state_changed("listening")
    context = SimpleNamespace(session=session)
    result = await inbox.InboxTools().gmail_inbox(context, n=10)
    assert 1 < peak <= 3
    assert result["say"].index("Mail 0") < result["say"].index("Mail 9")
    assert observer.summary()["vad_end_to_verified_workflow_s"]["n"] == 1
    observer.user_state_changed("speaking")
    observer.user_state_changed("listening")
    failures.add("2")
    await inbox.InboxTools().gmail_inbox(context, n=10)
    assert observer.summary()["vad_end_to_verified_workflow_s"]["n"] == 1


@pytest.mark.asyncio
async def test_read_pool_cancellation_keeps_running_thread_slots_until_they_finish(
    monkeypatch,
):
    started = threading.Event()
    release = threading.Event()
    calls = 0
    lock = threading.Lock()

    def api(*_):
        nonlocal calls
        with lock:
            calls += 1
            if calls == 3:
                started.set()
        release.wait(2)
        return {"id": "1"}

    monkeypatch.setattr(inbox, "_gmail_api", api)
    task = asyncio.create_task(
        inbox._gmail_metadata_batch("fake", [{"id": str(i)} for i in range(10)])
    )
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    following = asyncio.create_task(inbox._gmail_metadata_batch("fake", [{"id": "1"}]))
    try:
        await asyncio.sleep(0.01)
        assert calls == 3  # cancelled running reads still own all three slots
    finally:
        release.set()
        await asyncio.wait_for(following, 1)
