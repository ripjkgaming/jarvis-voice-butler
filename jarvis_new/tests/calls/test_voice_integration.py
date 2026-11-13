"""Voice tools against the real W1 stores (tmp dir)."""

import time

import pytest

from calls import voice_commands as vc

pytest.importorskip("calls.store")


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_CALLS_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_SCHEDULED_TASKS", "1")
    monkeypatch.delenv("JARVIS_GOODNIGHT_V2", raising=False)
    monkeypatch.setattr(vc, "_guard", lambda: None)
    from calls import store

    if hasattr(store, "reset"):
        store.reset()
    elif hasattr(store, "close"):
        store.close()


async def test_contacts_and_rules_roundtrip():
    from calls import calllog, rules

    t = vc.CallTools()
    r = await vc.handle_utterance("Mum's number is 9123 4567")
    assert "+6591234567" in r["say"]
    await vc.handle_utterance("mark Mum as family")
    assert "family" in (await t.find_contact(None, name="mum"))["say"]
    await t.set_auto_answer(
        None, request="for the next 2 hours answer all calls and say I'm in a meeting"
    )
    assert len(rules.active_rules(time.time())) == 1
    assert "meeting" in (await t.list_call_rules(None))["say"]
    await t.cancel_auto_answer(None)
    assert rules.active_rules(time.time()) == []
    kinds = [a["kind"] for a in calllog.list_audit(50)]
    assert "rule_set_voice" in kinds and "rule_cancel_voice" in kinds


async def test_schedule_call_real_contact():
    t = vc.CallTools()
    await t.add_contact(None, name="Dad", number="91234568")
    r = await t.schedule_call(None, contact="Dad", when="in 5 minutes", goal="lunch")
    assert r["job_id"].startswith("call-")
