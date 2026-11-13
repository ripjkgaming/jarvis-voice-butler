"""Shared lifecycle around three isolated copies of the established paper ledger.

Preparing never starts a ledger. A durable launch intent fixes the deadline before
account creation, so recovering a partial launch cannot extend the measured week.
Legacy state and every completed comparison remain untouched.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from . import FILL_POLICY, SYMBOLS
from .calendar import session_at
from .ledger import Ledger, dollars, stamp, utcnow

PORTFOLIOS = (
    ("codex", "Codex Trader", "codex"),
    ("copy", "Trader Copy", "verified_copy"),
    ("bot", "Your Trading Bot", "tradingagents"),
)
SETTINGS = {
    "currency": "USD",
    "symbols": list(SYMBOLS),
    "fractional_shares": True,
    "initial_cash_usd": "1000.00",
    "duration_hours": 168,
    "maximum_position_weight": "0.35",
    "maximum_exposure": "0.95",
    "leverage": False,
    "shorting": False,
    "quote_max_age_seconds": 120,
    "quote_source": "Yahoo Finance (unofficial)",
    "slippage_bps": "10",
    "fees_usd": "0",
    "fill_policy": FILL_POLICY,
    "trading_model": "gpt-6.1-sol",
    "trading_effort": "medium",
    "screening_model": "gpt-6-luna",
    "screening_effort": "low",
    "display_timezone": "Asia/Singapore",
    "exchange_timezone": "America/New_York",
    "benchmark": "SPY",
    "corporate_actions": "unsupported; affected results incomplete",
    "exits": {
        "codex": "1% stop, 2% take profit, flatten final 10 minutes",
        "copy": "verified source allocation changes only",
        "bot": "existing TradingAgents ratings and 5% swing stop",
    },
}


class ReadinessError(RuntimeError):
    pass


def atomic_json(path: Path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, allow_nan=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    os.chmod(path, 0o600)
    descriptor = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Comparison:
    def __init__(self, state_dir: Path, *, readiness=None):
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.root = self.state_dir / "comparison"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.manifest_path = self.root / "current.json"
        self._readiness = readiness

    @property
    def exists(self):
        return self.manifest_path.exists()

    @contextmanager
    def lock(self):
        with (self.root / "lifecycle.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def manifest(self):
        return json.loads(self.manifest_path.read_text())

    def save(self, manifest):
        atomic_json(self.root / manifest["id"] / "manifest.json", manifest)
        atomic_json(self.manifest_path, manifest)

    def prepare(self, *, new=False):
        with self.lock():
            if self.exists:
                previous = self.manifest()
                if not new:
                    return previous
                if previous["status"] != "expired":
                    raise ReadinessError(
                        "Finish the current comparison before preparing another."
                    )
            manifest = {
                "id": str(uuid.uuid4()),
                "status": "preparing",
                "started_at": None,
                "expires_at": None,
                "settings": SETTINGS,
                "created_at": stamp(utcnow()),
            }
            self.save(manifest)
            return manifest

    def gate_reason(self, key="copy"):
        """Why a portfolio cannot launch, from its own readiness evidence."""
        path = self.root / f"readiness-{key}.json"
        evidence = json.loads(path.read_text()) if path.exists() else {}
        if evidence.get("ready") is True:
            return None
        blockers = " ".join(evidence.get("blockers") or [])
        if "credential" in blockers.lower():
            return "blocked: needs credentials"
        return "blocked: " + (
            (evidence.get("blockers") or ["verification pending"])[0].rstrip(".")
        )

    def readiness(self, gated=()):
        if self._readiness:
            return self._readiness()
        checks = {}
        blockers = []
        # Runtime integrations must supply their own evidence; configuration alone
        # is never treated as successful source access or an authenticated model run.
        for name in ("news", "models", "copy", "bot", "trial"):
            if name in gated:
                checks[name] = {
                    "ready": False,
                    "gated": True,
                    "blockers": [self.gate_reason(name)],
                }
                continue
            path = self.root / f"readiness-{name}.json"
            evidence = json.loads(path.read_text()) if path.exists() else {}
            passed = evidence.get("ready") is True
            checks[name] = evidence or {"ready": False}
            if not passed:
                blockers.extend(
                    evidence.get("blockers")
                    or [f"{name}: integration verification pending"]
                )
        return {"ready": not blockers, "checks": checks, "blockers": blockers}

    def manifest_gates(self):
        try:
            return self.manifest().get("gated") or {}
        except FileNotFoundError:
            return {}

    def active_keys(self):
        gated = self.manifest_gates()
        return [key for key, _, _ in PORTFOLIOS if key not in gated]

    def start(self, now=None, *, gate=()):
        """Launch the measured week. ``gate`` names portfolios that stay blocked.

        A gated portfolio gets no ledger and never fills; it is reported as
        blocked with the reason from its own readiness evidence.
        """
        now = now or utcnow()
        with self.lock():
            manifest = self.manifest()
            if manifest["status"] in {"active", "expired"}:
                return manifest
            if manifest["status"] != "starting":
                gate = tuple(gate)
                if not set(gate) <= {key for key, _, _ in PORTFOLIOS}:
                    raise ReadinessError("Unknown portfolio in gate list.")
                if self._readiness is None:
                    # Only a portfolio whose own check fails may be gated.
                    for key in gate:
                        if self.gate_reason(key) is None:
                            raise ReadinessError(
                                f"{key} is ready; it cannot be gated out of the launch."
                            )
                    manifest["gated"] = {
                        key: [self.gate_reason(key)]
                        for key in gate
                        if self.gate_reason(key)
                    }
                elif gate:
                    manifest["gated"] = {key: ["blocked: gated"] for key in gate}
                readiness = self.readiness(gated=set(gate))
                if not readiness["ready"]:
                    raise ReadinessError("; ".join(readiness["blockers"]))
                manifest.update(
                    status="starting",
                    started_at=stamp(now),
                    expires_at=stamp(now + timedelta(hours=168)),
                    readiness=readiness,
                )
                self.save(manifest)
            # Recover the original launch intent, including its exact start time.
            started = datetime.fromisoformat(manifest["started_at"])
            for key in self.active_keys():
                ledger = Ledger(self.root / manifest["id"] / key / "ledger.sqlite3")
                experiment = ledger.initialize(
                    started, identifier=f"{manifest['id']}:{key}"
                )
                if (
                    experiment["started_at"] != manifest["started_at"]
                    or experiment["expires_at"] != manifest["expires_at"]
                ):
                    raise ReadinessError(
                        "Partial launch account does not match the fixed deadline."
                    )
                ledger.runtime(
                    comparison_id=manifest["id"],
                    portfolio_id=key,
                    autotrading_enabled=True,
                    effective_model="gpt-6.1-sol",
                    effective_effort="medium",
                )
            manifest["status"] = "active"
            self.save(manifest)
            return manifest

    def ledgers(self):
        manifest = self.manifest()
        if manifest["started_at"] is None:
            return {}
        return {
            key: Ledger(self.root / manifest["id"] / key / "ledger.sqlite3")
            for key in self.active_keys()
        }

    def _news_status(self):
        manifest = self.manifest()
        database = self.root / manifest["id"] / "news.sqlite3"
        if database.exists():
            from .news import NewsStore

            status = NewsStore(database).runner_status()
            if status:
                return status
        path = self.root / "readiness-news.json"
        return (
            json.loads(path.read_text())
            if path.exists()
            else {"ready": False, "status": "discovery_pending"}
        )

    def snapshot(self, now=None):
        now = now or utcnow()
        manifest = self.manifest()
        directory = self.root / manifest["id"]
        final = directory / "final.json"
        if final.exists():
            return json.loads(final.read_text())
        portfolios = []
        market = session_at(now)
        ledgers = self.ledgers()
        gated = manifest.get("gated") or {}
        for key, name, strategy in PORTFOLIOS:
            if key in ledgers:
                snapshot = ledgers[key].snapshot(now)
            else:
                snapshot = {
                    "schema_version": 1,
                    "experiment": {
                        "id": f"{manifest['id']}:{key}",
                        "status": "preparing",
                        "started_at": None,
                        "expires_at": None,
                        "initial_cash_usd": "1000.00",
                        "model": "gpt-6.1-sol",
                        "effort": "medium",
                    },
                    "account": {
                        "cash_usd": "1000.00",
                        "equity_usd": "1000.00",
                        "total_pnl_usd": "0.00",
                        "total_return_pct": "0.00",
                        "realized_pnl_usd": "0.00",
                        "unrealized_pnl_usd": "0.00",
                        "starting_balance_usd": "1000.00",
                        "invested_value_usd": "0.00",
                        "maximum_drawdown_pct": "0.00",
                        "trade_count": 0,
                        "valuation_complete": True,
                        "valuation_fresh": True,
                        "marks_as_of": None,
                    },
                    "holdings": [],
                    "fills": [],
                    "decisions": [],
                    "equity_history": [],
                    "quotes": [],
                    "runtime": {
                        "last_cycle_at": None,
                        "last_cycle_status": "not_started",
                        "last_error": None,
                        "last_refresh_at": None,
                        "feed": "Yahoo Finance (unofficial)",
                        "fill_policy": FILL_POLICY,
                        "decision_interval_seconds": 300,
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
                }
            extra = {}
            if key in gated:
                reason = gated[key][0]
                snapshot["experiment"]["status"] = "blocked"
                snapshot["runtime"].update(
                    last_cycle_status="blocked", last_error=reason
                )
                extra = {"blocked": True, "blocked_reason": reason}
            portfolios.append(
                {"id": key, "name": name, "strategy": strategy, **extra, **snapshot}
            )
        benchmark = self.benchmark(portfolios, manifest)
        return {
            **manifest,
            "status": "expired"
            if manifest["expires_at"]
            and now >= datetime.fromisoformat(manifest["expires_at"])
            else manifest["status"],
            "generated_at": stamp(now),
            "remaining_seconds": max(
                0,
                (datetime.fromisoformat(manifest["expires_at"]) - now).total_seconds(),
            )
            if manifest["expires_at"]
            else None,
            "readiness": manifest.get("readiness") or self.readiness(),
            "news": self._news_status(),
            "portfolios": portfolios,
            "equity_history": [],
            "benchmark": benchmark,
            "gaps": self.gaps(),
            "corporate_actions_status": "incomplete: corporate actions are not modeled",
            "usage_costs": self.usage_costs(portfolios),
            "stage_latency": self.stage_latency(),
        }

    def usage_costs(self, portfolios):
        """Token totals per portfolio over all decisions; never a guessed USD."""
        per = {}
        ledgers = self.ledgers()
        for portfolio in portfolios:
            totals = {"decisions": 0, "total": 0, "input": 0, "output": 0}
            ledger = ledgers.get(portfolio["id"])
            if ledger is not None:
                with ledger.connect() as db:
                    rows = db.execute(
                        "SELECT usage_json FROM decisions WHERE usage_json IS NOT NULL"
                    ).fetchall()
                for row in rows:
                    last = (json.loads(row[0]) or {}).get("last") or {}
                    if last:
                        totals["decisions"] += 1
                        totals["total"] += int(last.get("totalTokens") or 0)
                        totals["input"] += int(last.get("inputTokens") or 0)
                        totals["output"] += int(last.get("outputTokens") or 0)
            per[portfolio["id"]] = totals
        return {
            "included_in_returns": False,
            "usd": None,
            "status": "provider monetary cost unavailable; token usage only",
            "tokens_by_portfolio": per,
        }

    def stage_latency(self):
        manifest = self.manifest()
        database = self.root / manifest["id"] / "news.sqlite3"
        if not database.exists():
            return {}
        from .news import NewsStore

        return NewsStore(database).latency_summary()

    def benchmark(self, portfolios, manifest):
        history = []
        if manifest["started_at"]:
            ledger = self.ledgers()["codex"]
            with ledger.connect() as db:
                rows = db.execute(
                    "SELECT * FROM quotes WHERE symbol='SPY' AND quoted_at>=? AND fetched_at<=? ORDER BY fetched_at,quoted_at",
                    (manifest["started_at"], manifest["expires_at"]),
                ).fetchall()
            if rows:
                initial = Decimal(rows[0]["price"])
                history = [
                    {
                        "at": row["fetched_at"],
                        "equity_usd": dollars(
                            Decimal("1000") * Decimal(row["price"]) / initial
                        ),
                    }
                    for row in rows
                ]
        return {
            "symbol": "SPY",
            "status": "observed" if history else "awaiting_valid_observation",
            "equity_history": history,
            "policy": "USD 1000 fractional SPY at first valid observation after start; no invented/backdated entry; no dividends",
        }

    def heartbeat(self, now):
        manifest = self.manifest()
        path = self.root / manifest["id"] / "heartbeat.json"
        previous = json.loads(path.read_text()) if path.exists() else {}
        if (
            previous.get("at")
            and (now - datetime.fromisoformat(previous["at"])).total_seconds() > 15
        ):
            gap_path = path.with_name("gaps.json")
            gaps = json.loads(gap_path.read_text()) if gap_path.exists() else []
            gaps.append(
                {
                    "from": previous["at"],
                    "to": stamp(now),
                    "reason": "service unavailable or suspended; no reconstruction",
                }
            )
            atomic_json(gap_path, gaps)
        atomic_json(path, {"at": stamp(now)})

    def gaps(self):
        path = self.root / self.manifest()["id"] / "gaps.json"
        return json.loads(path.read_text()) if path.exists() else []

    def finalize(self, now=None, *, vault=None):
        now = now or utcnow()
        with self.lock():
            manifest = self.manifest()
            if not manifest["expires_at"] or now < datetime.fromisoformat(
                manifest["expires_at"]
            ):
                return False
            directory = self.root / manifest["id"]
            final = directory / "final.json"
            if not final.exists():
                cutoff = datetime.fromisoformat(manifest["expires_at"])
                for ledger in self.ledgers().values():
                    with ledger.connect() as db:
                        db.execute(
                            "UPDATE decisions SET status='cancelled',completed_at=?,error='Experiment expired; pending proposal cancelled.' WHERE status='running'",
                            (stamp(cutoff),),
                        )
                    ledger.runtime(autotrading_enabled=False)
                result = self.snapshot(cutoff)
                result["status"] = "expired"
                atomic_json(final, result)
            manifest["status"] = "expired"
            self.save(manifest)
            if vault is None:
                from memory import vault_dir

                vault = vault_dir()
            summary = Path(vault) / "Paper Market" / f"{manifest['id']}.md"
            summary.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            result = json.loads(final.read_text())
            lines = ["# Paper Market comparison", "", f"Experiment: {manifest['id']}"]
            for key in ("started_at", "expires_at"):
                lines.append(
                    f"{key}: {datetime.fromisoformat(manifest[key]).astimezone(ZoneInfo('Asia/Singapore')).isoformat()}"
                )
            for portfolio in result["portfolios"]:
                a = portfolio["account"]
                if portfolio.get("blocked"):
                    lines.append(
                        f"\n{portfolio['name']}: {portfolio['blocked_reason']}; no fills."
                    )
                    continue
                lines.append(
                    f"\n{portfolio['name']}: equity ${a['equity_usd']}; return {a['total_return_pct']}%; marks {a['marks_as_of']}; fresh {a['valuation_fresh']}."
                )
            lines += [
                "",
                "Open holdings preserved. API/model costs excluded. Corporate actions not modeled; affected results incomplete.",
                f"Full comparison: {final}",
            ]
            summary.write_text("\n".join(lines) + "\n")
            return True

    def settings_hash(self):
        return hashlib.sha256(
            json.dumps(self.manifest()["settings"], sort_keys=True).encode()
        ).hexdigest()
