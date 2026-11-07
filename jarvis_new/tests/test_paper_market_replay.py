import asyncio
import json

import pytest

from paper_market.openrouter_worker import OpenRouterWorker
from paper_market.replay import load_rows, run_replay


def test_exact_model_and_no_fallback():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            return json.dumps({"model": "wrong/model", "choices": []}).encode()

    def opener(request, timeout):
        payload = json.loads(request.data)
        assert payload["model"] == "dots-studio/dots-3-note-preview:free"
        assert "tools" not in payload
        return Response()

    worker = OpenRouterWorker(opener=opener, key="secret-test")
    with pytest.raises(RuntimeError, match="model"):
        asyncio.run(worker.propose({"mode": "preflight"}))


def test_replay_only_reveals_prior_days_and_reproduces():
    async def decide(context):
        assert all(bar["date"] <= context["as_of"] for bar in context["history"])
        return {"proposal": {"actions": [], "rationale": "Hold for test."}}

    first = asyncio.run(run_replay(load_rows(), decide, seed=42))
    second = asyncio.run(run_replay(load_rows(), decide, seed=42))
    assert first == second
    assert 19 <= len(first["decisions"]) <= 23
    assert first["final_equity_usd"] == "1000.00"
    assert first["model_errors"] == 0
    assert first["decision_score_pct"] is not None
    assert all(d["as_of"] < d["date"] for d in first["decisions"])


def test_model_failure_is_unscored():
    async def fail(context):
        raise RuntimeError("unavailable")

    result = asyncio.run(run_replay(load_rows(), fail, seed=42))
    assert result["status"] == "failed"
    assert result["decision_score_pct"] is None
    assert result["model_errors"] == len(result["decisions"])


def test_replay_fills_next_open_with_costs_and_outcome_score():
    from decimal import ROUND_DOWN, Decimal

    rows = load_rows()
    calls = 0

    async def buy_once(context):
        nonlocal calls
        calls += 1
        actions = (
            [{"symbol": "AAPL", "side": "buy", "notional_usd": "100", "quantity": None}]
            if calls == 1
            else []
        )
        return {
            "proposal": {"actions": actions, "rationale": "Test bounded allocation."}
        }

    report = asyncio.run(run_replay(rows, buy_once, seed=42))
    first = report["decisions"][0]
    bar = next(r for r in rows if r["date"] == first["date"])
    slip = Decimal(first["slippage_bps"]) / 10000
    quantity = (Decimal(100) / (Decimal(bar["open"]) * (1 + slip))).quantize(
        Decimal(".000001"), rounding=ROUND_DOWN
    )
    cash = Decimal(1000) - quantity * Decimal(bar["open"]) * (1 + slip)
    assert first["equity_usd"] == format(
        (cash + quantity * Decimal(bar["close"])).quantize(Decimal(".01")), "f"
    )
    assert first["correct"] == (
        Decimal(bar["close"]) > Decimal(bar["open"]) * (1 + slip)
    )
    assert report["calendar_days"] == 30
    assert abs(float(report["buy_hold_return_pct"])) < 100


def test_progress_publishes_initial_and_final_portfolio():
    reports = []

    async def hold(context):
        return {"proposal": {"actions": [], "rationale": "Hold."}}

    result = asyncio.run(
        run_replay(load_rows(), hold, progress=lambda r: reports.append(r.copy()))
    )
    assert reports[0]["sessions_completed"] == 0
    assert reports[0]["final_equity_usd"] == "1000.00"
    assert reports[-1]["sessions_completed"] == result["sessions_total"]
