from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from test_paper_market_engine import NOW, decision, proposal, quotes
from test_paper_market_feed import payload

from paper_market import feed
from paper_market.engine import Engine, TradeError, apply_proposal
from paper_market.ledger import Ledger
from paper_market.strategy import intraday_context


@pytest.fixture
def ledger(tmp_path):
    result = Ledger(tmp_path / "ledger.sqlite3")
    result.initialize(NOW)
    return result


def chart():
    data = payload()
    row = data["chart"]["result"][0]
    row["timestamp"] = [
        int((NOW - timedelta(minutes=20 - i)).timestamp()) for i in range(20)
    ]
    row["indicators"] = {
        "quote": [
            {
                "open": [250 + i * 0.1 for i in range(20)],
                "high": [251 + i * 0.1 for i in range(20)],
                "low": [249 + i * 0.1 for i in range(20)],
                "close": [250.5 + i * 0.1 for i in range(20)],
                "volume": [1000] * 20,
            }
        ]
    }
    return data


def test_parses_completed_bars_and_derives_actual_intraday_evidence():
    q = feed.parse_chart_quote(chart(), "AAPL", now=NOW)
    assert len(q.bars) == 20
    signal = intraday_context(q, NOW)
    assert signal["status"] == "ready"
    assert Decimal(signal["momentum_15m_pct"]) > 0
    assert Decimal(signal["vwap_usd"]) > 249
    assert len(signal["recent_bars"]) == 20


def test_missing_and_invalid_bars_never_invent_trend():
    q = feed.parse_chart_quote(payload(), "AAPL", now=NOW)
    assert intraday_context(q, NOW)["status"] == "unavailable"
    data = chart()
    data["chart"]["result"][0]["indicators"]["quote"][0]["volume"] = [-1] * 20
    q = feed.parse_chart_quote(data, "AAPL", now=NOW)
    assert q.bars == ()


def test_prior_session_and_incomplete_bars_are_excluded():
    q = feed.parse_chart_quote(chart(), "AAPL", now=NOW)
    assert intraday_context(q, NOW + timedelta(days=3))["status"] == "unavailable"
    assert len(intraday_context(q, NOW - timedelta(seconds=30))["recent_bars"]) == 19


@pytest.mark.asyncio
async def test_stop_loss_is_paper_only_audited_and_does_not_call_model(ledger):
    apply_proposal(ledger, decision(ledger), proposal(), quotes(), NOW)
    later = NOW + timedelta(minutes=1)
    prices = {
        "SPY": replace(
            quotes()["SPY"], price=Decimal("98"), quoted_at=later, fetched_at=later
        )
    }
    engine = Engine(
        ledger,
        fetch=lambda symbols: (prices, {}),
        clock=lambda: later,
        worker_factory=lambda: pytest.fail("Risk exit must not invoke model"),
    )
    result = await engine.risk_once()
    assert result["status"] == "filled"
    snapshot = ledger.snapshot(later)
    assert not snapshot["holdings"]
    assert snapshot["decisions"][0]["kind"] == "risk_exit"
    assert "stop loss" in snapshot["decisions"][0]["rationale"]
    assert len(snapshot["fills"]) == 2
    assert (await engine.risk_once())["status"] == "no_risk_exit"


@pytest.mark.asyncio
async def test_session_close_exit_and_stale_quote_failure(ledger):
    apply_proposal(ledger, decision(ledger), proposal(), quotes(), NOW)
    late = NOW.replace(hour=19, minute=51)
    engine = Engine(ledger, fetch=lambda symbols: (quotes(), {}), clock=lambda: late)
    assert (await engine.risk_once())["status"] == "quotes_unavailable"
    assert len(ledger.snapshot(late)["holdings"]) == 1
    engine.fetch = lambda symbols: (quotes(late), {})
    assert (await engine.risk_once())["status"] == "filled"
    assert not ledger.snapshot(late)["holdings"]


def test_new_entries_blocked_near_session_close(ledger):
    late = NOW.replace(hour=19, minute=31)
    with pytest.raises(TradeError, match="last 30 minutes"):
        apply_proposal(ledger, decision(ledger), proposal(), quotes(late), late)


def test_autotrading_toggle_persists(ledger):
    assert ledger.autotrading_enabled() is False
    ledger.runtime(autotrading_enabled=True)
    assert ledger.autotrading_enabled() is True
    ledger.runtime(autotrading_enabled=False)
    assert ledger.autotrading_enabled() is False


@pytest.mark.asyncio
async def test_missing_bars_and_no_setup_skip_paid_model(ledger):
    def worker():
        pytest.fail("No paid model for missing or weak signals")

    q = replace(quotes()["SPY"], bars=())
    engine = Engine(
        ledger,
        fetch=lambda symbols: ({"SPY": q}, {}),
        clock=lambda: NOW,
        worker_factory=worker,
    )
    assert (await engine.run_once())["status"] == "warming_up"
    q = replace(
        quotes()["SPY"], bars=tuple({**b, "close": "99"} for b in quotes()["SPY"].bars)
    )
    assert (await engine.run_once())["status"] == "no_setup"
    assert ledger.snapshot(NOW)["decisions"] == []


@pytest.mark.asyncio
async def test_model_receives_compact_signals_and_final_size_is_authoritative(ledger):
    from test_paper_market_atomic import Worker

    worker = Worker()
    original = worker.propose

    async def inspect(context):
        assert context["intraday"]["SPY"]["status"] == "ready"
        assert "recent_bars" not in context["intraday"]["SPY"]
        assert "observed_bars" not in context
        assert context["entry_candidates"] == ["SPY"]
        return await original(context)

    worker.propose = inspect
    engine = Engine(
        ledger,
        fetch=lambda symbols: (quotes(), {}),
        clock=lambda: NOW,
        worker_factory=lambda: worker,
    )
    assert (await engine.run_once())["status"] == "filled"
    assert worker.calls == 1
    assert Decimal(ledger.snapshot(NOW)["fills"][0]["notional_usd"]) <= 100
    assert (await engine.run_once())["status"] == "duplicate"
    assert worker.calls == 1


@pytest.mark.parametrize(
    "bad", [None, {}, {"quote": {}}, {"quote": [None]}, {"quote": "bad"}]
)
def test_malformed_bar_envelope_preserves_valid_last_price(bad):
    data = payload()
    data["chart"]["result"][0]["timestamp"] = [int(NOW.timestamp())]
    data["chart"]["result"][0]["indicators"] = bad
    q = feed.parse_chart_quote(data, "AAPL", now=NOW)
    assert q.price == Decimal("253.25")
    assert not q.bars
