import memory
from calls import calllog, contacts, rules
from calls import memory_bridge as mb


def mk():
    return contacts.add_contact(
        "Mum",
        "91234567",
        relationship="mother",
        trust="family",
        notes="Prefers calls after 6pm",
        policy={"greeting": "Hi Mum", "allow_topics": ["dinner"]},
    )


def test_remember_and_recall_facts_redacted(calls_env):
    c = mk()
    assert mb.remember_person(c.id, "Likes durian")
    assert mb.remember_person(c.id, "Likes durian")  # dedup
    assert mb.remember_person(c.id, "wifi password is hunter2abc")
    facts = mb.facts_for(c.id)
    assert facts.count("Likes durian") == 1
    assert not any("hunter2abc" in f for f in facts)
    note = (memory.threads_dir() / "Calls - Mum.md").read_text()
    assert "Likes durian" in note and "hunter2abc" not in note


def test_remember_rejects_unknown_contact_or_empty():
    c = mk()
    assert not mb.remember_person(999, "x")
    assert not mb.remember_person(c.id, "   ")


def test_recall_includes_last_three_summaries_only():
    c = mk()
    for i in range(5):
        cid = calllog.start_call("in", c.number, c.id, "", ts=1000.0 + i)
        calllog.end_call(cid, "completed", f"summary-{i}", ts=2000.0 + i)
    cid = calllog.start_call("in", c.number, c.id, "")  # open call: no summary
    out = mb.recall_for_call(c)
    assert all(f"summary-{i}" in out for i in (4, 3, 2))
    assert "summary-1" not in out and "summary-0" not in out
    assert "Prefers calls after 6pm" in out and "Hi Mum" in out and "dinner" in out
    assert cid


def test_recall_standing_rule_instructions():
    c = mk()
    rules.add_rule("message_only", message="Owner in exam until Friday")
    rules.add_rule("reject", message="secret reject text")
    out = mb.recall_for_call(c)
    assert "Owner in exam until Friday" in out
    assert "secret reject text" not in out


def test_recall_unknown_caller():
    rules.add_rule("answer_unknown", message="Take a message")
    out = mb.recall_for_call(None)
    assert out.startswith("Unknown caller") and "Take a message" in out


def test_recall_redacts_summary_and_is_capped():
    c = mk()
    cid = calllog.start_call("in", c.number, c.id, "")
    calllog.end_call(cid, "done", "token is abc123supersecret " + "x " * 2000)
    out = mb.recall_for_call(c)
    assert "abc123supersecret" not in out and len(out) <= mb.MAX_RECALL_CHARS


def test_write_call_summary_to_threads_redacted():
    c = mk()
    cid = calllog.start_call("in", c.number, c.id, "", ts=1_800_000_000.0)
    assert not mb.write_call_summary(cid)  # not ended
    calllog.end_call(cid, "completed", "Chat about dinner, pin: 4821")
    assert mb.write_call_summary(cid)
    text = (memory.threads_dir() / "Calls - Mum.md").read_text()
    assert "dinner" in text and "4821" not in text and text.startswith("# Calls - Mum")
    assert not mb.write_call_summary(999)


def test_vault_disabled_skips_write(monkeypatch):
    monkeypatch.setenv(memory.SWITCH_ENV, "0")
    c = mk()
    cid = calllog.start_call("in", c.number, c.id, "")
    calllog.end_call(cid, "done", "s")
    assert not mb.write_call_summary(cid)
    assert mb.remember_person(c.id, "fact")  # still stored in db
    assert not memory.threads_dir().exists()


def test_unknown_number_summary_uses_number():
    cid = calllog.start_call("in", "81234567", None, "")
    calllog.end_call(cid, "msg", "left message")
    assert mb.write_call_summary(cid)
    assert any("6581234567" in p.name for p in memory.threads_dir().iterdir())
