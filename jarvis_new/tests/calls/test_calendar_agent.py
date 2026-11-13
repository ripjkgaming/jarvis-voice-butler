# ruff: noqa: F811
import datetime as dt

from agent_support import make_contact, make_stores, mk, sink, spoken  # noqa: F401

from calls import calendar_hook
from calls.calendar_tools import TZ, CalendarTools, InMemoryCalendar

NOW = dt.datetime(2026, 11, 13, 10, 0, tzinfo=TZ)


def tools(seed=None):
    return CalendarTools(InMemoryCalendar(seed or []), clock=lambda: NOW)


def test_apply_tokens_strips_and_runs():
    t = tools()
    t.contact = make_contact("family")
    t.contact.policy = {"allow_topics": ["booking"]}
    reply = 'Sure. BOOK {"title":"Dinner","start":"2026-11-14T19:00","end":"2026-11-14T20:00","who":"Mum"}'
    clean, res = calendar_hook.apply_tokens(t, reply)
    assert "BOOK" not in clean and "{" not in clean
    assert "in the calendar" in res[0] and len(t.backend) == 1


def test_bad_json_is_safe():
    _clean, res = calendar_hook.apply_tokens(tools(), 'BOOK {"title":"x"}')
    assert "exact day" in res[0]


async def run(mk, reply, trust="family", seed=None, allow=("booking",)):
    c = make_contact(trust)
    c.policy = {"allow_topics": list(allow)}
    st = make_stores("answer", contact=c)
    t = tools(seed)
    seen = {}

    async def llm(system, history):
        if "Summarise" in system:
            return "sum"
        seen["system"] = system
        return reply

    s = mk(stores=st, llm=llm, calendar=t)
    await s.start_inbound()
    await s.on_caller_text("yes that is right, thank you")
    return s, t, st, seen


async def test_prompt_has_calendar_line(mk, spoken):
    _, _t, _, seen = await run(mk, "Okay.")
    assert "Calendar (owner's time Fri 2026-11-13 10:00" in seen["system"]
    assert "CALENDAR TOOLS" in seen["system"]


BOOK = 'BOOK {"title":"Dinner","start":"2026-11-14T19:00","end":"2026-11-14T20:00","who":"Mum"}'


async def test_book_family_auto(mk, spoken, sink):
    _s, t, st, _ = await run(mk, BOOK)
    assert len(t.backend) == 1
    assert "Done" in spoken[-1][0]
    assert any(k == "booking_confirmed" for k, _ in st.log["audit"])
    assert not [e for e in sink if e.get("event") == "escalation"]


async def test_book_unknown_escalates_and_audits(mk, spoken, sink):
    _s, t, st, _ = await run(mk, BOOK, trust="unknown")
    import asyncio

    await asyncio.sleep(0)
    assert len(t.backend) == 0
    assert "tentative hold" in spoken[-1][0]
    assert any(k == "booking_denied" for k, _ in st.log["audit"])
    assert [e for e in sink if e.get("event") == "escalation"]


async def test_book_no_policy_asks_owner(mk, spoken, sink):
    _s, t, _st, _ = await run(mk, BOOK, allow=())
    import asyncio

    await asyncio.sleep(0)
    assert len(t.backend) == 0 and "approve" in spoken[-1][0]
    assert [e for e in sink if e.get("event") == "escalation"]


async def test_clash_offers_alternatives(mk, spoken):
    seed = [{"title": "X", "start": "2026-11-14T19:00", "end": "2026-11-14T20:00"}]
    _s, t, _st, _ = await run(mk, BOOK, seed=seed)
    assert "already taken" in spoken[-1][0] and "I could offer" in spoken[-1][0]
    assert len(t.backend) == 1  # only the seed


async def test_remind_in_and_not_in_calendar(mk, spoken):
    seed = [
        {"title": "Dentist", "start": "2026-11-14T15:00", "end": "2026-11-14T16:00"}
    ]
    await run(mk, 'REMIND {"query":"dentist","when":""}', seed=seed)
    assert "Yes, Dentist is on" in spoken[-1][0]
    await run(mk, 'REMIND {"query":"flight","when":""}', seed=seed)
    assert "don't see that" in spoken[-1][0]


async def test_remind_unknown_caller_no_details(mk, spoken):
    seed = [
        {"title": "Dentist", "start": "2026-11-14T15:00", "end": "2026-11-14T16:00"}
    ]
    await run(mk, 'REMIND {"query":"dentist","when":""}', trust="unknown", seed=seed)
    assert "Dentist" not in spoken[-1][0]


async def test_change_escalates(mk, spoken):
    await run(mk, 'CHANGE {"what":"cancel","detail":"dinner"}')
    assert "can't change existing events" in spoken[-1][0]
