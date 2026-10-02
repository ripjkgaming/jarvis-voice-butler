"""Failure-boundary regressions for the paper ledger; no live data or inference."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from paper_market.engine import Engine, TradeError, apply_proposal
from paper_market.feed import Quote
from paper_market.ledger import Ledger
from system.codex_worker import VERIFIED_VERSION

NOW = datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, value=NOW):
        self.value = value

    def __call__(self):
        return self.value


def proposal():
    return {
        "actions": [
            {
                "symbol": "SPY",
                "side": "buy",
                "notional_usd": "100",
                "quantity": None,
            }
        ],
        "rationale": "A bounded simulated allocation.",
    }


def result():
    return {
        "proposal": proposal(),
        "model": "gpt-6-astra",
        "effort": "high",
        "usage": {"last": {"inputTokens": 21, "outputTokens": 9, "totalTokens": 30}},
        "verification": {
            "model_verified": True,
            "effort_verified": True,
            "tools_disabled": True,
            "provider": "openai",
            "cli_version": VERIFIED_VERSION,
            "thread_id": "fake-thread",
            "turn_id": "fake-turn",
        },
    }


class Worker:
    def __init__(
        self, *, response=None, after_start=None, after_proposal=None, close_error=False
    ):
        self.response = result() if response is None else response
        self.after_start = after_start
        self.after_proposal = after_proposal
        self.close_error = close_error
        self.closed = False
        self.calls = 0

    async def start(self, workspace):
        assert workspace.is_dir()
        if self.after_start:
            self.after_start()

    async def propose(self, context):
        self.calls += 1
        if self.after_proposal:
            self.after_proposal()
        return self.response

    async def close(self):
        self.closed = True
        if self.close_error:
            raise OSError("simulated cleanup error")


@pytest.fixture
def ledger(tmp_path):
    instance = Ledger(tmp_path / "ledger.sqlite3")
    instance.initialize(NOW)
    return instance


def quotes(at=NOW, symbols=("SPY",)):
    return {
        symbol: Quote(symbol, Decimal("100"), at, fetched_at=at) for symbol in symbols
    }


def engine(ledger, *, worker=None, clock=None):
    clock = clock or Clock()
    worker = worker or Worker()

    def fetch(symbols):
        return quotes(clock(), symbols), {}

    return Engine(ledger, fetch=fetch, worker_factory=lambda: worker, clock=clock)


def raw_state(ledger):
    with ledger.connect() as db:
        return {
            "account": dict(db.execute("SELECT * FROM experiment").fetchone()),
            "holdings": [dict(row) for row in db.execute("SELECT * FROM holdings")],
            "fills": [dict(row) for row in db.execute("SELECT * FROM fills")],
            "decisions": [dict(row) for row in db.execute("SELECT * FROM decisions")],
        }


def assert_cash_unchanged(ledger):
    state = raw_state(ledger)
    assert Decimal(state["account"]["cash"]) == Decimal("1000")
    assert Decimal(state["account"]["realized_pnl"]) == 0
    assert state["holdings"] == []
    assert state["fills"] == []


async def test_fill_status_requires_atomic_proposal_usage_and_verification(ledger):
    # A filled decision may never be visible without its audit metadata, even
    # transiently between SQL writes in the same transaction.
    with ledger.connect() as db:
        db.execute(
            """CREATE TRIGGER require_fill_audit BEFORE UPDATE ON decisions
               WHEN NEW.status='filled' AND (
                   NEW.proposal_json IS NULL OR NEW.rationale IS NULL OR
                   NEW.usage_json IS NULL OR NEW.verification_json IS NULL)
               BEGIN SELECT RAISE(ABORT, 'missing fill audit'); END"""
        )
    response = result()
    worker = Worker(response=response)
    assert (await engine(ledger, worker=worker).run_once())["status"] == "filled"
    state = raw_state(ledger)
    assert len(state["fills"]) == len(state["holdings"]) == 1
    decision = state["decisions"][0]
    assert decision["status"] == "filled"
    assert json.loads(decision["proposal_json"]) == response["proposal"]
    assert json.loads(decision["usage_json"]) == response["usage"]
    assert json.loads(decision["verification_json"]) == response["verification"]
    assert decision["rationale"] == response["proposal"]["rationale"]
    assert decision["model"] == "gpt-6-astra"
    assert decision["effort"] == "high"
    assert worker.closed


async def test_audit_write_failure_rolls_back_cash_shares_and_fills(ledger):
    with ledger.connect() as db:
        db.execute(
            """CREATE TRIGGER fail_fill_audit BEFORE UPDATE ON decisions
               WHEN NEW.status='filled'
               BEGIN SELECT RAISE(ABORT, 'simulated audit disk failure'); END"""
        )
    outcome = await engine(ledger).run_once()
    assert outcome["status"] in {"error", "rejected"}
    assert_cash_unchanged(ledger)
    assert raw_state(ledger)["decisions"][0]["status"] != "filled"


def test_corrupted_second_fill_insert_rolls_back_entire_batch(ledger):
    ledger.begin_decision("two-fills", NOW, {})
    with ledger.connect() as db:
        db.execute(
            """CREATE TRIGGER fail_second_fill BEFORE INSERT ON fills
               WHEN NEW.symbol='AAPL'
               BEGIN SELECT RAISE(ABORT, 'simulated second insert failure'); END"""
        )
    batch = proposal()
    batch["actions"].append({**batch["actions"][0], "symbol": "AAPL"})
    with pytest.raises(sqlite3.IntegrityError):
        apply_proposal(ledger, "two-fills", batch, quotes(symbols=("SPY", "AAPL")), NOW)
    assert_cash_unchanged(ledger)
    assert raw_state(ledger)["decisions"][0]["status"] == "running"


async def test_runtime_failure_after_commit_preserves_filled_result(
    ledger, monkeypatch
):
    runtime = ledger.runtime
    attempts = []

    def fail_completed_runtime(**values):
        if values.get("last_cycle_status") == "filled":
            attempts.append(values)
            raise sqlite3.OperationalError("simulated runtime write failure")
        runtime(**values)

    monkeypatch.setattr(ledger, "runtime", fail_completed_runtime)
    outcome = await engine(ledger).run_once()
    assert attempts
    assert outcome["status"] == "filled"
    state = raw_state(ledger)
    assert len(state["fills"]) == 1
    assert state["decisions"][0]["status"] == "filled"
    assert state["decisions"][0]["error"] is None
    assert Decimal(state["account"]["cash"]) < 1000


async def test_worker_close_failure_does_not_override_committed_fill(ledger):
    worker = Worker(close_error=True)
    outcome = await engine(ledger, worker=worker).run_once()
    assert worker.closed
    assert outcome["status"] == "filled"
    state = raw_state(ledger)
    assert len(state["fills"]) == 1
    assert state["decisions"][0]["status"] == "filled"
    assert state["decisions"][0]["error"] is None


@pytest.mark.parametrize(
    "missing", ["verification", "model_verified", "effort_verified", "tools_disabled"]
)
async def test_missing_isolation_verification_rejects_proposal(ledger, missing):
    response = result()
    if missing == "verification":
        response.pop("verification")
    else:
        response["verification"].pop(missing)
    outcome = await engine(ledger, worker=Worker(response=response)).run_once()
    assert outcome["status"] == "rejected"
    assert_cash_unchanged(ledger)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("tools_disabled", False),
        ("model_verified", 1),
        ("provider", "other"),
        ("cli_version", "unverified"),
    ],
)
async def test_false_or_untrusted_verification_rejects_proposal(ledger, key, value):
    response = result()
    response["verification"][key] = value
    outcome = await engine(ledger, worker=Worker(response=response)).run_once()
    assert outcome["status"] == "rejected"
    assert_cash_unchanged(ledger)


@pytest.mark.parametrize("deadline", ["expiry", "market_close"])
async def test_deadline_crossing_during_startup_prevents_inference(ledger, deadline):
    clock = Clock()
    target = (
        NOW + timedelta(hours=168) if deadline == "expiry" else NOW.replace(hour=20)
    )
    worker = Worker(after_start=lambda: setattr(clock, "value", target))
    outcome = await engine(ledger, worker=worker, clock=clock).run_once()
    assert worker.calls == 0
    assert worker.closed
    assert outcome["status"] == "rejected"
    assert_cash_unchanged(ledger)


@pytest.mark.parametrize("deadline", ["expiry", "market_close"])
async def test_deadline_crossing_during_model_turn_prevents_fill(ledger, deadline):
    clock = Clock()
    target = (
        NOW + timedelta(hours=168) if deadline == "expiry" else NOW.replace(hour=20)
    )
    worker = Worker(after_proposal=lambda: setattr(clock, "value", target))
    outcome = await engine(ledger, worker=worker, clock=clock).run_once()
    assert worker.calls == 1
    assert worker.closed
    assert outcome["status"] == "rejected"
    assert_cash_unchanged(ledger)


@pytest.mark.parametrize("deadline", ["expiry", "market_close", "quote_age"])
def test_precommit_clock_recheck_blocks_deadline_crossing(ledger, deadline):
    ledger.begin_decision("deadline", NOW, {})
    target = {
        "expiry": NOW + timedelta(hours=168),
        "market_close": NOW.replace(hour=20),
        "quote_age": NOW + timedelta(seconds=121),
    }[deadline]
    with pytest.raises(TradeError):
        apply_proposal(
            ledger, "deadline", proposal(), quotes(), NOW, clock=lambda: target
        )
    assert_cash_unchanged(ledger)


@pytest.mark.parametrize("status", ["rejected", "filled"])
async def test_terminal_filled_decision_cannot_be_rewritten(ledger, status):
    outcome = await engine(ledger).run_once()
    assert outcome["status"] == "filled"
    decision = raw_state(ledger)["decisions"][0]
    ledger.finish_decision(
        decision["id"],
        NOW + timedelta(seconds=1),
        status,
        result=None,
        error="late bookkeeping error",
    )
    assert raw_state(ledger)["decisions"][0] == decision
