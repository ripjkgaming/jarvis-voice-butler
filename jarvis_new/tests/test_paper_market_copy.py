from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from paper_market.copy_trader import (
    CopyTraderError,
    CopyTraderStore,
    FrozenSelection,
    TraderWeight,
    VerifiedSignal,
    copy_prompt_contract,
    proposal_for_snapshot,
    readiness,
)
from paper_market.feed import Quote

NOW = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)  # 10:00 ET, open


def selection():
    return FrozenSelection(
        frozen_at=NOW - timedelta(days=1),
        traders=(
            TraderWeight(
                "12345",
                "Reviewed Strategy",
                Decimal("100"),
                "Reviewed performance, drawdown, delay, and 3 years of history.",
            ),
        ),
        review_method="Reviewed completed equity, max drawdown, delay, and trade history; fixed equal weights before launch.",
    )


def signal(**kwargs):
    defaults = {
        "strategy_id": "12345",
        "trade_id": "trade-1",
        "symbol": "AAPL",
        "source_allocation_percent": Decimal("10"),
        "reported_at": NOW - timedelta(seconds=8),
        "received_at": NOW,
    }
    defaults.update(kwargs)
    return VerifiedSignal(**defaults)


def quote(symbol="AAPL", price="100"):
    return Quote(
        symbol,
        Decimal(price),
        NOW - timedelta(seconds=1),
        fetched_at=NOW - timedelta(seconds=1),
    )


def test_readiness_requires_actual_api_access_selection_and_history_review():
    result = readiness(selection=None)
    assert result["ready"] is False
    assert len(result["blockers"]) == 4
    assert result["fills_enabled"] is False

    ready = readiness(
        selection=selection(),
        api_key="configured-secret",
        authenticated=True,
        history_reviewed=True,
        model_verified=True,
    )
    assert ready["ready"] is True
    assert "configured-secret" not in repr(ready)
    assert (ready["model"], ready["effort"]) == ("gpt-6.1-sol", "medium")


def test_selection_weights_are_frozen_and_sum_to_one_hundred(tmp_path):
    with pytest.raises(CopyTraderError, match="sum exactly"):
        FrozenSelection(
            frozen_at=NOW,
            traders=(
                TraderWeight("12345", "One", Decimal("70"), "reviewed"),
                TraderWeight("23456", "Two", Decimal("20"), "reviewed"),
            ),
            review_method="performance and risk review",
        )

    store = CopyTraderStore(tmp_path / "copy.sqlite")
    store.freeze(selection())
    with pytest.raises(CopyTraderError, match="cannot be changed"):
        store.freeze(
            FrozenSelection(
                NOW, (TraderWeight("12345", "Changed", Decimal("100"), "new"),), "new"
            )
        )


def test_authenticated_envelope_and_supported_symbol_are_required():
    with pytest.raises(CopyTraderError, match="source envelope"):
        VerifiedSignal.from_api4(
            {"strategy_id": "12345"}, expected_strategy_id="12345", received_at=NOW
        )
    with pytest.raises(CopyTraderError, match="unsupported instrument"):
        signal(symbol="BTC-USD")
    with pytest.raises(CopyTraderError, match="cannot be after receipt"):
        signal(reported_at=NOW + timedelta(seconds=1))


def test_signal_ingestion_is_deduplicated_and_retains_reporting_delay(tmp_path):
    store = CopyTraderStore(tmp_path / "copy.sqlite")
    store.freeze(selection())
    observation = signal()
    assert store.ingest(observation, source_authenticated=True) is True
    assert store.ingest(observation, source_authenticated=True) is False
    allocation, provenance = store.latest_allocations()
    assert allocation[("12345", "AAPL")] == Decimal("10")
    assert provenance[0]["reporting_delay_seconds"] == 8


def test_copy_scaling_targets_fractional_100_dollar_position(tmp_path):
    store = CopyTraderStore(tmp_path / "copy.sqlite")
    store.freeze(selection())
    store.ingest(signal(), source_authenticated=True)

    class Snapshot:
        def snapshot(self, now):
            return {
                "account": {"cash_usd": "1000.00"},
                "holdings": [],
            }

    result = proposal_for_snapshot(store, Snapshot(), {"AAPL": quote()}, NOW)
    assert result["proposal"]["actions"] == [
        {"symbol": "AAPL", "side": "buy", "notional_usd": "100.00", "quantity": None}
    ]
    assert result["metadata"]["target_values_usd"] == {"AAPL": "100.00"}
    assert result["metadata"]["source_execution_prices_used_for_fills"] is False
    contract = copy_prompt_contract(selection(), store.latest_allocations()[1])
    assert (contract["model"], contract["effort"]) == ("gpt-6.1-sol", "medium")


def test_limits_and_missing_fresh_quotes_block_proposal(tmp_path):
    store = CopyTraderStore(tmp_path / "copy.sqlite")
    store.freeze(selection())
    store.ingest(
        signal(source_allocation_percent=Decimal("40")), source_authenticated=True
    )

    class Snapshot:
        def snapshot(self, now):
            return {"account": {"cash_usd": "1000.00"}, "holdings": []}

    with pytest.raises(CopyTraderError, match="35% position limit"):
        proposal_for_snapshot(store, Snapshot(), {"AAPL": quote()}, NOW)

    other = CopyTraderStore(tmp_path / "copy2.sqlite")
    other.freeze(selection())
    other.ingest(signal(), source_authenticated=True)
    with pytest.raises(CopyTraderError, match="no fresh quote"):
        proposal_for_snapshot(other, Snapshot(), {}, NOW)
