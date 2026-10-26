"""Exercise the actual tool wrapper under interrupted side effects."""

import asyncio
from types import SimpleNamespace


async def test_tool_wrapper_drains_mutation_and_records_result_once(
    monkeypatch, tmp_path
):
    import agent
    import handoff_recovery
    import phone_session
    from latency import TRACKER

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(phone_session, "private", lambda: False)
    monkeypatch.setattr(phone_session, "guest_required", lambda: False)
    monkeypatch.setattr(phone_session, "private_tool_error", lambda _: None)
    remembered = []
    monkeypatch.setattr(
        handoff_recovery, "remember", lambda context, result: remembered.append(result)
    )
    entered, release = asyncio.Event(), asyncio.Event()
    mutations = []

    async def send(context):
        entered.set()
        await release.wait()
        mutations.append("sent")
        return "sent"

    tool = SimpleNamespace(id="gmail_send", _func=send)
    agent._wrap_tools_with_timing([tool])
    before = TRACKER.pending
    task = asyncio.create_task(tool._func(None))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    assert await task == "sent"
    assert mutations == ["sent"]
    assert remembered == ["sent"]
    assert TRACKER.pending == before
