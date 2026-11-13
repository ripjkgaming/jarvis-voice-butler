import sqlite3
import threading

import pytest

from calls import calllog, store


def test_never_touches_home(calls_env):
    store.connect()
    assert store.db_path() == calls_env / "calls" / "calls.db"
    assert store.db_path().exists()


def test_wal_and_migration_table(calls_env):
    c = store.connect()
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store.schema_version() >= 1
    rows = c.execute("SELECT version FROM schema_migrations").fetchall()
    assert len(rows) == len({r[0] for r in rows})


def test_migration_idempotent_across_reconnect():
    store.connect()
    v = store.schema_version()
    store.close_all()
    assert store.schema_version() == v
    n = store.connect().execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert n == len(store.MIGRATIONS)


def test_thread_safe_inserts():
    store.connect()
    errs = []

    def work(i):
        try:
            for j in range(20):
                calllog.audit("t", {"i": i, "j": j})
        except Exception as e:  # pragma: no cover
            errs.append(e)

    ts = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    assert len(calllog.list_audit(1000, kind="t")) == 120


def test_settings_roundtrip():
    assert store.get_setting("x") is None
    assert store.get_bool_setting("disclose_ai", True) is True
    store.set_setting("disclose_ai", "0")
    store.set_setting("disclose_ai", "0")
    assert store.get_bool_setting("disclose_ai", True) is False


@pytest.mark.parametrize(
    "raw,want",
    [
        ("91234567", "+6591234567"),
        ("9123 4567", "+6591234567"),
        ("+65 9123-4567", "+6591234567"),
        ("6591234567", "+6591234567"),
        ("0065 9123 4567", "+6591234567"),
        ("(+65) 6123 4567", "+6561234567"),
        ("+44 7700 900123", "+447700900123"),
        ("447700900123", "+447700900123"),
        ("+1 (415) 555-2671", "+14155552671"),
    ],
)
def test_normalize_ok(raw, want):
    assert store.normalize_number(raw) == want


@pytest.mark.parametrize(
    "raw",
    ["", "abc", "123", "+65 1234", "51234567", "+0123456789", "+" + "1" * 16, None],
)
def test_normalize_bad(raw):
    with pytest.raises(ValueError):
        store.normalize_number(raw)
    assert store.try_normalize(raw) is None


def test_calllog_append_only():
    cid = calllog.start_call("in", "91234567", None, "")
    calllog.add_turn(cid, "caller", "hi", ts=1.0)
    c = store.connect()
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("UPDATE turns SET text='x'")
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("DELETE FROM turns")
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("UPDATE audit SET kind='x'")
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("DELETE FROM audit")
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("DELETE FROM calls")


def test_call_lifecycle():
    cid = calllog.start_call("out", "+65 9123 4567", 7, "book table", ts=100.0)
    calllog.add_turn(cid, "jarvis", "hello", ts=101.0)
    calllog.add_turn(cid, "caller", "yes?", ts=102.0)
    calllog.end_call(cid, "completed", "booked", ts=110.0)
    c = calllog.get_call(cid)
    assert c["number"] == "+6591234567"
    assert c["outcome"] == "completed" and c["summary"] == "booked"
    assert [t["speaker"] for t in c["turns"]] == ["jarvis", "caller"]
    with pytest.raises(ValueError):
        calllog.end_call(cid, "again", "x")
    assert calllog.get_call(cid)["summary"] == "booked"
    kinds = [a["kind"] for a in calllog.list_audit(10)]
    assert "call_start" in kinds and "call_end" in kinds


def test_calllog_validation_and_recent():
    with pytest.raises(ValueError):
        calllog.start_call("sideways", "91234567", None, "")
    cid = calllog.start_call("in", "91234567", None, "")
    with pytest.raises(ValueError):
        calllog.add_turn(cid, "robot", "x")
    with pytest.raises(KeyError):
        calllog.add_turn(999, "caller", "x")
    with pytest.raises(KeyError):
        calllog.end_call(999, "x", "y")
    assert calllog.get_call(999) is None
    ids = [calllog.start_call("in", "91234567", 1, "", ts=float(i)) for i in range(4)]
    assert [c["id"] for c in calllog.recent(2)] == [ids[3], ids[2]]
    assert all(c["contact_id"] == 1 for c in calllog.recent(10, contact_id=1))


def test_audit_order_and_detail():
    calllog.audit("a", {"k": 1})
    calllog.audit("b", {"k": 2})
    got = calllog.list_audit(2)
    assert [g["kind"] for g in got] == ["b", "a"]
    assert got[0]["detail"] == {"k": 2}
