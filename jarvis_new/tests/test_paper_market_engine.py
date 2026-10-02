from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from paper_market.engine import Engine, TradeError, apply_proposal
from paper_market.feed import Quote
from paper_market.ledger import Ledger

NOW = datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def ledger(tmp_path):
    result = Ledger(tmp_path / "ledger.sqlite3")
    result.initialize(NOW)
    return result


def quotes(now=NOW):
    return {"SPY": Quote("SPY", Decimal("100"), now, fetched_at=now)}


def proposal(side="buy", amount="100", symbol="SPY"):
    return {
        "actions": [
            {
                "symbol": symbol,
                "side": side,
                "notional_usd": amount if side == "buy" else None,
                "quantity": amount if side == "sell" else None,
            }
        ],
        "rationale": "Bounded paper allocation.",
    }


def decision(ledger, identifier="d1"):
    ledger.begin_decision(identifier, NOW, {"test": True})
    return identifier


def test_start_is_persistent_and_exactly_168_hours(ledger):
    first = ledger.experiment()
    ledger.initialize(NOW + timedelta(days=2))
    assert ledger.experiment() == first
    assert datetime.fromisoformat(first["expires_at"]) - NOW == timedelta(hours=168)
    assert ledger.snapshot(NOW)["account"]["equity_usd"] == "1000.00"
    assert ledger.snapshot(NOW)["holdings"] == []
    assert len(ledger.snapshot(NOW)["equity_history"]) == 1


def test_fractional_fill_conserves_cash_and_realizes_decimal_pnl(ledger):
    apply_proposal(ledger, decision(ledger), proposal(), quotes(), NOW)
    first = ledger.snapshot(NOW)
    assert Decimal(first["account"]["cash_usd"]) >= Decimal("900")
    qty = first["holdings"][0]["quantity"]
    later = NOW + timedelta(seconds=30)
    next_quotes = {"SPY": Quote("SPY", Decimal("110"), later, fetched_at=later)}
    apply_proposal(
        ledger, decision(ledger, "d2"), proposal("sell", qty), next_quotes, later
    )
    snapshot = ledger.snapshot(later)
    assert snapshot["holdings"] == []
    assert Decimal(snapshot["account"]["cash_usd"]) == Decimal("1000") + Decimal(
        snapshot["account"]["realized_pnl_usd"]
    )
    assert len(snapshot["fills"]) == 2


@pytest.mark.parametrize(
    "bad",
    [
        proposal(amount="1001"),
        proposal("sell", "1"),
        proposal(amount="NaN"),
        proposal(amount="0"),
        proposal(symbol="BOGUS"),
    ],
)
def test_invalid_trades_never_change_ledger(ledger, bad):
    with pytest.raises(TradeError):
        apply_proposal(ledger, decision(ledger), bad, quotes(), NOW)
    assert ledger.snapshot(NOW)["account"]["cash_usd"] == "1000.00"
    assert ledger.snapshot(NOW)["fills"] == []


def test_bad_second_action_rolls_back_first(ledger):
    bad = proposal()
    bad["actions"].append(
        {"symbol": "AAPL", "side": "sell", "quantity": "1", "notional_usd": None}
    )
    with pytest.raises(TradeError):
        apply_proposal(ledger, decision(ledger), bad, quotes(), NOW)
    assert ledger.snapshot(NOW)["fills"] == []


@pytest.mark.parametrize("seconds", [-121, 1])
def test_stale_or_future_quote_cannot_fill(ledger, seconds):
    with pytest.raises(TradeError):
        apply_proposal(
            ledger,
            decision(ledger),
            proposal(),
            quotes(NOW + timedelta(seconds=seconds)),
            NOW,
        )


def test_expiry_stops_fills_and_freezes_observed_marks(ledger):
    apply_proposal(ledger, decision(ledger), proposal(), quotes(), NOW)
    expiry = NOW + timedelta(hours=168)
    with pytest.raises(TradeError):
        apply_proposal(
            ledger, decision(ledger, "late"), proposal(), quotes(expiry), expiry
        )
    before = ledger.snapshot(expiry)["account"]["equity_usd"]
    ledger.store_quotes(
        {
            "SPY": Quote(
                "SPY",
                Decimal("999"),
                expiry + timedelta(seconds=1),
                fetched_at=expiry + timedelta(seconds=1),
            )
        },
        {},
        expiry + timedelta(seconds=1),
    )
    assert (
        ledger.snapshot(expiry + timedelta(days=2))["account"]["equity_usd"] == before
    )


class FakeWorker:
    calls = 0

    async def start(self, workspace):
        pass

    async def propose(self, context):
        self.calls += 1
        return {
            "proposal": proposal(),
            "model": "gpt-6-astra",
            "effort": "high",
            "usage": {"total_tokens": 2},
            "verification": {
                "model_verified": True,
                "effort_verified": True,
                "tools_disabled": True,
                "provider": "openai",
                "cli_version": "codex-cli 0.159.2",
                "turn_id": "test-turn",
            },
        }

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_cycle_refetches_and_is_idempotent(ledger):
    fetches = []
    worker = FakeWorker()

    def fetch(symbols, **kwargs):
        fetches.append(tuple(symbols))
        return quotes(), {}

    engine = Engine(
        ledger, fetch=fetch, worker_factory=lambda: worker, clock=lambda: NOW
    )
    assert (await engine.run_once())["status"] == "filled"
    assert (await engine.run_once())["status"] == "duplicate"
    assert len(fetches) == 2
    assert worker.calls == 1
    assert len(ledger.snapshot(NOW)["fills"]) == 1


@pytest.mark.asyncio
async def test_post_model_stale_refetch_prevents_fill(ledger):
    count = 0

    def fetch(symbols, **kwargs):
        nonlocal count
        count += 1
        return quotes(NOW if count == 1 else NOW - timedelta(minutes=10)), {}

    engine = Engine(ledger, fetch=fetch, worker_factory=FakeWorker, clock=lambda: NOW)
    assert (await engine.run_once())["status"] == "rejected"
    assert ledger.snapshot(NOW)["fills"] == []


@pytest.mark.asyncio
async def test_closed_market_never_calls_model(ledger):
    worker = FakeWorker()
    engine = Engine(
        ledger,
        fetch=lambda *a, **k: ({}, {}),
        worker_factory=lambda: worker,
        clock=lambda: NOW + timedelta(days=1),
    )
    assert (await engine.run_once())["status"] == "market_closed"
    assert worker.calls == 0
