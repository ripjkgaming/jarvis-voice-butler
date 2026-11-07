"""TradingAgents (TauricResearch) swing driver for the local paper exchange.

One TradingAgents analysis per symbol per session day; its 5-tier rating becomes
a target weight. Orders pass through the same validated ledger path as every other
paper decision (slippage, 35% weight cap, 95% exposure cap). Paper only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from . import SYMBOLS
from .calendar import session_at
from .engine import (
    Engine,
    TradeError,
    apply_proposal,
    cycle_lock,
    ensure_active,
    refresh,
)
from .ledger import Ledger, exact, utcnow
from .strategy import ENTRY_CUTOFF_SECONDS

TA_HOME = Path(
    os.environ.get("TA_REPO", Path.home() / "tradingagents-sim/TradingAgents")
)
DEEP_MODEL = os.environ.get("TA_DEEP_MODEL", "gpt-6.1-sol:medium")
QUICK_MODEL = os.environ.get("TA_QUICK_MODEL", "gpt-6.1-sol:low")
PROVIDER = os.environ.get("TA_PROVIDER", "codex")
# Rating -> target share of equity. Hold keeps the current position.
TARGETS = {
    "Buy": Decimal("0.30"),
    "Overweight": Decimal("0.20"),
    "Underweight": Decimal("0.05"),
    "Sell": Decimal("0"),
}
STOP_LOSS = Decimal(
    "0.05"
)  # swing stop; the 1% intraday stop would churn this strategy
MIN_TRADE = Decimal("15")
CASH_BUFFER = Decimal("0.995")
ENTRY_AFTER_OPEN_SECONDS = 15 * 60
NY = ZoneInfo("America/New_York")


def analyse(symbol: str, day: str, state_dir: Path) -> dict:
    """Run the full TradingAgents graph for one symbol. Returns rating + summary."""
    sys.path.insert(0, str(TA_HOME))
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    if PROVIDER == "codex":
        from .codex_llm import install

        install()
    config = dict(DEFAULT_CONFIG)
    config.update(
        llm_provider="openai" if PROVIDER == "codex" else PROVIDER,
        deep_think_llm=DEEP_MODEL,
        quick_think_llm=QUICK_MODEL,
        max_debate_rounds=1,
        max_risk_discuss_rounds=1,
        results_dir=str(state_dir / "ta" / "logs"),
    )
    graph = TradingAgentsGraph(
        selected_analysts=("market", "news"), debug=False, config=config
    )
    state, signal = graph.propagate(symbol, day)
    decision = state.get("final_trade_decision") or ""
    return {"symbol": symbol, "rating": str(signal), "summary": str(decision)[:900]}


def signal_day(now) -> str:
    """Analyse the last completed session: no look-ahead, and it can run overnight."""
    d = now.astimezone(NY).date()
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def cached_signals(state_dir: Path, day: str) -> dict:
    cache = state_dir / "ta" / f"signals-{day}.json"
    return json.loads(cache.read_text()) if cache.exists() else {}


def daily_signals(state_dir: Path, day: str) -> dict:
    cache = state_dir / "ta" / f"signals-{day}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    done = json.loads(cache.read_text()) if cache.exists() else {}
    todo = [s for s in SYMBOLS if s not in done]
    if todo:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {s: pool.submit(analyse, s, day, state_dir) for s in todo}
            for symbol, future in futures.items():
                try:
                    done[symbol] = future.result()
                    cache.write_text(json.dumps(done))
                except Exception as error:  # one failed symbol must not stop the rest
                    print(
                        f"TA {symbol} failed: {type(error).__name__}: {error}",
                        file=sys.stderr,
                    )
        cache.write_text(json.dumps(done))
    return done


def plan(snapshot: dict, quotes: dict, signals: dict) -> tuple[list, list]:
    """Return (sell actions, buy actions) moving toward rating targets."""
    equity = Decimal(snapshot["account"]["equity_usd"])
    held = {h["symbol"]: Decimal(h["quantity"]) for h in snapshot["holdings"]}
    sells, wants = [], []
    for symbol, info in signals.items():
        target = TARGETS.get(info["rating"])
        if target is None or symbol not in quotes:
            continue
        price = quotes[symbol].price
        value = held.get(symbol, Decimal(0)) * price
        delta = target * equity - value
        if delta < -MIN_TRADE:
            quantity = (
                held[symbol]
                if target == 0
                else (
                    (-delta / price).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
                )
            )
            if quantity > 0:
                sells.append((symbol, min(quantity, held[symbol]), info))
        elif delta > MIN_TRADE:
            wants.append((symbol, delta, info))
    # Strongest conviction first.
    wants.sort(key=lambda w: (signals[w[0]]["rating"] != "Buy", -w[1]))
    return sells, wants


async def commit(ledger, engine, identifier, actions, rationale, kind="ta_swing"):
    """Fetch fresh quotes, then run one validated batch through apply_proposal."""
    if not actions:
        return []
    now = utcnow()
    proposal = {"actions": actions[:4], "rationale": rationale[:1900]}
    if not ledger.begin_decision(
        identifier, now, {"policy": "tradingagents", "proposal": proposal}, kind
    ):
        return []
    try:
        symbols = [a["symbol"] for a in proposal["actions"]]
        quotes, errors = await engine._fetch(
            symbols + [h["symbol"] for h in ledger.snapshot(now)["holdings"]]
        )
        now = utcnow()
        fills = apply_proposal(
            ledger,
            identifier,
            proposal,
            quotes,
            now,
            clock=utcnow,
            result={
                "usage": None,
                "verification": {
                    "provider": "tradingagents",
                    "deep": DEEP_MODEL,
                    "quick": QUICK_MODEL,
                },
            },
        )
        return fills
    except Exception as error:
        message = (
            str(error)
            if isinstance(error, TradeError)
            else "TA decision failed; nothing committed."
        )
        ledger.finish_decision(identifier, utcnow(), "rejected", error=message)
        print(f"{identifier}: {message}", file=sys.stderr)
        return []


async def decide(ledger: Ledger) -> dict:
    engine = Engine(ledger)
    now = utcnow()
    market = session_at(now)
    if not market.is_open:
        return {"status": "market_closed"}
    ensure_active(ledger.experiment(), now)
    if (now - market.session_open_at).total_seconds() < ENTRY_AFTER_OPEN_SECONDS:
        return {"status": "waiting_for_open_settle"}
    if (market.session_close_at - now).total_seconds() <= ENTRY_CUTOFF_SECONDS:
        return {"status": "entry_window_closed"}
    day = now.astimezone(NY).date().isoformat()
    experiment_id = ledger.experiment()["id"]
    signals = cached_signals(ledger.path.parent, signal_day(now))
    if len(signals) < 4:
        return {"status": "waiting_for_signals", "count": len(signals)}
    marker = ledger.path.parent / "ta" / f"decided-{day}"
    if marker.exists() and int(marker.read_text().split("|")[0]) >= len(signals):
        return {"status": "already_decided"}
    with cycle_lock(ledger):
        now = utcnow()
        quotes, errors = await engine._fetch(SYMBOLS)
        snapshot = ledger.snapshot(now)
        sells, wants = plan(snapshot, quotes, signals)
        note = "; ".join(f"{s} {i['rating']}" for s, i in sorted(signals.items()))
        fills = []
        batch = 0
        if sells:
            actions = [
                {
                    "symbol": s,
                    "side": "sell",
                    "notional_usd": None,
                    "quantity": exact(q),
                }
                for s, q, _ in sells
            ]
            for i in range(0, len(actions), 4):
                fills += await commit(
                    ledger,
                    engine,
                    f"{experiment_id}:ta:{day}:{len(signals)}:s{batch}",
                    actions[i : i + 4],
                    f"TradingAgents {day} trims/exits. {note}",
                )
                batch += 1
        if wants:
            snapshot = ledger.snapshot(utcnow())
            cash = Decimal(snapshot["account"]["cash_usd"]) * CASH_BUFFER
            total = sum(d for _, d, _ in wants)
            scale = min(Decimal(1), cash / total) if total else Decimal(0)
            actions = []
            for s, d, _ in wants:
                notional = (d * scale).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                if notional >= 5:
                    actions.append(
                        {
                            "symbol": s,
                            "side": "buy",
                            "notional_usd": str(notional),
                            "quantity": None,
                        }
                    )
            for i in range(0, len(actions), 4):
                fills += await commit(
                    ledger,
                    engine,
                    f"{experiment_id}:ta:{day}:{len(signals)}:b{batch}",
                    actions[i : i + 4],
                    f"TradingAgents {day} entries. {note}",
                )
                batch += 1
    marker.write_text(f"{len(signals)}|{note}")
    return {"status": "decided", "day": day, "fills": len(fills), "ratings": note}


async def stop_check(ledger: Ledger) -> dict:
    engine = Engine(ledger)
    with cycle_lock(ledger):
        now = utcnow()
        holdings = ledger.snapshot(now)["holdings"]
        if not holdings or not session_at(now).is_open:
            return {"status": "idle"}
        quotes, _ = await engine._fetch([h["symbol"] for h in holdings])
        actions = []
        for h in holdings:
            q = quotes.get(h["symbol"])
            if q and q.price / Decimal(h["average_cost_usd"]) - 1 <= -STOP_LOSS:
                actions.append(
                    {
                        "symbol": h["symbol"],
                        "side": "sell",
                        "notional_usd": None,
                        "quantity": h["quantity"],
                    }
                )
        fills = await commit(
            ledger,
            engine,
            f"{ledger.experiment()['id']}:tastop:{int(now.timestamp()) // 60}",
            actions,
            "TradingAgents swing stop-loss (5%).",
            "risk_exit",
        )
        return {"status": "stopped" if fills else "ok"}


def analysis_loop(state_dir: Path) -> None:
    while True:
        try:
            daily_signals(state_dir, signal_day(utcnow()))
        except Exception as error:
            print(f"analysis error: {type(error).__name__}: {error}", flush=True)
        time.sleep(900)


def daemon(ledger: Ledger) -> None:
    threading.Thread(
        target=analysis_loop, args=(ledger.path.parent,), daemon=True
    ).start()
    while True:
        now = utcnow()
        if now >= datetime.fromisoformat(ledger.experiment()["expires_at"]):
            print("experiment expired; exiting", flush=True)
            return
        try:
            refresh(ledger)
            print(json.dumps(asyncio.run(stop_check(ledger))), flush=True)
            print(json.dumps(asyncio.run(decide(ledger))), flush=True)
        except Exception as error:
            print(f"cycle error: {type(error).__name__}: {error}", flush=True)
        time.sleep(60 if session_at(utcnow()).is_open else 600)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("command", choices=["decide", "stops", "daemon"])
    args = parser.parse_args()
    os.umask(0o077)
    ledger = Ledger(args.state_dir.expanduser().resolve() / "ledger.sqlite3")
    ledger.experiment()
    if args.command == "decide":
        print(json.dumps(asyncio.run(decide(ledger)), indent=2))
    elif args.command == "stops":
        print(json.dumps(asyncio.run(stop_check(ledger)), indent=2))
    else:
        daemon(ledger)


if __name__ == "__main__":
    main()
