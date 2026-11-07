"""Auto-add of email events to the calendar, ledger dedupe and undo."""

from __future__ import annotations

import datetime as dt
import json
import subprocess

import chatgpt_cli
import pytest

import event_extractor as ex

NOW = dt.datetime(2026, 10, 1, 9, 0).timestamp()
EV = {
    "has_event": True,
    "title": "Chiang Mai meetup",
    "date": "2026-11-01",
    "start": "15:00",
    "end": "",
    "all_day": False,
    "location": "",
    "confidence": 0.95,
}
MAIL = {"id": "m1", "sender": "Mr Borzel <b@x.com>", "subject": "Meetup", "body": "Sunday 3pm"}


def model(reply):
    def run(argv, **kw):
        out = json.dumps(
            {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(reply)}}
        ) + "\n" + json.dumps({"type": "turn.completed", "usage": {}}) + "\n"
        return subprocess.CompletedProcess(argv, 0, out, "")

    return run


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_EMAIL_EVENTS", "1")
    monkeypatch.delenv("JARVIS_AUTO_CALENDAR", raising=False)
    monkeypatch.setenv("JARVIS_CHATGPT", "1")
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(chatgpt_cli.shutil, "which", lambda n: "/usr/bin/codex")
    return tmp_path


def run(mail=MAIL, reply=EV, inserter=None, notes=None):
    notes = [] if notes is None else notes
    return ex.suggest(
        mail,
        now=NOW,
        runner=model(reply),
        lister=lambda *a, **k: [],
        inserter=inserter,
        notify_fn=lambda text, **kw: notes.append(kw["speak_text"]),
    )


def test_auto_add_once_and_notice(home) -> None:
    made, notes = [], []
    ins = lambda ev: made.append(ev) or {"id": "g1"}  # noqa: E731
    assert run(inserter=ins, notes=notes) == "auto-added"
    assert len(made) == 1 and made[0]["title"] == "Chiang Mai meetup"
    assert notes == [
        "Added Chiang Mai meetup, Sunday 1 November 3 PM, to your calendar, Sir "
        "(from Mr Borzel). Say 'undo that' to remove it."
    ]
    assert ex.pending() == [] and ex.get("m1")["status"] == "added"


def test_duplicate_email_and_key_not_double_added(home) -> None:
    made = []
    ins = lambda ev: made.append(ev) or {"id": f"g{len(made)}"}  # noqa: E731
    run(inserter=ins)
    assert run(inserter=ins) == "exists"
    # different email, same event -> ledger key dedupe
    assert run({**MAIL, "id": "m2"}, inserter=ins) == "auto-duplicate"
    assert len(made) == 1


def test_past_event_skipped(home) -> None:
    made = []
    res = ex.auto_add_event(
        {"id": "p", "event": {**ex.validate(EV, NOW), "date": "2026-09-01"}},
        now=NOW,
        lister=lambda *a, **k: [],
        inserter=lambda ev: made.append(ev) or {"id": "x"},
    )
    assert res["status"] == "past" and made == []


def test_insert_failure_queues_retry_never_asks(home, monkeypatch) -> None:
    monkeypatch.setattr(ex.time, "sleep", lambda s: None)
    calls = []

    def boom(ev):
        calls.append(1)
        raise RuntimeError("403 scope")

    notes = []
    assert run(inserter=boom, notes=notes) == "retry-queued"
    assert len(calls) == 4 and notes == []  # retried, never asked
    assert ex._ledger_load() == []
    made = []
    assert ex.retry_pending(now=NOW, lister=lambda *a, **k: [], inserter=lambda ev: made.append(ev) or {"id": "g1"}, notify_fn=lambda *a, **k: None) == 1
    assert len(made) == 1 and ex._ledger_load()[0]["google_id"] == "g1"
    assert ex.retry_pending(now=NOW, lister=lambda *a, **k: [], inserter=lambda ev: {"id": "g2"}, notify_fn=lambda *a, **k: None) == 0


def test_transient_failure_retries_then_adds(home, monkeypatch) -> None:
    monkeypatch.setattr(ex.time, "sleep", lambda s: None)
    n = []

    def flaky(ev):
        n.append(1)
        if len(n) < 3:
            raise RuntimeError("net")
        return {"id": "g"}

    assert run(inserter=flaky) == "auto-added"


def test_low_confidence_still_added(home) -> None:
    made = []
    out = run(reply={**EV, "confidence": 0.75}, inserter=lambda ev: made.append(ev) or {"id": "g"})
    assert out == "auto-added" and len(made) == 1


def test_blocked_context_skipped(home) -> None:
    assert run({**MAIL, "guest": True}) == "skipped-context"


def test_env_off_keeps_suggestion_flow(home, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_AUTO_CALENDAR", "0")
    made = []
    assert run(inserter=lambda ev: made.append(ev) or {"id": "g"}) == "suggested"
    assert made == []


def test_undo_deletes_only_ledger_events(home) -> None:
    deleted = []
    assert ex.undo_last_auto_event(deleter=deleted.append)["ok"] is False
    assert deleted == []
    run(inserter=lambda ev: {"id": "g1"})
    run({**MAIL, "id": "m3"}, reply={**EV, "title": "Other thing"}, inserter=lambda ev: {"id": "g2"})
    out = ex.undo_last_auto_event(deleter=deleted.append)
    assert out["ok"] and deleted == ["g2"] and "Other thing" in out["say"]
    ex.undo_last_auto_event(deleter=deleted.append)
    assert deleted == ["g2", "g1"]
    assert ex.undo_last_auto_event(deleter=deleted.append)["ok"] is False
    assert deleted == ["g2", "g1"]
    # undone mail is never re-added
    assert run(inserter=lambda ev: {"id": "z"}) == "exists"
