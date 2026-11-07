"""One randomly selected complete historical month, next-open daily paper fills."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import random
import time
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from .engine import validate_proposal

DATA = Path(__file__).resolve().parents[2] / "data/paper-market/aapl-2015-2017.csv"
SOURCE = (
    "https://raw.githubusercontent.com/plotly/datasets/master/finance-charts-apple.csv"
)


def load_rows(path=DATA):
    with Path(path).open() as handle:
        rows = [
            {
                "date": r["Date"],
                **{
                    k: r[f"AAPL.{k.title()}"]
                    for k in ("open", "high", "low", "close", "volume")
                },
            }
            for r in csv.DictReader(handle)
        ]
    if (
        not rows
        or rows != sorted(rows, key=lambda r: r["date"])
        or len({r["date"] for r in rows}) != len(rows)
    ):
        raise ValueError("Historical data must have unique chronological dates.")
    for row in rows:
        if date.fromisoformat(row["date"]) >= date(2026, 10, 28):
            raise ValueError("Only past historical observations are allowed.")
        values = [Decimal(row[k]) for k in ("open", "high", "low", "close", "volume")]
        if any(not v.is_finite() or v <= 0 for v in values):
            raise ValueError("Invalid historical observation.")
        if (
            not values[2]
            <= min(values[0], values[3])
            <= max(values[0], values[3])
            <= values[1]
        ):
            raise ValueError("Invalid historical OHLC range.")
    return rows


def money(value):
    return format(value.quantize(Decimal(".01")), "f")


async def run_replay(rows, decide, *, seed=42, progress=None, duration_seconds=0):
    rng = random.Random(seed)
    months = sorted({r["date"][:7] for r in rows[21:-23]})[1:-1]
    month = rng.choice(months)
    start = date.fromisoformat(month + "-01")
    end = start + timedelta(days=30)
    indices = [
        i for i, r in enumerate(rows) if start <= date.fromisoformat(r["date"]) < end
    ]
    cash, quantity = Decimal(1000), Decimal(0)
    decisions, correct, errors = [], 0, 0
    peak, drawdown = cash, Decimal(0)

    def report(status):
        final = cash + quantity * (
            Decimal(rows[indices[len(decisions) - 1]]["close"])
            if decisions
            else Decimal(0)
        )
        scored = len(decisions) - errors
        return {
            "status": status,
            "mode": "historical_paper_only",
            "model": "dots-studio/dots-3-note-preview:free",
            "seed": seed,
            "month": month,
            "start_date": start.isoformat(),
            "end_date": (end - timedelta(days=1)).isoformat(),
            "calendar_days": 30,
            "sessions_total": len(indices),
            "sessions_completed": len(decisions),
            "symbol": "AAPL",
            "source": SOURCE,
            "dataset_sha256": hashlib.sha256(
                json.dumps(rows, sort_keys=True).encode()
            ).hexdigest(),
            "initial_cash_usd": "1000.00",
            "final_equity_usd": money(final),
            "pnl_usd": money(final - 1000),
            "return_pct": money((final / 1000 - 1) * 100),
            "buy_hold_return_pct": money(
                (
                    Decimal(rows[indices[len(decisions) - 1]]["close"])
                    / Decimal(rows[indices[0]]["open"])
                    - 1
                )
                * 100
            )
            if decisions
            else "0.00",
            "max_drawdown_pct": money(drawdown),
            "correct_decisions": correct,
            "scored_decisions": scored,
            "decision_score_pct": round(correct / scored * 100, 2) if scored else None,
            "model_errors": errors,
            "scoring": "Actions beat unchanged holdings over next open-to-close, after slippage. Cash holds pass on nonpositive net long returns; stock holds pass on rising closes. Hindsight outcome score, not proof of reasoning. Final equity marks open positions at last close.",
            "limitations": "Historical prices may be memorized by the model. One stock and one month; no dividends, taxes or market impact. Daily replay differs from live intraday strategy.",
            "decisions": decisions,
        }

    if progress:
        progress(report("running"))
    started = time.monotonic()
    for session_index, i in enumerate(indices):
        if duration_seconds:
            await asyncio.sleep(
                max(
                    0,
                    started
                    + session_index * duration_seconds / max(1, len(indices) - 1)
                    - time.monotonic(),
                )
            )
        row = rows[i]
        # Only history through the PRIOR close is sent. Open/close stay in evaluator.
        history = rows[max(0, i - 21) : i]
        context = {
            "mode": "historical_daily_replay",
            "as_of": history[-1]["date"],
            "symbol": "AAPL",
            "allowed_symbols": ["AAPL"],
            "history": history,
            "cash_usd": money(cash),
            "holdings": {"AAPL": str(quantity)},
            "constraints": {
                "maximum_symbol_weight": ".35",
                "shorting": False,
                "leverage": False,
                "execution": "next session open; evaluate at close; 5-20bps random adverse slippage",
            },
        }
        slip = Decimal(rng.choice([5, 10, 20])) / 10000
        opening, closing = Decimal(row["open"]), Decimal(row["close"])
        before = cash + quantity * opening
        entry = {
            "date": row["date"],
            "as_of": history[-1]["date"],
            "slippage_bps": str(slip * 10000),
        }
        try:
            result = await decide(context)
            actions = validate_proposal(result["proposal"])
            proposed_cash, proposed_qty = cash, quantity
            for action in actions:
                if action["symbol"] != "AAPL":
                    raise ValueError("Replay accepts AAPL only.")
                if action["side"] == "buy":
                    spend = Decimal(action["notional_usd"])
                    qty = (spend / (opening * (1 + slip))).quantize(
                        Decimal(".000001"), rounding=ROUND_DOWN
                    )
                    cost = qty * opening * (1 + slip)
                    if spend > proposed_cash or (
                        proposed_qty + qty
                    ) * opening > before * Decimal(".35"):
                        raise ValueError("Replay cash or position limit exceeded.")
                    proposed_cash -= cost
                    proposed_qty += qty
                else:
                    qty = Decimal(action["quantity"])
                    if qty > proposed_qty:
                        raise ValueError("Replay cannot short.")
                    proposed_cash += qty * opening * (1 - slip)
                    proposed_qty -= qty
            cash, quantity = proposed_cash, proposed_qty
            # Compare chosen exposure to doing nothing for exactly this session.
            effect = (
                cash
                + quantity * closing
                - (before + Decimal(context["holdings"]["AAPL"]) * (closing - opening))
            )
            # Holds: correct if cash avoids a negative net long session, or held stock rises.
            if actions:
                good = effect > 0
            else:
                good = (
                    (closing > opening)
                    if quantity > 0
                    else (closing <= opening * (1 + slip))
                )
            correct += int(good)
            entry.update(
                actions=actions,
                rationale=result["proposal"]["rationale"],
                correct=good,
                incremental_pnl_usd=money(effect),
                usage=result.get("usage"),
            )
        except Exception as error:
            errors += 1
            entry.update(
                actions=[],
                correct=None,
                error=str(error)
                if isinstance(error, (ValueError, RuntimeError))
                else "Model unavailable or invalid response; no fill and no decision score.",
            )
        equity = cash + quantity * closing
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak * 100)
        entry["equity_usd"] = money(equity)
        decisions.append(entry)
        if progress:
            progress(report("running"))
    return report(
        "complete"
        if not errors
        else ("partial" if len(decisions) > errors else "failed")
    )
