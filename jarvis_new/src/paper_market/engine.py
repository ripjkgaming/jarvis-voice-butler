"""One locked decision cycle; only this module may commit simulated fills."""

from __future__ import annotations

import asyncio
import fcntl
import re
import sqlite3
import tempfile
from contextlib import contextmanager, suppress
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from . import DECISION_INTERVAL_SECONDS, EFFORT, MAX_QUOTE_AGE_SECONDS, MODEL, SYMBOLS
from .calendar import session_at
from .feed import QuoteError, fetch_quotes, validate_quote
from .ledger import Ledger, dollars, encoded, exact, stamp, utcnow

ZERO = Decimal("0")
SLIPPAGE = Decimal("0.001")
QUANTITY_STEP = Decimal("0.000001")
MAX_WEIGHT = Decimal("0.35")
MAX_EXPOSURE = Decimal("0.95")


class TradeError(ValueError):
    pass


class CycleBusyError(RuntimeError):
    pass


@contextmanager
def cycle_lock(ledger: Ledger):
    with ledger.lock_path.open("a", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CycleBusyError(
                "Another paper-market cycle is already running."
            ) from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def positive(value, label: str) -> Decimal:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{1,10}(?:\.[0-9]{1,6})?", value
    ):
        raise TradeError(
            f"{label} must be a positive decimal string with at most six decimal places."
        )
    number = Decimal(value)
    if not number.is_finite() or number <= 0:
        raise TradeError(f"{label} must be positive and finite.")
    return number


def validate_proposal(proposal: dict) -> list[dict]:
    if not isinstance(proposal, dict) or set(proposal) != {"actions", "rationale"}:
        raise TradeError("Proposal must contain only actions and rationale.")
    rationale = proposal["rationale"]
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 2000:
        raise TradeError("Proposal rationale is missing or too long.")
    actions = proposal["actions"]
    if not isinstance(actions, list) or len(actions) > 4:
        raise TradeError("At most four paper actions are permitted per decision.")
    seen = set()
    for action in actions:
        if not isinstance(action, dict) or set(action) != {
            "symbol",
            "side",
            "notional_usd",
            "quantity",
        }:
            raise TradeError("Paper action has an invalid schema.")
        symbol, side = action["symbol"], action["side"]
        if symbol not in SYMBOLS or symbol in seen or side not in {"buy", "sell"}:
            raise TradeError("Action has a duplicate or unapproved symbol or side.")
        seen.add(symbol)
        if side == "buy":
            if action["quantity"] is not None:
                raise TradeError("Buy actions specify notional_usd only.")
            if positive(action["notional_usd"], "Buy notional") < 5:
                raise TradeError("Minimum simulated buy is $5.")
        else:
            if action["notional_usd"] is not None:
                raise TradeError("Sell actions specify quantity only.")
            positive(action["quantity"], "Sell quantity")
    return actions


def ensure_active(experiment: dict, now: datetime):
    if now < datetime.fromisoformat(experiment["started_at"]):
        raise TradeError("Clock precedes experiment start; fills are disabled.")
    if now >= datetime.fromisoformat(experiment["expires_at"]):
        raise TradeError("The seven-day paper experiment has expired.")
    if not session_at(now).is_open:
        raise TradeError("The regular US equity market session is closed.")


def quote_record(quote) -> dict:
    return {
        "symbol": quote.symbol,
        "price_usd": exact(quote.price),
        "quoted_at": stamp(quote.quoted_at),
        "fetched_at": stamp(quote.fetched_at) if quote.fetched_at else None,
        "source": quote.source,
        "currency": quote.currency,
        "slippage_bps": "10",
    }


def verify_worker_result(result: dict):
    from system.codex_worker import VERIFIED_VERSION

    verification = result.get("verification") if isinstance(result, dict) else None
    if (
        not isinstance(result, dict)
        or result.get("model") != MODEL
        or result.get("effort") != EFFORT
        or not isinstance(verification, dict)
        or any(
            verification.get(key) is not True
            for key in ("model_verified", "effort_verified", "tools_disabled")
        )
        or verification.get("provider") != "openai"
        or verification.get("cli_version") != VERIFIED_VERSION
        or not verification.get("turn_id")
    ):
        raise TradeError(
            "Trading worker did not verify exact GPT-6 Astra/high and proposal-only isolation."
        )


def apply_proposal(
    ledger: Ledger,
    identifier: str,
    proposal: dict,
    quotes: dict,
    now: datetime,
    *,
    clock=None,
    result: dict | None = None,
) -> list[dict]:
    """Validate the entire batch, then atomically write cash, shares and fills.

    The caller holds cycle_lock through inference/refetch/commit. This additional
    database transaction and decision status gate prevent accidental replays.
    """
    actions = validate_proposal(proposal)
    # Quote persistence can fail before a fill, never after a committed fill is
    # reported as rejected. Quotes alone do not alter cash or owned shares.
    ledger.store_quotes(quotes, {}, now)
    with ledger.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        experiment = dict(db.execute("SELECT * FROM experiment").fetchone())
        ensure_active(experiment, now)
        decision = db.execute(
            "SELECT status FROM decisions WHERE id=?", (identifier,)
        ).fetchone()
        if not decision or decision["status"] != "running":
            raise TradeError("Decision is missing or already completed.")
        if db.execute(
            "SELECT 1 FROM fills WHERE decision_id=?", (identifier,)
        ).fetchone():
            raise TradeError("Decision already has committed fills.")
        positions = {
            row["symbol"]: [Decimal(row["quantity"]), Decimal(row["cost_basis"])]
            for row in db.execute("SELECT * FROM holdings")
        }
        required = set(positions) | {action["symbol"] for action in actions}
        for symbol in required:
            if symbol not in quotes:
                raise TradeError(f"No fresh quote is available for {symbol}.")
            try:
                validate_quote(
                    quotes[symbol],
                    now,
                    max_age_seconds=MAX_QUOTE_AGE_SECONDS,
                    require_open=True,
                )
                if quotes[symbol].symbol != symbol:
                    raise QuoteError("Symbol mismatch.")
            except QuoteError:
                raise TradeError(
                    f"The {symbol} quote is stale, invalid, or outside the regular session."
                ) from None
        cash = Decimal(experiment["cash"])
        realized = Decimal(experiment["realized_pnl"])
        fills = []
        # Sell owned shares before funding buys, with no leverage even transiently.
        for index, action in enumerate(
            sorted(actions, key=lambda item: item["side"] != "sell")
        ):
            symbol, side = action["symbol"], action["side"]
            quote = quotes[symbol]
            price = quote.price * (1 + SLIPPAGE if side == "buy" else 1 - SLIPPAGE)
            quantity, basis = positions.get(symbol, [ZERO, ZERO])
            gain = ZERO
            if side == "sell":
                traded = positive(action["quantity"], "Sell quantity")
                if traded > quantity:
                    raise TradeError(f"Cannot sell unowned shares of {symbol}.")
                proceeds = traded * price
                # Carry exact residual basis; a full close consumes it all.
                sold_basis = basis if traded == quantity else basis * traded / quantity
                gain = proceeds - sold_basis
                realized += gain
                cash += proceeds
                quantity -= traded
                basis -= sold_basis
                notional = proceeds
            else:
                requested = positive(action["notional_usd"], "Buy notional")
                if requested > cash:
                    raise TradeError("Buy proposal exceeds available paper cash.")
                traded = (requested / price).quantize(
                    QUANTITY_STEP, rounding=ROUND_DOWN
                )
                if traded <= ZERO:
                    raise TradeError("Buy proposal is too small for a fractional fill.")
                notional = traded * price
                cash -= notional
                quantity += traded
                basis += notional
            if quantity == 0:
                positions.pop(symbol, None)
            else:
                positions[symbol] = [quantity, basis]
            if cash < ZERO:
                raise TradeError("Paper cash cannot be negative.")
            fills.append(
                {
                    "id": f"{identifier}:{index}",
                    "at": stamp(now),
                    "symbol": symbol,
                    "side": side,
                    "quantity": exact(traded),
                    "price_usd": exact(price),
                    "notional_usd": exact(notional),
                    "realized_pnl_usd": exact(gain),
                    "quote": quote_record(quote),
                }
            )
        market_value = sum(
            (
                quantity * quotes[symbol].price
                for symbol, (quantity, _) in positions.items()
            ),
            ZERO,
        )
        equity = cash + market_value
        bought = {action["symbol"] for action in actions if action["side"] == "buy"}
        for symbol in bought:
            if positions[symbol][0] * quotes[symbol].price > equity * MAX_WEIGHT:
                raise TradeError(
                    f"A buy would exceed the 35% position limit for {symbol}."
                )
        if bought and market_value > equity * MAX_EXPOSURE:
            raise TradeError("A buy would exceed the 95% portfolio exposure limit.")
        # Time can advance while waiting for SQLite or validating the batch.
        commit_now = clock() if clock is not None else now
        ensure_active(experiment, commit_now)
        for symbol in required:
            try:
                validate_quote(
                    quotes[symbol],
                    commit_now,
                    max_age_seconds=MAX_QUOTE_AGE_SECONDS,
                    require_open=True,
                )
            except QuoteError:
                raise TradeError(f"The {symbol} quote expired before commit.") from None
        db.execute(
            "UPDATE experiment SET cash=?,realized_pnl=? WHERE singleton=1",
            (exact(cash), exact(realized)),
        )
        db.execute("DELETE FROM holdings")
        db.executemany(
            "INSERT INTO holdings VALUES(?,?,?)",
            [
                (symbol, exact(quantity), exact(basis))
                for symbol, (quantity, basis) in positions.items()
            ],
        )
        for fill in fills:
            fill["at"] = stamp(commit_now)
            db.execute(
                "INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    fill["id"],
                    identifier,
                    fill["at"],
                    fill["symbol"],
                    fill["side"],
                    fill["quantity"],
                    fill["price_usd"],
                    fill["notional_usd"],
                    fill["realized_pnl_usd"],
                    encoded(fill["quote"]),
                ),
            )
        result = result or {}
        db.execute(
            "UPDATE decisions SET status=?,completed_at=?,proposal_json=?,rationale=?,usage_json=?,verification_json=? WHERE id=?",
            (
                "filled" if fills else "held",
                stamp(commit_now),
                encoded(proposal),
                proposal["rationale"],
                encoded(result.get("usage")),
                encoded(result.get("verification")),
                identifier,
            ),
        )
        db.execute(
            "INSERT OR REPLACE INTO equity_history VALUES(?,?,?,?,1)",
            (
                stamp(commit_now),
                dollars(equity),
                dollars(cash),
                dollars(equity - Decimal(experiment["initial_cash"])),
            ),
        )
        # SQLite writes may wait on I/O. Recheck at the transaction boundary,
        # after all inserts; raising here rolls the entire batch back.
        final_now = clock() if clock is not None else now
        ensure_active(experiment, final_now)
        for symbol in required:
            try:
                validate_quote(
                    quotes[symbol],
                    final_now,
                    max_age_seconds=MAX_QUOTE_AGE_SECONDS,
                    require_open=True,
                )
            except QuoteError:
                raise TradeError(
                    f"The {symbol} quote expired at the commit boundary."
                ) from None
    return fills


class Engine:
    def __init__(
        self, ledger: Ledger, *, fetch=fetch_quotes, worker_factory=None, clock=utcnow
    ):
        self.ledger = ledger
        self.fetch = fetch
        self.worker_factory = worker_factory
        self.clock = clock

    async def _fetch(self, symbols):
        # The real feed timestamps receipt, not request start. Injected clocks
        # belong to test feed functions, otherwise valid newer ticks look future.
        return await asyncio.to_thread(self.fetch, symbols)

    async def run_once(self) -> dict:
        try:
            with cycle_lock(self.ledger):
                return await self._run_locked()
        except CycleBusyError:
            return {
                "status": "busy",
                "message": "Another paper-market cycle is running.",
            }

    def _status(self, status, error=None, **extra):
        response = {"status": status, **({"error": error} if error else {}), **extra}
        try:
            self.ledger.runtime(
                last_cycle_at=stamp(self.clock()),
                last_cycle_status=status,
                last_error=error,
            )
        except (sqlite3.Error, OSError):
            response["warning"] = (
                "Runtime status could not be saved; committed ledger records remain authoritative."
            )
        return response

    async def _run_locked(self) -> dict:
        now = self.clock()
        self.ledger.recover_interrupted(now)
        experiment = self.ledger.experiment()
        if now >= datetime.fromisoformat(experiment["expires_at"]):
            return self._status("expired", expires_at=experiment["expires_at"])
        if now < datetime.fromisoformat(experiment["started_at"]):
            return self._status("clock_error", "Clock precedes experiment start.")
        market = session_at(now)
        if not market.is_open:
            return self._status(
                "market_closed",
                next_open_at=stamp(market.next_open_at)
                if market.next_open_at
                else None,
            )
        slot = int(now.timestamp()) // DECISION_INTERVAL_SECONDS
        identifier = f"{experiment['id']}:{slot}"
        if self.ledger.has_decision(identifier):
            return {"status": "duplicate", "decision_id": identifier}
        quotes, errors = await self._fetch(SYMBOLS)
        now = self.clock()
        self.ledger.store_quotes(quotes, errors, now)
        fresh = {}
        for symbol, quote in quotes.items():
            try:
                validate_quote(
                    quote, now, max_age_seconds=MAX_QUOTE_AGE_SECONDS, require_open=True
                )
                fresh[symbol] = quote
            except QuoteError:
                continue
        snapshot = self.ledger.snapshot(now)
        held_symbols = {holding["symbol"] for holding in snapshot["holdings"]}
        if not fresh or held_symbols - fresh.keys():
            return self._status(
                "quotes_unavailable",
                "Fresh regular-session quotes are required before making a decision.",
            )
        try:
            ensure_active(experiment, now)
        except TradeError as error:
            return self._status("skipped", str(error))
        context = {
            "mode": "paper_trading",
            "as_of": stamp(now),
            "experiment": snapshot["experiment"],
            "account": snapshot["account"],
            "holdings": snapshot["holdings"],
            "quotes": [
                quote_record(quote)
                | {
                    "previous_close_usd": exact(quote.previous_close)
                    if getattr(quote, "previous_close", None)
                    else None
                }
                for quote in fresh.values()
            ],
            "allowed_symbols": list(fresh),
            "recent_decisions": snapshot["decisions"][:5],
            "constraints": {
                "maximum_actions": 4,
                "maximum_symbol_weight": "0.35",
                "maximum_total_exposure": "0.95",
                "minimum_buy_usd": "5",
                "fractional_quantity_decimals": 6,
                "shorting": False,
                "leverage": False,
                "slippage_bps": "10",
                "commission_usd": "0",
            },
            "data_limitations": "Quotes are an unofficial best-effort feed. No news or fundamentals are supplied. Prefer holding when evidence is insufficient. This is simulated capital, not a model inference budget.",
        }
        if not self.ledger.begin_decision(identifier, now, context):
            return {"status": "duplicate", "decision_id": identifier}
        self.ledger.runtime(
            last_cycle_at=stamp(now), last_cycle_status="running", last_error=None
        )
        worker = None
        result = None
        try:
            if self.worker_factory is None:
                from .worker import PaperMarketWorker

                worker = PaperMarketWorker()
            else:
                worker = self.worker_factory()
            with tempfile.TemporaryDirectory(
                prefix="proposal-", dir=self.ledger.path.parent
            ) as workspace:
                await worker.start(Path(workspace))
                ensure_active(experiment, self.clock())
                result = await worker.propose(context)
            verify_worker_result(result)
            actions = validate_proposal(result.get("proposal"))
            ensure_active(experiment, self.clock())
            # A distinct fetch after the model response is mandatory even for a
            # hold, so the cycle's mark records current observed data.
            refreshed, errors = await self._fetch(
                set(held_symbols) | {action["symbol"] for action in actions}
                or set(fresh)
            )
            now = self.clock()
            self.ledger.store_quotes(refreshed, errors, now)
            fills = apply_proposal(
                self.ledger,
                identifier,
                result["proposal"],
                refreshed,
                now,
                clock=self.clock,
                result=result,
            )
            status = "filled" if fills else "held"
            return self._status(status, decision_id=identifier, fills=len(fills))
        except Exception as error:
            # Only our explicitly sanitized errors leave the process. Credentials
            # and arbitrary transport response bodies never reach logs/the UI.
            from system.codex_worker import CodexWorkerError

            message = (
                str(error)
                if isinstance(error, (TradeError, CodexWorkerError))
                else "Paper decision failed before completion; no unvalidated trade was accepted."
            )
            status = "rejected" if isinstance(error, TradeError) else "error"
            self.ledger.finish_decision(
                identifier, self.clock(), status, result=result, error=message
            )
            return self._status(status, message, decision_id=identifier)
        finally:
            if worker is not None:
                try:
                    await worker.close()
                except Exception:
                    # A process cleanup error cannot undo or relabel a fill.
                    with suppress(sqlite3.Error, OSError):
                        self.ledger.runtime(
                            worker_cleanup_error="Worker cleanup failed after the decision ended."
                        )

    async def preflight(self) -> dict:
        """One auditable no-trade live model entitlement/isolation check."""
        try:
            with cycle_lock(self.ledger):
                identifier = f"{self.ledger.experiment()['id']}:preflight"
                context = {
                    "mode": "preflight",
                    "instruction": "Return actions=[] and a short rationale confirming a proposal-only paper-market preflight. Do not propose any trade.",
                    "allowed_symbols": list(SYMBOLS),
                }
                if not self.ledger.begin_decision(
                    identifier, self.clock(), context, "preflight"
                ):
                    return {"status": "duplicate", "decision_id": identifier}
                from .worker import PaperMarketWorker

                worker = PaperMarketWorker(timeout=120)
                result = None
                try:
                    with tempfile.TemporaryDirectory(
                        prefix="preflight-", dir=self.ledger.path.parent
                    ) as workspace:
                        await worker.start(Path(workspace))
                        result = await worker.propose(context)
                    verify_worker_result(result)
                    if validate_proposal(result["proposal"]):
                        raise TradeError(
                            "Preflight must confirm exact model/effort and propose no trades."
                        )
                    self.ledger.finish_decision(
                        identifier, self.clock(), "preflight_passed", result=result
                    )
                    self.ledger.runtime(
                        preflight_status="passed", preflight_at=stamp(self.clock())
                    )
                    return {
                        "status": "preflight_passed",
                        "model": MODEL,
                        "effort": EFFORT,
                        "verification": result.get("verification"),
                        "usage": result.get("usage"),
                    }
                except Exception as error:
                    from system.codex_worker import CodexWorkerError

                    message = (
                        str(error)
                        if isinstance(error, (TradeError, CodexWorkerError))
                        else "Paper worker preflight failed. No fallback or trade was attempted."
                    )
                    self.ledger.finish_decision(
                        identifier,
                        self.clock(),
                        "preflight_failed",
                        result=result,
                        error=message,
                    )
                    self.ledger.runtime(preflight_status="failed", last_error=message)
                    return {"status": "preflight_failed", "error": message}
                finally:
                    await worker.close()
        except CycleBusyError:
            return {"status": "busy"}


def refresh(ledger: Ledger, *, fetch=fetch_quotes, clock=utcnow) -> dict:
    """Read quotes and mark existing holdings; this path never creates a model."""
    try:
        with cycle_lock(ledger):
            now = clock()
            if now >= datetime.fromisoformat(ledger.experiment()["expires_at"]):
                return {"status": "expired"}
            quotes, errors = fetch(SYMBOLS)
            now = clock()
            ledger.store_quotes(quotes, errors, now)
            ledger.record_equity(now)
            return {"status": "refreshed", "quotes": len(quotes), "errors": len(errors)}
    except CycleBusyError:
        return {"status": "busy"}
