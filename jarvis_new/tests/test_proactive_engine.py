"""Hermetic tests for the proactive engine and its sources (IRONMAN_SPEC §1)."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from proactive import engine
from proactive.engine import Context, Decision, ProactiveEngine, decide
from proactive.signals import Signal
from proactive.sources import (
    calendar_source,
    deadline_source,
    jobs_source,
    mail_source,
    system_source,
)

NOW = dt.datetime(2026, 10, 1, 14, 0).timestamp()


def sig(urgency="normal", kind="calendar", key="k1") -> Signal:
    return Signal(kind=kind, key=key, title="Meeting in 10", urgency=urgency, ts=NOW)


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    return tmp_path


# --- decide(): the §1.2 policy ---


@pytest.mark.parametrize(
    ("urgency", "ctx", "expected"),
    [
        ("critical", Context(quiet=True, dnd=True, focus=True, spoken_today=99), "say"),
        ("low", Context(idle_s=60), "hud"),
        ("high", Context(quiet=True), "toast"),
        ("high", Context(dnd=True), "toast"),
        ("high", Context(focus=True), "toast"),
        ("high", Context(ignored_streak=3), "toast"),
        ("high", Context(spoken_today=12, cap=12), "toast"),
        ("high", Context(idle_s=0), "say"),
        ("normal", Context(idle_s=45), "say"),
        ("normal", Context(idle_s=5), "toast"),
        ("normal", Context(idle_s=3600), "toast"),
        ("normal", Context(idle_s=None), "toast"),
    ],
)
def test_decide_matrix(urgency, ctx, expected) -> None:
    assert decide(sig(urgency), ctx).action == expected


def test_decide_reasons_are_logged_codes() -> None:
    assert decide(sig("high"), Context(focus=True)) == Decision("toast", "focus-mode")
    assert decide(sig("normal"), Context(idle_s=45)).reason == "natural-pause"


# --- engine loop ---


class Recorder:
    def __init__(self, say_ok=True):
        self.calls: list[tuple[str, str]] = []
        self.say_ok = say_ok
        self.logs: list[str] = []

    def make(self, sources, ctx: Context):
        return ProactiveEngine(
            sources,
            context_fn=lambda state, kind, now: Context(
                **{
                    **ctx.__dict__,
                    "spoken_today": int(
                        (state.get("spoken") or {}).get(
                            dt.date.fromtimestamp(now).isoformat()
                        )
                        or 0
                    ),
                    "ignored_streak": int((state.get("streaks") or {}).get(kind) or 0),
                }
            ),
            say=lambda s: self.calls.append(("say", s.key)) or self.say_ok,
            toast=lambda s: self.calls.append(("toast", s.key)) or True,
            hud=lambda s: self.calls.append(("hud", s.key)) or True,
            log=lambda cat, msg: self.logs.append(msg),
        )


def test_engine_delivers_once_and_logs(home) -> None:
    rec = Recorder()
    eng = rec.make(
        [lambda now: [sig("high", key="a"), sig("low", key="b")]], Context(idle_s=0)
    )
    eng.run_once(NOW)
    eng.run_once(NOW + 30)
    assert rec.calls == [("say", "a"), ("hud", "b")]
    assert rec.logs[0] == "calendar say high key=a"


def test_engine_failed_speech_falls_back_to_toast(home) -> None:
    rec = Recorder(say_ok=False)
    out = rec.make([lambda now: [sig("high")]], Context(idle_s=0)).run_once(NOW)
    assert rec.calls == [("say", "k1"), ("toast", "k1")]
    assert out[0][1].reason == "high+speaker-failed"


def test_engine_source_errors_are_swallowed(home) -> None:
    rec = Recorder()

    def broken(now):
        raise RuntimeError("calendar down")

    rec.make([broken, lambda now: [sig("high")]], Context(idle_s=0)).run_once(NOW)
    assert rec.calls == [("say", "k1")]
    assert any("source-error" in m for m in rec.logs)


def test_engine_daily_cap(home) -> None:
    rec = Recorder()
    signals = [sig("high", key=f"s{i}") for i in range(4)]
    rec.make([lambda now: signals], Context(idle_s=0, cap=2)).run_once(NOW)
    assert [c[0] for c in rec.calls] == ["say", "say", "toast", "toast"]


def test_ignored_three_times_downgrades_until_answered(home) -> None:
    rec = Recorder()
    n = {"i": 0}

    def source(now):
        n["i"] += 1
        return [sig("high", kind="calendar", key=f"c{n['i']}")]

    eng = rec.make([source], Context(idle_s=0))
    t = NOW
    for _ in range(3):
        eng.run_once(t)
        t += engine.ACK_WINDOW_S + 10  # nobody answered
    eng.run_once(t)
    assert [c[0] for c in rec.calls] == ["say", "say", "say", "toast"]
    engine.note_user_turn(t + 5)
    state = engine.load_state()
    state["streaks"]["calendar"] = 0  # an answered announcement resets it
    engine.save_state(state)
    eng.run_once(t + 10)
    assert rec.calls[-1][0] == "say"


def test_settle_streaks_answered_resets() -> None:
    state = {
        "pending_acks": [{"ts": 100.0, "kind": "mail"}],
        "streaks": {"mail": 2},
        "last_user_ts": 150.0,
    }
    engine.settle_streaks(state, 400.0)
    assert state["streaks"]["mail"] == 0 and state["pending_acks"] == []


# --- sources ---


def _event(eid, title, start_min, dur_min=30, **extra):
    s = dt.datetime.fromtimestamp(NOW + start_min * 60).astimezone()
    e = s + dt.timedelta(minutes=dur_min)
    return {
        "id": eid,
        "summary": title,
        "start": {"dateTime": s.isoformat()},
        "end": {"dateTime": e.isoformat()},
        **extra,
    }


def test_calendar_stages_and_clash() -> None:
    events = [
        _event("a", "Chemistry", 10, location="Lab 2"),
        _event("b", "Standup", 4),
        _event("c", "Call", 0),
        _event("d", "Later", 90),
        _event("e", "Overlap", 95),
        {"id": "x", "summary": "Holiday", "start": {"date": "2026-10-01"}},
    ]
    sigs = calendar_source.signals_for(events, NOW)
    by_key = {s.key: s for s in sigs}
    assert (
        by_key["cal:a:10min"].title == "Chemistry starts in 10 minutes, Sir, at Lab 2."
    )
    assert by_key["cal:b:5min"].urgency == "high"
    assert by_key["cal:c:now"].title == "Call is starting now, Sir."
    assert "cal-clash:d:e" in by_key
    assert not any("x" in k.split(":")[1] for k in by_key if k.startswith("cal:"))


def test_calendar_poll_uses_account_and_caches(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_CALENDAR_ACCOUNT", "school")
    seen = []

    def lister(a, b, calendar_id, account):
        seen.append(account)
        return [_event("a", "Maths", 10)]

    calendar_source._cache.update(at=0.0, events=[])
    assert calendar_source.poll(NOW, lister)[0].key == "cal:a:10min"
    assert seen == ["school"]
    assert calendar_source.fetch(NOW + 10)[0]["id"] == "a"  # cached, no lister


def test_mail_source_normal_mail_is_hud_only(home) -> None:
    import mail_log

    mail_log.record("received", id="m1", sender="Bob", subject="Lunch?", ts=NOW - 60)
    mail_log.record("flagged", id="m2", sender="Teacher", subject="Urgent", ts=NOW - 60)
    sigs = mail_source.poll(NOW)
    assert [(s.key, s.urgency) for s in sigs] == [("mail:m1", "low")]


def test_jobs_source(home) -> None:
    import jobs

    a = jobs.start("The test run", "tests", now=NOW - 100)
    jobs.finish(a, ok=False, summary="2 failed", failures=2, now=NOW - 50)
    b = jobs.start("The build", "build", now=NOW - 100)
    jobs.finish(b, ok=True, now=NOW - 40)
    jobs.start("Still going", "build", now=NOW)
    sigs = {s.title: s.urgency for s in jobs_source.poll(NOW)}
    assert sigs == {
        "The test run finished, Sir: 2 failures.": "high",
        "The build finished cleanly, Sir.": "normal",
    }


def test_system_source_disk() -> None:
    assert system_source.poll(NOW, lambda p: (100, 50, 50)) == []
    low = system_source.poll(NOW, lambda p: (100e9, 97e9, 3e9))
    assert low[0].urgency == "high" and "3.0 gigabytes" in low[0].title


def test_deadline_source() -> None:
    todos = [
        {"text": "english essay due tomorrow"},
        {"text": "permission form by 2026-10-01"},
        {"text": "old thing", "due": "2026-09-20"},
        {"text": "done one due today", "done": True},
        {"text": "no date here"},
    ]
    sigs = {s.title: s.urgency for s in deadline_source.poll(NOW, todos)}
    assert sigs == {
        "Reminder, Sir: english essay is due tomorrow.": "normal",
        "Sir, permission form is due today.": "high",
        "Overdue: old thing": "low",
    }


def test_default_sources_registered() -> None:
    from proactive.sources import default_sources

    assert len(default_sources()) == 5


def test_state_file_roundtrip(home) -> None:
    engine.save_state({"x": 1})
    assert json.loads(engine.state_path().read_text()) == {"x": 1}
