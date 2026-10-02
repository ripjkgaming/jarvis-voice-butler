"""Durable Decimal ledger and read-only dashboard projection."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import DECISION_INTERVAL_SECONDS, EFFORT, FILL_POLICY, MODEL, SYMBOLS
from .calendar import session_at
from .feed import QuoteError, validate_mark_quote

UTC = timezone.utc
ZERO = Decimal("0")


def utcnow() -> datetime:
    return datetime.now(UTC)


def stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("An aware timestamp is required.")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def dollars(value: Decimal | None) -> str | None:
    return format(value.quantize(Decimal("0.01")), "f") if value is not None else None


def exact(value: Decimal) -> str:
    return format(value, "f")


def encoded(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock_path = self.path.with_suffix(".lock")
        with self.connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS experiment (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), id TEXT NOT NULL,
                    started_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    initial_cash TEXT NOT NULL, cash TEXT NOT NULL,
                    realized_pnl TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS holdings (
                    symbol TEXT PRIMARY KEY, quantity TEXT NOT NULL,
                    cost_basis TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS quotes (
                    symbol TEXT NOT NULL, quoted_at TEXT NOT NULL, price TEXT NOT NULL,
                    fetched_at TEXT NOT NULL, source TEXT NOT NULL, currency TEXT NOT NULL,
                    previous_close TEXT, PRIMARY KEY(symbol,quoted_at));
                CREATE TABLE IF NOT EXISTS feed_status (
                    symbol TEXT PRIMARY KEY, checked_at TEXT NOT NULL, error TEXT);
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY, at TEXT NOT NULL, completed_at TEXT,
                    kind TEXT NOT NULL, status TEXT NOT NULL, input_json TEXT NOT NULL,
                    proposal_json TEXT, rationale TEXT, error TEXT, model TEXT NOT NULL,
                    effort TEXT NOT NULL, usage_json TEXT, verification_json TEXT);
                CREATE TABLE IF NOT EXISTS fills (
                    id TEXT PRIMARY KEY, decision_id TEXT NOT NULL REFERENCES decisions(id),
                    at TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
                    quantity TEXT NOT NULL, price TEXT NOT NULL, notional TEXT NOT NULL,
                    realized_pnl TEXT NOT NULL, quote_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS equity_history (
                    at TEXT PRIMARY KEY, equity TEXT, cash TEXT NOT NULL,
                    total_pnl TEXT, valuation_complete INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS runtime (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """
            )
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self, now: datetime) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM experiment").fetchone()
            if existing is None:
                db.execute(
                    "INSERT INTO experiment VALUES(1,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        stamp(now),
                        stamp(now + timedelta(hours=168)),
                        "1000.00",
                        "1000.00",
                        "0",
                    ),
                )
                db.execute(
                    "INSERT INTO equity_history VALUES(?,?,?,?,?)",
                    (stamp(now), "1000.00", "1000.00", "0.00", 1),
                )
        return self.experiment()

    def experiment(self) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM experiment").fetchone()
            if row is None:
                raise RuntimeError("Paper experiment has not been initialized.")
            return dict(row)

    def runtime(self, **values) -> None:
        with self.connect() as db:
            db.executemany(
                "INSERT OR REPLACE INTO runtime VALUES(?,?)",
                [(key, encoded(value)) for key, value in values.items()],
            )

    def store_quotes(self, quotes: dict, errors: dict[str, str], now: datetime):
        expiry = datetime.fromisoformat(self.experiment()["expires_at"])
        if now >= expiry:
            return
        with self.connect() as db:
            for symbol, quote in quotes.items():
                # Feed parsing is not the only boundary: reject corrupted injected data.
                if (
                    symbol not in SYMBOLS
                    or quote.symbol != symbol
                    or quote.currency != "USD"
                ):
                    continue
                try:
                    validate_mark_quote(quote, now)
                except QuoteError:
                    continue
                previous = getattr(quote, "previous_close", None)
                db.execute(
                    "INSERT INTO quotes VALUES(?,?,?,?,?,?,?) ON CONFLICT(symbol,quoted_at) DO UPDATE SET price=excluded.price,fetched_at=excluded.fetched_at,source=excluded.source,currency=excluded.currency,previous_close=excluded.previous_close WHERE excluded.fetched_at>=quotes.fetched_at",
                    (
                        symbol,
                        stamp(quote.quoted_at),
                        exact(quote.price),
                        stamp(quote.fetched_at or now),
                        quote.source,
                        quote.currency,
                        exact(previous) if previous is not None else None,
                    ),
                )
                db.execute(
                    "INSERT OR REPLACE INTO feed_status VALUES(?,?,NULL)",
                    (symbol, stamp(now)),
                )
            for symbol, error in errors.items():
                if symbol in SYMBOLS:
                    db.execute(
                        "INSERT OR REPLACE INTO feed_status VALUES(?,?,?)",
                        (symbol, stamp(now), error[:300]),
                    )
        self.runtime(last_refresh_at=stamp(now))

    def begin_decision(
        self, identifier: str, now: datetime, context: dict, kind: str = "decision"
    ) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO decisions(id,at,kind,status,input_json,model,effort) VALUES(?,?,?,'running',?,?,?)",
                (identifier, stamp(now), kind, encoded(context), MODEL, EFFORT),
            )
            return cursor.rowcount == 1

    def finish_decision(
        self,
        identifier: str,
        now: datetime,
        status: str,
        *,
        result: dict | None = None,
        error: str | None = None,
    ):
        result = result or {}
        proposal = result.get("proposal")
        with self.connect() as db:
            db.execute(
                "UPDATE decisions SET completed_at=?,status=?,proposal_json=?,rationale=?,error=?,usage_json=?,verification_json=? WHERE id=? AND status='running'",
                (
                    stamp(now),
                    status,
                    encoded(proposal) if proposal is not None else None,
                    proposal.get("rationale") if isinstance(proposal, dict) else None,
                    error,
                    encoded(result.get("usage")),
                    encoded(result.get("verification")),
                    identifier,
                ),
            )

    def has_decision(self, identifier: str) -> bool:
        with self.connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM decisions WHERE id=?", (identifier,)
                ).fetchone()
                is not None
            )

    def recover_interrupted(self, now: datetime):
        # Called only while holding the process lock. A prior crashed proposal
        # cannot be retried in the same slot or leave the UI 'running' forever.
        with self.connect() as db:
            db.execute(
                "UPDATE decisions SET status='interrupted',completed_at=?,error=? WHERE status='running'",
                (stamp(now), "Previous decision process ended before completion."),
            )

    def record_equity(self, now: datetime):
        if now >= datetime.fromisoformat(self.experiment()["expires_at"]):
            return
        account = self.snapshot(now)["account"]
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO equity_history VALUES(?,?,?,?,?)",
                (
                    stamp(now),
                    account["equity_usd"],
                    account["cash_usd"],
                    account["total_pnl_usd"],
                    int(account["valuation_complete"]),
                ),
            )

    def snapshot(self, now: datetime | None = None) -> dict:
        now = now or utcnow()
        with self.connect() as db:
            db.execute("BEGIN")
            experiment = dict(db.execute("SELECT * FROM experiment").fetchone() or {})
            if not experiment:
                raise RuntimeError("Paper experiment has not been initialized.")
            expiry = datetime.fromisoformat(experiment["expires_at"])
            cutoff = min(now, expiry)
            market = session_at(now)
            quote_rows = db.execute(
                "SELECT q.* FROM quotes q JOIN (SELECT symbol, MAX(quoted_at) AS latest FROM quotes WHERE quoted_at<=? AND fetched_at<=? GROUP BY symbol) t ON q.symbol=t.symbol AND q.quoted_at=t.latest",
                (stamp(cutoff), stamp(cutoff)),
            ).fetchall()
            by_symbol = {row["symbol"]: dict(row) for row in quote_rows}
            feed_errors = {
                row["symbol"]: row["error"]
                for row in db.execute("SELECT * FROM feed_status")
            }
            quotes = []
            for symbol in SYMBOLS:
                row = by_symbol.get(symbol)
                age = (
                    (now - datetime.fromisoformat(row["quoted_at"])).total_seconds()
                    if row
                    else None
                )
                quotes.append(
                    {
                        "symbol": symbol,
                        "price_usd": row["price"] if row else None,
                        "quoted_at": row["quoted_at"] if row else None,
                        "age_seconds": age,
                        "source": row["source"]
                        if row
                        else "Yahoo Finance (unofficial)",
                        "status": "unavailable"
                        if not row
                        else "stale"
                        if age > 120
                        else "fresh",
                        "error": feed_errors.get(symbol),
                        "previous_close_usd": row["previous_close"] if row else None,
                    }
                )
            holdings = []
            value = ZERO
            basis = ZERO
            complete = True
            marks_at = []
            for row in db.execute("SELECT * FROM holdings ORDER BY symbol"):
                quantity, cost = Decimal(row["quantity"]), Decimal(row["cost_basis"])
                quote = by_symbol.get(row["symbol"])
                price = Decimal(quote["price"]) if quote else None
                marked = price * quantity if price is not None else None
                complete = complete and marked is not None
                value += marked or ZERO
                basis += cost
                if quote:
                    marks_at.append(quote["quoted_at"])
                holdings.append(
                    {
                        "symbol": row["symbol"],
                        "quantity": exact(quantity),
                        "average_cost_usd": exact(cost / quantity),
                        "cost_basis_usd": exact(cost),
                        "mark_price_usd": exact(price) if price is not None else None,
                        "market_value_usd": dollars(marked),
                        "unrealized_pnl_usd": dollars(marked - cost)
                        if marked is not None
                        else None,
                        "quote_at": quote["quoted_at"] if quote else None,
                    }
                )
            cash, initial = (
                Decimal(experiment["cash"]),
                Decimal(experiment["initial_cash"]),
            )
            equity = cash + value if complete else None
            pnl = equity - initial if equity is not None else None
            decisions = []
            for row in db.execute("SELECT * FROM decisions ORDER BY at DESC LIMIT 30"):
                proposal = (
                    json.loads(row["proposal_json"]) if row["proposal_json"] else {}
                )
                decisions.append(
                    {
                        "id": row["id"],
                        "at": row["at"],
                        "completed_at": row["completed_at"],
                        "kind": row["kind"],
                        "status": row["status"],
                        "rationale": row["rationale"],
                        "error": row["error"],
                        "model": row["model"],
                        "effort": row["effort"],
                        "actions": proposal.get("actions", []),
                        "usage": json.loads(row["usage_json"])
                        if row["usage_json"]
                        else None,
                        "verification": json.loads(row["verification_json"])
                        if row["verification_json"]
                        else None,
                    }
                )
            fills = [
                {
                    "id": row["id"],
                    "decision_id": row["decision_id"],
                    "at": row["at"],
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "quantity": row["quantity"],
                    "price_usd": row["price"],
                    "notional_usd": row["notional"],
                    "realized_pnl_usd": row["realized_pnl"],
                    "quote": json.loads(row["quote_json"]),
                }
                for row in db.execute(
                    "SELECT * FROM fills ORDER BY at DESC,id DESC LIMIT 100"
                )
            ]
            history = [
                {
                    "at": row["at"],
                    "equity_usd": row["equity"],
                    "cash_usd": row["cash"],
                    "total_pnl_usd": row["total_pnl"],
                    "valuation_complete": bool(row["valuation_complete"]),
                }
                for row in db.execute("SELECT * FROM equity_history ORDER BY at")
            ]
            runtime = {
                row["key"]: json.loads(row["value"])
                for row in db.execute("SELECT * FROM runtime")
            }
        return {
            "schema_version": 1,
            "generated_at": stamp(now),
            "experiment": {
                "id": experiment["id"],
                "started_at": experiment["started_at"],
                "expires_at": experiment["expires_at"],
                "initial_cash_usd": experiment["initial_cash"],
                "status": "expired" if now >= expiry else "active",
                "model": MODEL,
                "effort": EFFORT,
            },
            "account": {
                "cash_usd": dollars(cash),
                "equity_usd": dollars(equity),
                "total_pnl_usd": dollars(pnl),
                "total_return_pct": dollars(pnl / initial * 100)
                if pnl is not None
                else None,
                "realized_pnl_usd": dollars(Decimal(experiment["realized_pnl"])),
                "unrealized_pnl_usd": dollars(value - basis) if complete else None,
                "valuation_complete": complete,
                "marks_as_of": min(marks_at) if marks_at else None,
                "valuation_basis": "last observed Yahoo regular-market prices at or before expiry",
                "valuation_fresh": all(
                    (now - datetime.fromisoformat(at)).total_seconds() <= 120
                    for at in marks_at
                ),
            },
            "market": {
                "is_open": market.is_open,
                "status": market.status,
                "next_open_at": stamp(market.next_open_at)
                if market.next_open_at
                else None,
                "session_close_at": stamp(market.session_close_at)
                if market.session_close_at
                else None,
            },
            "quotes": quotes,
            "holdings": holdings,
            "equity_history": history,
            "decisions": decisions,
            "fills": fills,
            "runtime": {
                "last_cycle_at": None,
                "last_cycle_status": "not_started",
                "last_error": None,
                "last_refresh_at": None,
                "feed": "Yahoo Finance (unofficial)",
                "fill_policy": FILL_POLICY,
                "decision_interval_seconds": DECISION_INTERVAL_SECONDS,
                **runtime,
            },
        }
