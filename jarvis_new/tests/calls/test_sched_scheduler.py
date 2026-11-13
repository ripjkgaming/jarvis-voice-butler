import types

import pytest

from calls import scheduler as sch

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_CALLS_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_SCHEDULED_TASKS", "1")
    monkeypatch.delenv("JARVIS_GOODNIGHT_V2", raising=False)


def mk(store, due=NOW + 60, goal="ask about dinner", **kw):
    return store.create("Mum", "+6591234567", goal, due, now=NOW, **kw)


def test_create_list_get_cancel():
    s = sch.Store()
    j = mk(s)
    assert j["status"] == "pending" and j["id"].startswith("call-")
    assert s.get(j["id"])["goal"] == "ask about dinner"
    assert [x["id"] for x in s.list(pending_only=True)] == [j["id"]]
    assert s.cancel(j["id"]) and not s.cancel(j["id"])
    assert s.list(pending_only=True) == []


def test_create_validation():
    s = sch.Store()
    with pytest.raises(sch.ScheduleError):
        s.create("Mum", "+65", "x", NOW - 5, now=NOW)
    with pytest.raises(sch.ScheduleError):
        s.create("", "", "x", NOW + 5, now=NOW)
    with pytest.raises(sch.ScheduleError):
        s.create("Mum", "+65", "x" * 301, NOW + 5, now=NOW)


def test_idempotent_request_id():
    s = sch.Store()
    assert mk(s, request_id="r1")["id"] == mk(s, request_id="r1")["id"]
    assert mk(s, request_id="r2")["id"] != mk(s, request_id="r1")["id"]


def test_tick_dispatches_once_with_job():
    s = sch.Store()
    j = mk(s)
    seen = []
    assert s.tick(now=NOW + 30, dial=seen.append) == 0  # not due
    assert s.tick(now=NOW + 61, dial=lambda r: seen.append(r) or True) == 1
    assert seen[0]["number"] == "+6591234567" and seen[0]["goal"] == "ask about dinner"
    assert s.get(j["id"])["status"] == "queued"
    assert s.tick(now=NOW + 62, dial=seen.append) == 0 and len(seen) == 1


def test_receipt_statuses_and_exception():
    s = sch.Store()
    a, b, c = mk(s, goal="a"), mk(s, goal="b"), mk(s, goal="c")

    def dial(row):
        if row["goal"] == "a":
            return {"status": "started", "reason": "ok"}
        if row["goal"] == "b":
            raise RuntimeError("boom secret")
        return {"status": "weird"}

    s.tick(now=NOW + 61, dial=dial)
    assert s.get(a["id"])["status"] == "started"
    assert s.get(b["id"])["status"] == "failed"
    assert "boom" not in s.get(b["id"])["reason"]
    assert s.get(c["id"])["status"] == "failed"


def test_crash_is_uncertain_not_replayed():
    s = sch.Store()
    j = mk(s)
    with s.db() as db:  # simulate claim then crash before receipt
        db.execute(
            "UPDATE scheduled_calls SET status='dispatching',claimed=? WHERE id=?",
            (NOW + 61, j["id"]),
        )
    seen = []
    s.tick(now=NOW + 61 + sch.GRACE_S + 5, dial=seen.append)
    row = s.get(j["id"])
    assert row["status"] == "uncertain" and seen == []


def test_missed_when_overdue():
    s = sch.Store()
    j = mk(s)
    seen = []
    s.tick(now=NOW + 60 + sch.GRACE_S + 5, dial=seen.append)
    assert s.get(j["id"])["status"] == "missed" and seen == []


def test_cancel_during_dispatch_window():
    s = sch.Store()
    j = mk(s)

    def dial(row):
        return True

    # cancel after claim but before handoff: row not dispatching -> skipped
    s.cancel(j["id"])
    assert s.tick(now=NOW + 61, dial=dial) == 0
    assert s.get(j["id"])["status"] == "cancelled"


def test_no_dialer_fails_safe():
    s = sch.Store()
    j = mk(s)
    s.tick(now=NOW + 61)
    assert s.get(j["id"])["status"] == "failed"


def test_disabled(monkeypatch):
    s = sch.Store()
    mk(s)
    monkeypatch.setenv("JARVIS_SCHEDULED_TASKS", "0")
    assert s.tick(now=NOW + 61, dial=lambda r: True) == 0
    with pytest.raises(sch.ScheduleError):
        mk(s)


def test_cancel_for_contact():
    s = sch.Store()
    mk(s), mk(s, goal="b")
    assert s.cancel_for_contact("mum") == 2


def test_rule_expiry():
    active = {1: True, 2: True}
    cancelled, audits = [], []
    rules = types.SimpleNamespace(
        active_rules=lambda now: [
            types.SimpleNamespace(id=i) for i, a in active.items() if a
        ],
        cancel_rule=lambda rid=None: cancelled.append(rid) or 1,
    )
    log = types.SimpleNamespace(audit=lambda k, d: audits.append((k, d)))
    ex = sch.RuleExpiry(rules, log)
    assert ex.check(NOW) == []
    active[2] = False
    assert ex.check(NOW + 1) == [2]
    assert cancelled == [2] and audits[0][0] == "rule_expired"
    assert ex.check(NOW + 2) == []
