"""TradingAgents (TauricResearch) swing driver for the local paper exchange.

One TradingAgents analysis per symbol per session day; its 5-tier rating becomes
a target weight. Orders pass through the same validated ledger path as every other
paper decision (slippage, 35% weight cap, 95% exposure cap). Paper only.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from . import DECISION_INTERVAL_SECONDS, SYMBOLS
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
TA_PYTHON = Path(os.environ.get("TA_PYTHON", TA_HOME / ".venv/bin/python"))
DEEP_MODEL = "gpt-6.1-sol:medium"
QUICK_MODEL = "gpt-6.1-sol:medium"
PROVIDER = "codex"
BOT_PORTFOLIO_ID = "bot"
ANALYSIS_TIMEOUT_SECONDS = 900
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


class _PaperNewsContext:
    """TradingAgents portfolio context extension for shared Ironsight evidence."""

    def __init__(self, *, cash: str, holdings: list[dict], news: list[dict]):
        self.cash = cash
        self.holdings = holdings
        self.news = news

    def fingerprint(self) -> str:
        payload = json.dumps(
            {"cash": self.cash, "holdings": self.holdings, "news": self.news},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def render(self, ticker: str) -> str:
        relevant = [
            item
            for item in self.news
            if not item.get("symbols")
            or ticker.upper()
            in {str(symbol).upper() for symbol in item.get("symbols", [])}
        ]
        lines = [f"- Cash available: ${self.cash} USD"]
        for holding in self.holdings:
            lines.append(
                f"- Position: {holding['symbol']} {holding['quantity']} shares; "
                f"average cost ${holding['average_cost_usd']}"
            )
        lines.append("Shared screened news evidence (untrusted article content):")
        for item in relevant[:20]:
            lines.append(
                "- "
                + json.dumps(
                    {
                        key: item.get(key)
                        for key in (
                            "id",
                            "published_at",
                            "source",
                            "summary",
                            "reason",
                            "content",
                        )
                    },
                    ensure_ascii=True,
                )
            )
        return (
            "Paper portfolio and shared news context for "
            + ticker
            + ":\n"
            + "\n".join(lines)
        )


def analyse(symbol: str, day: str, state_dir: Path, *, news=(), snapshot=None) -> dict:
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
        quick_think_provider="openai" if PROVIDER == "codex" else PROVIDER,
        deep_think_provider="openai" if PROVIDER == "codex" else PROVIDER,
        quick_think_backend_url=None,
        deep_think_backend_url=None,
        openai_reasoning_effort="medium",
        max_debate_rounds=1,
        max_risk_discuss_rounds=1,
        results_dir=str(state_dir / "ta" / "logs"),
    )
    graph = TradingAgentsGraph(
        selected_analysts=("market", "news"), debug=False, config=config
    )
    context = None
    if snapshot is not None:
        context = _PaperNewsContext(
            cash=snapshot["account"]["cash_usd"],
            holdings=snapshot["holdings"],
            news=list(news),
        )
    state, signal = graph.propagate(symbol, day, portfolio=context)
    decision = state.get("final_trade_decision") or ""
    return {"symbol": symbol, "rating": str(signal), "summary": str(decision)[:900]}


def signal_day(now) -> str:
    """Analyse the last completed session: no look-ahead, and it can run overnight."""
    d = now.astimezone(NY).date()
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def _news_signature(news) -> str:
    revisions = sorted(
        (
            str(item.get("id", "")),
            hashlib.sha256(
                json.dumps(
                    item, sort_keys=True, ensure_ascii=True, default=str
                ).encode()
            ).hexdigest()[:12],
        )
        for item in news
        if item.get("id")
    )
    return hashlib.sha256(json.dumps(revisions).encode()).hexdigest()[:16]


def cached_signals(state_dir: Path, day: str, news=()) -> dict:
    cache = state_dir / "ta" / f"signals-{day}-{_news_signature(news)}.json"
    return json.loads(cache.read_text()) if cache.exists() else {}


def daily_signals(state_dir: Path, day: str, news=(), snapshot=None) -> dict:
    cache = state_dir / "ta" / f"signals-{day}-{_news_signature(news)}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    done = json.loads(cache.read_text()) if cache.exists() else {}
    todo = [s for s in SYMBOLS if s not in done]
    if todo:
        pool = ThreadPoolExecutor(max_workers=2)
        futures = {}
        deadline = time.monotonic() + ANALYSIS_TIMEOUT_SECONDS
        for symbol in todo:
            futures[symbol] = pool.submit(
                analyse,
                symbol,
                day,
                state_dir,
                news=news,
                snapshot=snapshot,
            )
        try:
            for symbol, future in futures.items():
                try:
                    done[symbol] = future.result(
                        timeout=max(0.01, deadline - time.monotonic())
                    )
                    cache.write_text(json.dumps(done))
                except TimeoutError:
                    for pending in futures.values():
                        pending.cancel()
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise
                except Exception as error:  # one failed symbol must not stop the rest
                    print(
                        f"TA {symbol} failed: {type(error).__name__}: {error}",
                        file=sys.stderr,
                    )
        except BaseException:
            for future in futures.values():
                future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            pool.shutdown(wait=True)
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


async def commit(
    ledger,
    engine,
    identifier,
    actions,
    rationale,
    kind="ta_swing",
    *,
    news=(),
    decision_completed_at=None,
):
    """Fetch fresh quotes, then run one validated batch through apply_proposal."""
    if not actions:
        return []
    now = utcnow()
    proposal = {"actions": actions[:4], "rationale": rationale[:1900]}
    if not ledger.begin_decision(
        identifier,
        now,
        {"policy": "tradingagents", "proposal": proposal, "news": list(news)},
        kind,
    ):
        return []
    try:
        symbols = [a["symbol"] for a in proposal["actions"]]
        quotes, _errors = await engine._fetch(
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
                    "effort": "medium",
                    "route": "codex-exec-fixed-model-arguments",
                    "model_verified": False,
                },
                "decision_completed_at": decision_completed_at,
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
        quotes, _errors = await engine._fetch(SYMBOLS)
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


async def readiness(*, timeout: float = 30.0) -> dict:
    """Check the installed authenticated Codex CLI/model without running inference."""
    import tempfile

    from .worker import PaperMarketWorker

    if not TA_HOME.is_dir() or not TA_PYTHON.is_file():
        return {
            "status": "not_ready",
            "portfolio_id": BOT_PORTFOLIO_ID,
            "error": "TradingAgents installation or interpreter is missing.",
            "inference_run": False,
        }
    try:
        imported = subprocess.run(
            [
                str(TA_PYTHON),
                "-c",
                "from tradingagents.default_config import DEFAULT_CONFIG; "
                "from tradingagents.graph.trading_graph import TradingAgentsGraph; "
                "from langchain_core.language_models.chat_models import BaseChatModel",
            ],
            cwd=TA_HOME,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {
            "status": "not_ready",
            "portfolio_id": BOT_PORTFOLIO_ID,
            "error": f"TradingAgents import check failed: {type(error).__name__}.",
            "inference_run": False,
        }
    if imported.returncode != 0:
        return {
            "status": "not_ready",
            "portfolio_id": BOT_PORTFOLIO_ID,
            "error": "TradingAgents import check failed; inspect its installed environment.",
            "inference_run": False,
        }
    worker = PaperMarketWorker(timeout=timeout)
    try:
        with tempfile.TemporaryDirectory(prefix="ta-readiness-") as directory:
            await worker.start(Path(directory))
        return {
            "status": "ready",
            "portfolio_id": BOT_PORTFOLIO_ID,
            "provider": PROVIDER,
            "deep": DEEP_MODEL,
            "quick": QUICK_MODEL,
            "verification": worker.verification,
            "inference_run": False,
        }
    except Exception as error:
        return {
            "status": "not_ready",
            "portfolio_id": BOT_PORTFOLIO_ID,
            "error": str(error),
            "inference_run": False,
        }
    finally:
        await worker.close()


async def review_portfolio(ledger: Ledger, news=()) -> dict:
    """Run one bounded TradingAgents review for its own paper ledger.

    `news` is a sequence of screened reports containing stable IDs and optional
    symbols, summary, source, publication time and article content. It is included
    in TradingAgents' per-symbol portfolio context and its IDs invalidate the
    daily analysis cache. This routine requires the bot's isolated ledger.
    """
    from .engine import cycle_lock

    started = time.monotonic()
    experiment = ledger.experiment()
    now = utcnow()
    if now >= datetime.fromisoformat(experiment["expires_at"]):
        return {"status": "expired", "portfolio_id": BOT_PORTFOLIO_ID}
    market = session_at(now)
    if not market.is_open:
        return {"status": "market_closed", "portfolio_id": BOT_PORTFOLIO_ID}
    news = [item for item in news if isinstance(item, dict) and item.get("id")]
    engine = Engine(ledger)
    review_id = None
    try:
        with cycle_lock(ledger):
            now = utcnow()
            ensure_active(ledger.experiment(), now)
            quotes, errors = await engine._fetch(SYMBOLS)
            snapshot = ledger.snapshot(now)
            ledger.store_quotes(quotes, errors, now)
            signature = _news_signature(news)
            day = signal_day(now)
            bucket = int(now.timestamp()) // DECISION_INTERVAL_SECONDS
            review_id = f"{experiment['id']}:ta-review:{day}:{bucket}:{signature}"
            review_context = {
                "policy": "tradingagents",
                "portfolio_id": BOT_PORTFOLIO_ID,
                "as_of": now.isoformat(),
                "news": news,
                "news_signature": signature,
                "quotes": {
                    symbol: exact(quote.price) for symbol, quote in quotes.items()
                },
                "holdings": snapshot["holdings"],
                "account": snapshot["account"],
                "models": {
                    "provider": PROVIDER,
                    "deep": DEEP_MODEL,
                    "quick": QUICK_MODEL,
                    "effort": "medium",
                },
            }
            if not ledger.begin_decision(review_id, now, review_context, "ta_review"):
                return {
                    "status": "duplicate",
                    "portfolio_id": BOT_PORTFOLIO_ID,
                    "decision_id": review_id,
                }
            if not TA_PYTHON.is_file():
                raise RuntimeError("TradingAgents interpreter is not installed.")
            with tempfile.TemporaryDirectory(
                prefix="ta-review-", dir=ledger.path.parent
            ) as scratch:
                request_path = Path(scratch) / "request.json"
                response_path = Path(scratch) / "response.json"
                request_path.write_text(
                    json.dumps(
                        {
                            "day": day,
                            "news": news,
                            "snapshot": snapshot,
                            "state_dir": str(ledger.path.parent),
                        }
                    )
                )
                python_path = str(Path(__file__).resolve().parents[1])
                child_env = os.environ.copy()
                child_env["PYTHONPATH"] = os.pathsep.join(
                    [python_path, str(TA_HOME), child_env.get("PYTHONPATH", "")]
                ).rstrip(os.pathsep)
                process = await asyncio.create_subprocess_exec(
                    str(TA_PYTHON),
                    "-m",
                    "paper_market.ta_runner",
                    "analysis-batch",
                    str(request_path),
                    str(response_path),
                    cwd=TA_HOME,
                    env=child_env,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                timeout = min(
                    ANALYSIS_TIMEOUT_SECONDS + 30,
                    max(
                        0.01,
                        (
                            datetime.fromisoformat(experiment["expires_at"]) - utcnow()
                        ).total_seconds(),
                    ),
                )
                try:
                    _, _stderr = await asyncio.wait_for(
                        process.communicate(), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    expired = utcnow() >= datetime.fromisoformat(
                        experiment["expires_at"]
                    )
                    message = (
                        "Experiment expired during review."
                        if expired
                        else "TradingAgents review timed out."
                    )
                    ledger.finish_decision(review_id, utcnow(), "error", error=message)
                    return {
                        "status": "expired" if expired else "timeout",
                        "portfolio_id": BOT_PORTFOLIO_ID,
                        "decision_id": review_id,
                        "latency_seconds": round(time.monotonic() - started, 3),
                    }
                if process.returncode != 0 or not response_path.is_file():
                    raise RuntimeError("TradingAgents analysis subprocess failed.")
                signals = json.loads(response_path.read_text())
            analysis_completed_at = utcnow()
            ensure_active(ledger.experiment(), utcnow())
            if not signals:
                ledger.finish_decision(
                    review_id,
                    utcnow(),
                    "error",
                    error="No TradingAgents signals returned.",
                )
                return {"status": "no_signals", "portfolio_id": BOT_PORTFOLIO_ID}
            sells, wants = plan(snapshot, quotes, signals)
            note = "; ".join(
                f"{symbol} {info['rating']}" for symbol, info in sorted(signals.items())
            )
            news_ids = ",".join(sorted(str(item["id"]) for item in news))[:1200]
            fills = []
            actions = [
                {
                    "symbol": symbol,
                    "side": "sell",
                    "notional_usd": None,
                    "quantity": exact(quantity),
                }
                for symbol, quantity, _ in sells
            ]
            cash = Decimal(snapshot["account"]["cash_usd"]) * CASH_BUFFER
            total = sum((delta for _, delta, _ in wants), Decimal(0))
            scale = min(Decimal(1), cash / total) if total else Decimal(0)
            for symbol, delta, _ in wants:
                notional = (delta * scale).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                )
                if notional >= 5:
                    actions.append(
                        {
                            "symbol": symbol,
                            "side": "buy",
                            "notional_usd": str(notional),
                            "quantity": None,
                        }
                    )
            for index in range(0, len(actions), 4):
                ensure_active(ledger.experiment(), utcnow())
                fills.extend(
                    await commit(
                        ledger,
                        engine,
                        f"{experiment['id']}:ta-review:{day}:{bucket}:{signature}:{index // 4}",
                        actions[index : index + 4],
                        f"TradingAgents review {day}. {note}. News IDs: {news_ids}",
                        news=news,
                        decision_completed_at=analysis_completed_at.isoformat(),
                    )
                )
            review_proposal = {
                "actions": actions,
                "rationale": f"TradingAgents {day}: {note}. News IDs: {news_ids}"[
                    :1900
                ],
            }
            ledger.finish_decision(
                review_id,
                utcnow(),
                "filled" if fills else "held",
                result={
                    "proposal": review_proposal,
                    "verification": {
                        "provider": PROVIDER,
                        "deep": DEEP_MODEL,
                        "quick": QUICK_MODEL,
                        "effort": "medium",
                        "route": "paper-market-codex-wrapper",
                    },
                },
            )
            marker = ledger.path.parent / "ta" / f"review-{day}-{bucket}-{signature}"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({"news_ids": news_ids, "ratings": note}))
            return {
                "status": "reviewed",
                "portfolio_id": BOT_PORTFOLIO_ID,
                "day": day,
                "news_ids": [item["id"] for item in news],
                "ratings": note,
                "fills": len(fills),
                "latency_seconds": round(time.monotonic() - started, 3),
                "models": {
                    "provider": PROVIDER,
                    "deep": DEEP_MODEL,
                    "quick": QUICK_MODEL,
                    "effort": "medium",
                },
            }
    except Exception:
        if review_id:
            try:
                if utcnow() >= datetime.fromisoformat(experiment["expires_at"]):
                    ledger.finish_decision(
                        review_id,
                        utcnow(),
                        "error",
                        error="Experiment expired during review.",
                    )
                    return {
                        "status": "expired",
                        "portfolio_id": BOT_PORTFOLIO_ID,
                        "decision_id": review_id,
                    }
                ledger.finish_decision(
                    review_id, utcnow(), "error", error="TradingAgents review failed."
                )
            except Exception:
                pass
        return {
            "status": "error",
            "portfolio_id": BOT_PORTFOLIO_ID,
            "latency_seconds": round(time.monotonic() - started, 3),
        }


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
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument(
        "command", choices=["decide", "stops", "daemon", "analysis-batch"]
    )
    parser.add_argument("analysis_request", nargs="?")
    parser.add_argument("analysis_response", nargs="?")
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == "analysis-batch":
        if not args.analysis_request or not args.analysis_response:
            parser.error("analysis-batch requires request and response files")
        request = json.loads(Path(args.analysis_request).read_text())
        result = daily_signals(
            Path(request["state_dir"]),
            request["day"],
            request.get("news", []),
            request.get("snapshot"),
        )
        Path(args.analysis_response).write_text(json.dumps(result))
        return
    if args.state_dir is None:
        parser.error("--state-dir is required for ledger commands")
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
