import datetime as dt
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from calls.calendar_tools import (
    TZ,
    CalendarTools,
    InMemoryCalendar,
    RealCalendarAdapter,
)

NOW = dt.datetime(2026, 11, 13, 10, 0, tzinfo=TZ)  # Friday


def mk(seed=None, trust="family", allow=("booking",)):
    cal = InMemoryCalendar(seed or [])
    contact = SimpleNamespace(trust=trust, policy={"allow_topics": list(allow)})
    audit, esc = [], []
    t = CalendarTools(
        cal,
        contact=contact,
        audit=lambda k, d: audit.append((k, d)),
        escalate=esc.append,
        clock=lambda: NOW,
    )
    t.log, t.esc = audit, esc
    return t


def kinds(t):
    return [k for k, _ in t.log]


def test_free_slot():
    t = mk()
    r = t.check_availability("2026-11-14T15:00", "2026-11-14T16:00")
    assert r["free"] and r["clashes"] == [] and r["suggestions"] == []


def test_clash_gives_three_sorted_suggestions():
    t = mk(
        [{"title": "Dentist", "start": "2026-11-14T15:00", "end": "2026-11-14T16:00"}]
    )
    r = t.check_availability("2026-11-14T15:00", "2026-11-14T16:00")
    assert not r["free"] and r["clashes"][0]["title"] == "Dentist"
    assert len(r["suggestions"]) == 3
    starts = [s["start"] for s in r["suggestions"]]
    assert (
        "2026-11-14T14:30:00+08:00" in starts or "2026-11-14T16:00:00+08:00" in starts
    )
    for s in r["suggestions"]:
        assert t.check_availability(s["start"], s["end"])["free"]


def test_adjacent_is_free():
    t = mk([{"title": "A", "start": "2026-11-14T14:00", "end": "2026-11-14T15:00"}])
    assert t.check_availability("2026-11-14T15:00", "2026-11-14T16:00")["free"]


def test_all_day_blocks():
    t = mk([{"title": "Offsite", "start": "2026-11-14", "all_day": True}])
    assert not t.check_availability("2026-11-14T11:00", "2026-11-14T12:00")["free"]


def test_recurring_expands():
    t = mk([{"title": "Standup", "start": "2026-11-09T09:00", "end": "2026-11-09T09:30",
             "recurrence": "weekly"}])  # fmt: skip
    assert not t.check_availability("2026-11-16T09:00", "2026-11-16T09:15")["free"]
    assert t.check_availability("2026-11-17T09:00", "2026-11-17T09:15")["free"]


def test_naive_input_is_singapore():
    t = mk(
        [
            {
                "title": "A",
                "start": "2026-11-14T15:00+08:00",
                "end": "2026-11-14T16:00+08:00",
            }
        ]
    )
    assert not t.check_availability("2026-11-14T07:00+00:00", "2026-11-14T07:30+00:00")[
        "free"
    ]
    assert not t.check_availability("2026-11-14T15:10", "2026-11-14T15:20")["free"]


def test_past_booking_denied_and_audited():
    t = mk()
    p = t.propose_booking("Lunch", "2026-11-13T08:00", "2026-11-13T09:00", "Mum")
    assert p.status == "denied" and "past" in p.reason
    assert "booking_denied" in kinds(t)


def test_family_with_policy_auto_confirms():
    t = mk()
    p = t.propose_booking("Dinner", "2026-11-14T19:00", "2026-11-14T20:00", "Mum")
    assert p.status == "proposed" and p.allow_auto
    r = t.confirm_booking(p.id)
    assert r["ok"] and len(t.backend) == 1
    assert "booking_confirmed" in kinds(t)
    assert t.confirm_booking(p.id) != {}  # idempotent
    assert len(t.backend) == 1


def test_no_booking_topic_escalates_on_confirm():
    t = mk(allow=())
    p = t.propose_booking("Dinner", "2026-11-14T19:00", "2026-11-14T20:00", "Mum")
    r = t.confirm_booking(p.id)
    assert not r["ok"] and r["status"] == "escalated"
    assert t.esc and len(t.backend) == 0 and "booking_escalated" in kinds(t)
    assert t.approve(p.id)["ok"] and len(t.backend) == 1


def test_work_trust_auto_but_blocked_policy_trust_not():
    assert (
        mk(trust="work")
        .propose_booking("x", "2026-11-14T19:00", "2026-11-14T20:00")
        .allow_auto
    )
    t = mk(trust="unknown")
    p = t.propose_booking("x", "2026-11-14T19:00", "2026-11-14T20:00", "stranger")
    assert p.status == "hold_pending_owner" and not p.allow_auto
    assert t.esc and not t.confirm_booking(p.id)["ok"] and len(t.backend) == 0


def test_never_double_book_even_with_owner_approval():
    t = mk(allow=())
    p = t.propose_booking("A", "2026-11-14T19:00", "2026-11-14T20:00", "Mum")
    t.backend.add_event("Other", dt.datetime(2026, 11, 14, 19, 30, tzinfo=TZ),
                        dt.datetime(2026, 11, 14, 20, 30, tzinfo=TZ))  # fmt: skip
    r = t.approve(p.id)
    assert not r["ok"] and r["status"] == "denied"


def test_clash_proposal_has_alternatives():
    t = mk([{"title": "X", "start": "2026-11-14T19:00", "end": "2026-11-14T20:00"}])
    p = t.propose_booking("A", "2026-11-14T19:30", "2026-11-14T20:30", "Mum")
    assert p.status == "denied" and p.clashes and len(p.suggestions) == 3


def test_double_request_second_clashes_after_confirm():
    t = mk()
    a = t.propose_booking("A", "2026-11-14T19:00", "2026-11-14T20:00", "Mum")
    t.confirm_booking(a.id)
    b = t.propose_booking("A again", "2026-11-14T19:00", "2026-11-14T20:00", "Mum")
    assert b.status == "denied" and b.clashes


def test_withdraw():
    t = mk()
    p = t.propose_booking("A", "2026-11-14T19:00", "2026-11-14T20:00", "Mum")
    assert t.cancel_proposal(p.id) and not t.confirm_booking(p.id)["ok"]
    assert len(t.backend) == 0 and "booking_withdrawn" in kinds(t)


def test_reminder_in_calendar_not_in_calendar_partial_two_past():
    t = mk([
        {"title": "Dentist appointment", "start": "2026-11-14T15:00", "end": "2026-11-14T16:00"},
        {"title": "Mum birthday dinner", "start": "2026-11-20T19:00", "end": "2026-11-20T21:00"},
        {"title": "Mum birthday call", "start": "2026-11-21T09:00", "end": "2026-11-21T09:30"},
        {"title": "Team lunch", "start": "2026-11-10T12:00", "end": "2026-11-10T13:00"},
    ])  # fmt: skip
    r = t.reminder_lookup("dentist")
    assert (
        r["in_calendar"] and r["event"]["title"].startswith("Dentist") and not r["past"]
    )
    r = t.reminder_lookup("flight to Tokyo")
    assert not r["in_calendar"] and r["event"] is None and r["nearby"] == []
    r = t.reminder_lookup("dentist checkup tomorrow")
    assert r["in_calendar"] or r["nearby"]
    r = t.reminder_lookup("dentist cleaning")
    assert not r["in_calendar"] and r["nearby"][0]["title"].startswith("Dentist")
    r = t.reminder_lookup("Mum birthday")
    assert r["in_calendar"] and r["ambiguous"] and len(r["nearby"]) == 1
    r = t.reminder_lookup("team lunch")
    assert r["in_calendar"] and r["past"]
    assert "calendar_reminder_lookup" in kinds(t)


def test_context_line():
    t = mk(
        [{"title": "Dentist", "start": "2026-11-14T15:00", "end": "2026-11-14T16:00"}]
    )
    assert "Dentist Sat 15:00-16:00" in t.context_line()
    assert "no events" in mk().context_line()


def test_real_adapter_refused_in_tests():
    with pytest.raises(RuntimeError):
        RealCalendarAdapter(actions=object(), google=object())


def test_real_adapter_maps_items(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    acts = SimpleNamespace(
        events_on=lambda d: {"events": [{"id": "g1", "title": "Gym",
            "start": {"dateTime": f"{d}T07:00:00+08:00"},
            "end": {"dateTime": f"{d}T08:00:00+08:00"}}]},
        add=lambda ev: {"ok": True, "event_id": "g2"},
    )  # fmt: skip
    a = RealCalendarAdapter(actions=acts, google=object())
    evs = a.list_events(
        dt.datetime(2026, 11, 14, 6, tzinfo=TZ), dt.datetime(2026, 11, 14, 9, tzinfo=TZ)
    )
    assert [e.title for e in evs] == ["Gym"]
    assert a.add_event("x", NOW, NOW + dt.timedelta(hours=1)) == "g2"
    with pytest.raises(NotImplementedError):
        a.delete("g1")
