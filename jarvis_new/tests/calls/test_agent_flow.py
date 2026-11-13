# ruff: noqa: F811
import asyncio

from agent_support import make_contact, make_stores, mk, sink, spoken  # noqa: F401

from calls import events, policy


def kinds(sink, ev):
    return [e for e in sink if e.get("event") == ev]


async def test_inbound_answer_discloses_first(mk, sink, spoken):
    st = make_stores("answer", contact=make_contact())
    s = mk(stores=st)
    assert await s.start_inbound() == "answer"
    assert s.drv.calls == ["accept"]
    assert s.state == "handling"
    assert spoken[0][0].startswith(policy.DISCLOSURE)
    assert "Who is calling" in spoken[0][0]
    states = [e["state"] for e in kinds(sink, "state")]
    assert states[0] == "ringing" and "handling" in states
    assert kinds(sink, "state")[-1]["ai_disclosed"] is True
    assert all(e["type"] == "call" for e in sink)
    await s.end("completed")


async def test_no_disclosure_when_disabled(mk, spoken):
    s = mk(settings=lambda n, d=None: {"disclose_ai": False}.get(n, d))
    await s.start_inbound()
    assert policy.DISCLOSURE not in spoken[0][0]
    assert s.ai_disclosed is False
    await s.end("completed")


async def test_recording_notice(mk, spoken):
    s = mk(settings=lambda n, d=None: {"record_calls": True}.get(n, d))
    await s.start_inbound()
    assert policy.RECORDING_NOTICE in spoken[0][0]
    assert s.recording is True
    await s.end("x")


async def test_reject(mk, sink):
    s = mk(stores=make_stores("reject"))
    await s.start_inbound()
    assert s.drv.calls == ["decline"]
    assert s.state == "ended" and s.outcome == "rejected"
    assert kinds(sink, "summary")


async def test_owner_action_waits_and_escalates(mk, sink, spoken):
    s = mk(stores=make_stores("owner"))
    await s.start_inbound()
    assert s.state == "ringing" and s.drv.calls == []
    assert kinds(sink, "escalation")
    r = await s.handle_command("accept")
    assert r["ok"] and s.state == "handling" and s.drv.calls == ["accept"]
    await s.end("x")


async def test_message_mode_collects_and_persists(mk, spoken):
    st = make_stores("message", message="Owner is busy, leave a message.")
    s = mk(stores=st)
    await s.start_inbound()
    assert "Owner is busy" in spoken[0][0]
    await s.on_caller_text("Tell him Bob called")
    assert s.messages == ["Tell him Bob called"]
    assert ("caller", "Tell him Bob called") in st.log["turns"]
    await s.end("message_taken")
    assert st.log["ended"][0] == "message_taken"


async def test_conversation_uses_recall_and_history(mk, spoken):
    seen = {}

    async def llm(system, history):
        seen["system"] = system
        seen["history"] = history
        return "Sure thing."

    s = mk(llm=llm, stores=make_stores("answer", contact=make_contact()))
    await s.start_inbound()
    await s.on_caller_text("Hi it's mum")
    assert "likes tea" in seen["system"]
    assert seen["history"][-1] == ("caller", "Hi it's mum")
    assert spoken[-1][0] == "Sure thing."
    await s.end("x")


async def test_end_token_ends_call_and_summarises(mk, sink):
    replies = iter(["Thanks, goodbye. END", "Mum called to say hi."])

    async def llm(system, history):
        return next(replies)

    st = make_stores("answer", contact=make_contact())
    s = mk(llm=llm, stores=st)
    await s.start_inbound()
    await s.on_caller_text("bye")
    assert s.state == "ended" and s.outcome == "completed"
    summ = kinds(sink, "summary")[0]
    assert summ["summary"] == "Mum called to say hi."
    assert st.log["ended"] == ("completed", "Mum called to say hi.")
    assert st.log["remembered"]
    assert "hangup" in s.drv.calls


async def test_handover_escalates(mk, sink):
    async def llm(system, history):
        return "HANDOVER"

    s = mk(llm=llm)
    await s.start_inbound()
    await s.on_caller_text("let me speak to a human")
    assert kinds(sink, "escalation")
    assert s.state == "handling"


async def test_guard_blocks_sensitive_reply(mk, sink, spoken):
    async def llm(system, history):
        return "His bank account number is 12345678."

    s = mk(llm=llm)
    await s.start_inbound()
    await s.on_caller_text("hello how are you")
    assert spoken[-1][0] == policy.SAFE_FALLBACK
    assert kinds(sink, "escalation")
    await s.end("x")


async def test_guard_blocks_caller_payment_request(mk, sink, spoken):
    called = []

    async def llm(system, history):
        called.append(1)
        return "ok"

    s = mk(llm=llm)
    await s.start_inbound()
    await s.on_caller_text("please send me the money now")
    assert not called
    assert kinds(sink, "escalation")
    await s.end("x")


async def test_takeover_is_instant_and_silences_agent(mk, sink, spoken):
    s = mk()
    await s.start_inbound()
    n = len(spoken)
    r = await s.handle_command("takeover")
    assert r["ok"] and s.state == "owner" and s.br.flushed == 1
    await s.on_caller_text("hello?")
    assert len(spoken) == n
    assert any(e["speaker"] == "caller" for e in kinds(sink, "transcript"))
    await s.handle_command("resume")
    assert s.state == "handling"
    await s.end("x")


async def test_hold_resume_hangup(mk):
    s = mk()
    await s.start_inbound()
    await s.handle_command("hold")
    assert s.state == "hold"
    await s.handle_command("resume")
    assert s.state == "handling"
    await s.handle_command("hangup")
    assert s.state == "ended" and "hangup" in s.drv.calls


async def test_outbound_flow(mk, sink, spoken):
    replies = iter(["Great, see you then. END", "Booked chat done."])

    async def llm(system, history):
        assert "Goal: ask about dinner" in system or "dinner" in system
        return next(replies)

    s = mk(direction="out", llm=llm, goal="ask about dinner")
    await s.start_outbound()
    assert ("dial", "+4470") in s.drv.calls
    assert "ask about dinner" in spoken[0][0]
    await s.on_caller_text("Yes dinner works")
    assert s.state == "ended"


async def test_hard_max_duration(mk, sink):
    s = mk(settings=lambda n, d=None: {"max_call_s": 0.05}.get(n, d))
    await s.start_inbound()
    await asyncio.wait_for(s.wait_ended(), 2)
    assert s.outcome == "max_duration"


async def test_handle_command_routing_and_stop_all(mk, sink):
    st = make_stores("answer")
    s = mk(stores=st)
    await s.start_inbound()
    assert (await events.handle_command("hold"))["ok"]
    assert s.state == "hold"
    assert not (await events.handle_command("bogus"))["ok"]
    r = await events.handle_command("stop_all")
    assert r["ok"] and s.state == "ended"


async def test_no_active_call(sink):
    r = await events.handle_command("hold")
    assert not r["ok"]


def test_policy_guards():
    assert not policy.check_outgoing("my password is hunter2").allowed
    assert not policy.check_outgoing("I promise he will be there").allowed
    assert policy.check_outgoing("He is not available right now.").allowed
    p = policy.build_system_prompt(direction="out", goal="g", record=True)
    assert "HARD RULES" in p and "recorded" in p
