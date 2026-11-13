# ruff: noqa: F811
"""Validates scenarios/*.json and replays the booking/safety ones on CallSession."""

import asyncio
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_support import make_stores, mk, sink, spoken  # noqa: F401

from calls.calendar_tools import CalendarTools, InMemoryCalendar

DIR = Path(__file__).parent / "scenarios"
FILES = sorted(DIR.glob("*.json"))
REQ = {"id", "title", "setup", "caller_turns", "expect"}
SETUP = {"calendar_seed", "contacts", "rules", "now"}
EXPECT = {
    "must_contain",
    "must_not_contain",
    "events",
    "booking",
    "escalation",
    "outcome",
}
SKIP_PREFIX = ("in_", "win_", "out_")


def test_enough_scenarios_and_required_ids():
    ids = {f.stem for f in FILES}
    assert len(FILES) >= 24
    assert {
        "book_free_slot", "book_clash_alternatives", "remind_in_calendar",
        "remind_not_in_calendar", "book_unknown_caller", "safety_payment_ask",
        "safety_prompt_injection", "safety_ai_disclosure_question",
    } <= ids  # fmt: skip


@pytest.mark.parametrize("f", FILES, ids=lambda f: f.stem)
def test_schema(f):
    d = json.loads(f.read_text())
    assert d.keys() >= REQ and d["id"] == f.stem
    assert d["setup"].keys() >= SETUP and d["expect"].keys() >= EXPECT
    dt.datetime.fromisoformat(d["setup"]["now"])


def _runnable(f):
    return not f.stem.startswith(SKIP_PREFIX)


@pytest.mark.parametrize("f", [f for f in FILES if _runnable(f)], ids=lambda f: f.stem)
async def test_replay(f, mk, spoken, sink):
    d = json.loads(f.read_text())
    setup, exp = d["setup"], d["expect"]
    now = dt.datetime.fromisoformat(setup["now"])
    cal = InMemoryCalendar(setup["calendar_seed"])
    base = len(cal)
    contact = None
    if setup["contacts"]:
        c = setup["contacts"][0]
        contact = SimpleNamespace(
            id=1, name=c["name"], number=c["number"], relationship=c["relationship"],
            notes="", trust=c["trust"], policy=c["policy"],
        )  # fmt: skip
    st = make_stores("answer", contact=contact)
    tools = CalendarTools(cal, clock=lambda: now)
    replies = list(d["llm_replies"])

    async def llm(system, history):
        if "Summarise" in system:
            return "summary"
        return replies.pop(0) if replies else "Okay."

    s = mk(stores=st, llm=llm, calendar=tools)
    await s.start_inbound()
    for t in d["caller_turns"]:
        await s.on_caller_text(t)
    await asyncio.sleep(0)
    said = " ".join(t for t, _ in spoken).lower()
    for m in exp["must_contain"]:
        assert m.lower() in said, (m, said)
    for m in exp["must_not_contain"]:
        assert m.lower() not in said, (m, said)
    assert (
        bool([e for e in sink if e.get("event") == "escalation"]) == exp["escalation"]
    )
    b = exp["booking"]
    assert len(cal) - base == b["calendar_events_added"]
    if b["status"] != "none":
        assert list(tools.proposals.values())[-1].status == b["status"]
    for ev in exp["events"]:
        if ev.startswith("audit:"):
            assert any(k == ev[6:] for k, _ in st.log["audit"]), ev
        if ev == "summary":
            await s.end("completed")
            assert [e for e in sink if e.get("event") == "summary"]
    if s.state != "ended":
        await s.end("completed")
