"""Always Aware transaction audit (PLAN.md §4.4): append-only, 0600, queries."""

import json
import os
from decimal import Decimal

import pytest

from aware import transactions as tx


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    return tmp_path


def rec(**kw):
    now = kw.pop("now", 1_000_000.0)
    base = {
        "run": "r1",
        "kind": "payment",
        "host": "shop.example",
        "counterparty": "Scan Computers",
        "amount": Decimal("94.99"),
        "currency": "GBP",
        "items": ["2 TB SSD"],
        "content": "",
        "offer_text": "Check out the SSD for £94.99?",
        "result": "ok",
        "confirmation_ref": "ORDER-1",
    }
    base.update(kw)
    return tx.record(**base, now=now)


def test_record_appends_private_line(_home):
    entry = rec()
    path = _home / "aware" / "transactions.jsonl"
    assert (os.stat(path).st_mode & 0o777) == 0o600
    line = json.loads(path.read_text().splitlines()[0])
    assert line["kind"] == "payment" and line["amount"] == "94.99"
    assert line["currency"] == "GBP" and line["confirmation_ref"] == "ORDER-1"
    assert line["content_sha256"] == entry["content_sha256"]
    audit = (_home / "aware" / "audit.jsonl").read_text()
    assert "payment" in audit


def test_content_is_hashed_and_truncated():
    entry = rec(
        kind="send", content="x" * 500, counterparty="mum@example.com", amount=None
    )
    assert len(entry["content_head"]) == 200
    assert len(entry["content_sha256"]) == 64
    assert entry["amount"] is None


def test_failed_attempts_are_recorded_too(_home):
    rec(result="stuck: total_drift")
    rows = tx.read_all()
    assert rows[0]["result"].startswith("stuck")


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        rec(kind="refund")


def test_append_only_never_rewrites(_home):
    rec(now=1.0)
    first = (_home / "aware" / "transactions.jsonl").read_text()
    rec(now=2.0, confirmation_ref="ORDER-2")
    second = (_home / "aware" / "transactions.jsonl").read_text()
    assert second.startswith(first)


def test_query_by_kind_and_window():
    rec(now=100.0)
    rec(now=200.0, kind="send", amount=None, counterparty="Bob")
    rec(now=300.0, kind="submit", amount=None, host="council.example")
    rec(now=400.0, result="stuck: no_click")
    pays = tx.query(kinds=("payment",), since=0, until=1000)
    assert [r["ts"] for r in pays] == [100.0, 400.0]
    assert len(tx.query(kinds=("send", "submit"), since=150, until=1000)) == 2


def test_spoken_summary():
    rec(now=100.0)
    rec(
        now=110.0,
        amount=Decimal("5.00"),
        counterparty="Coffee Ltd",
        confirmation_ref="C9",
    )
    rec(now=120.0, result="stuck: total_drift", amount=Decimal("999"))
    say = tx.spoken(
        tx.query(kinds=("payment",), since=0, until=1000), "paid", "this week"
    )
    assert "two payments" in say.lower()
    assert "£99.99" in say  # only completed ones are totalled
    assert "Scan Computers" in say and "Coffee Ltd" in say
    assert "one more was stopped" in say.lower()
    assert tx.spoken([], "paid", "today") == "Nothing paid today, Sir."


def test_spend_cap_check():
    assert tx.within_cap(Decimal("50"), None) is True
    assert tx.within_cap(Decimal("50"), 50) is True
    assert tx.within_cap(Decimal("50.01"), 50) is False
    assert tx.within_cap(None, 50) is False  # unknown amount never passes a cap
