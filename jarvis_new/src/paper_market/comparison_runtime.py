"""Independent news, quote and strategy scheduling for the comparison service."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .calendar import session_at
from .comparison import Comparison, atomic_json
from .engine import Engine, refresh
from .ledger import Ledger, stamp, utcnow


class ComparisonRuntime:
    def __init__(self, comparison: Comparison, stopped):
        self.comparison = comparison
        self.stopped = stopped
        self.news_store = None

    def expired(self):
        manifest = self.comparison.manifest()
        return bool(
            manifest["expires_at"]
            and utcnow() >= datetime.fromisoformat(manifest["expires_at"])
        )

    async def delay(self, seconds):
        # A thread Event allows HTTP shutdown to wake every background task.
        for _ in range(max(1, int(seconds * 2))):
            if self.stopped.is_set() or self.expired():
                return
            await asyncio.sleep(0.5)

    async def run(self):
        from .news import NewsRunner, NewsStore, screen_with_codex

        manifest = self.comparison.manifest()
        self.news_store = NewsStore(
            self.comparison.root / manifest["id"] / "news.sqlite3"
        )
        runner = NewsRunner(self.news_store, screen=screen_with_codex)
        tasks = [asyncio.create_task(runner.run())]
        active_started = False
        try:
            while not self.stopped.is_set() and not self.expired():
                current = self.comparison.manifest()
                if current["id"] != manifest["id"]:
                    return  # Service supervisor reconstructs tasks for a new experiment.
                if current["status"] == "active":
                    if not active_started:
                        tasks += [
                            asyncio.create_task(self.portfolio(key))
                            for key in self.comparison.active_keys()
                        ]
                        tasks += [asyncio.create_task(self.quotes())]
                        active_started = True
                    self.comparison.heartbeat(utcnow())
                await self.delay(2)
            if self.expired():
                self.comparison.finalize()
        finally:
            runner.stop()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def quotes(self):
        while not self.stopped.is_set() and not self.expired():
            for key, ledger in self.comparison.ledgers().items():
                try:
                    await asyncio.to_thread(refresh, ledger)
                    if key == "codex":
                        await Engine(ledger).risk_once()
                    elif key == "bot":
                        from .ta_runner import stop_check

                        await stop_check(ledger)
                except Exception:
                    ledger.runtime(
                        last_refresh_error="Comparison quote/risk cycle failed; no invented marks."
                    )
            await self.delay(60 if session_at(utcnow()).is_open else 300)

    async def portfolio(self, key):
        ledger = self.comparison.ledgers()[key]
        while not self.stopped.is_set() and not self.expired():
            now = utcnow()
            reports = self.news_store.useful_pending(key, limit=20)
            slot = int(now.timestamp()) // 300
            with ledger.connect() as db:
                stored = db.execute(
                    "SELECT value FROM runtime WHERE key='comparison_review_slot'"
                ).fetchone()
            previous_slot = json.loads(stored[0]) if stored else None
            if session_at(now).is_open and (reports or previous_slot != slot):
                start = time.monotonic()
                if reports:
                    oldest = min(
                        datetime.fromisoformat(r["received_at"]) for r in reports
                    )
                    self.news_store.record_latency(
                        f"queue_wait:{key}", (now - oldest).total_seconds() * 1000
                    )
                try:
                    remaining = (
                        datetime.fromisoformat(ledger.experiment()["expires_at"]) - now
                    ).total_seconds()

                    async def review(reports=reports):
                        if key == "codex":
                            return await Engine(ledger).run_once(
                                news=reports,
                                trigger="useful_news"
                                if reports
                                else "session_or_baseline",
                            )
                        if key == "bot":
                            from .ta_runner import review_portfolio
                        else:
                            from .copy_trader import review_portfolio
                        return await review_portfolio(ledger, news=reports)

                    result = await asyncio.wait_for(
                        review(), timeout=max(0.01, min(600, remaining))
                    )
                    status = result.get("status")
                    self.news_store.record_latency(
                        f"review_total:{key}",
                        (time.monotonic() - start) * 1000,
                        outcome="ok" if status != "error" else "failed",
                    )
                    if isinstance(result.get("latency_seconds"), (int, float)):
                        self.news_store.record_latency(
                            f"inference:{key}", result["latency_seconds"] * 1000
                        )
                    successful = status in {
                        "filled",
                        "held",
                        "reviewed",
                        "copied",
                        "no_changes",
                        "no_signals",
                        "context_only",
                        "duplicate",
                    }
                    if successful:
                        # Tokens name the exact revisions captured before inference.
                        tokens = [(r["id"], r["revision"]) for r in reports]
                        self.news_store.acknowledge(key, tokens)
                        ledger.runtime(comparison_review_slot=slot)
                    ledger.runtime(
                        last_review=result,
                        last_review_at=stamp(utcnow()),
                        pending_news=len(reports) if not successful else 0,
                        last_review_latency_seconds=round(time.monotonic() - start, 3),
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.news_store.record_latency(
                        f"review_total:{key}",
                        (time.monotonic() - start) * 1000,
                        outcome="failed",
                    )
                    ledger.runtime(
                        last_error="Comparison review failed; useful news remains queued for retry.",
                        last_review_latency_seconds=round(time.monotonic() - start, 3),
                    )
            await self.delay(15)


async def probe(comparison: Comparison):
    """Exercise installed integrations without starting the measured accounts."""
    from .copy_trader import readiness as copy_readiness
    from .news import IronSightAdapter, NewsStore, poll_once, screen_with_codex
    from .ta_runner import readiness as bot_readiness

    async def save(name, result):
        result.update(
            checked_at=stamp(utcnow()), settings_hash=comparison.settings_hash()
        )
        atomic_json(comparison.root / f"readiness-{name}.json", result)
        return result

    try:
        manifest = comparison.manifest()
        store = NewsStore(comparison.root / manifest["id"] / "news.sqlite3")
        reports = await poll_once(store, IronSightAdapter())
        pending = reports or store.pending_screening()
        if not pending:
            raise RuntimeError(
                "No real Ironsight report is available for a screening trial."
            )
        report = pending[0]
        screening = await screen_with_codex(report)
        store.set_screening(report.id, report.revision, screening)
        await save(
            "news",
            {
                "ready": True,
                "blockers": [],
                "status": "connected",
                "screening_model": "gpt-6-luna",
                "screening_effort": "low",
                "report_id": report.id,
                "report_at": report.received_at,
                "poll_seconds": 90,
            },
        )
    except Exception:
        await save(
            "news",
            {
                "ready": False,
                "blockers": [
                    "Ironsight live ingestion and Luna screening have not both succeeded"
                ],
                "status": "unverified",
            },
        )
    try:
        with tempfile.TemporaryDirectory(prefix="paper-model-probe-") as directory:
            ledger = Ledger(Path(directory) / "ledger.sqlite3")
            ledger.initialize(utcnow())
            result = await Engine(ledger).preflight()
        passed = result.get("status") == "preflight_passed"
        await save(
            "models",
            {
                "ready": passed,
                "result": result,
                "blockers": []
                if passed
                else ["Authenticated Sol medium proposal preflight failed"],
            },
        )
    except Exception:
        await save(
            "models",
            {
                "ready": False,
                "blockers": ["Authenticated Sol medium proposal preflight failed"],
            },
        )
    try:
        result = await bot_readiness()
        passed = result.get("ready") is True or result.get("status") == "ready"
        await save(
            "bot",
            {
                **result,
                "ready": passed,
                "blockers": []
                if passed
                else ["Intended TradingAgents installation/model routing not verified"],
            },
        )
    except Exception:
        await save(
            "bot",
            {
                "ready": False,
                "blockers": ["TradingAgents installation/model routing not verified"],
            },
        )
    try:
        result = copy_readiness()
        if asyncio.iscoroutine(result):
            result = await result
        await save("copy", result)
    except Exception:
        await save(
            "copy",
            {
                "ready": False,
                "blockers": [
                    "Verified trader source, history review and frozen selection are not configured"
                ],
            },
        )
    return comparison.readiness()


async def trial(comparison: Comparison, *, seconds=60, gate=()):
    """A separate live short trial; it cannot alter or start measured portfolios."""
    import threading

    if not 10 <= seconds <= 1500:
        raise ValueError("Trial duration must be 10..1500 seconds.")
    await probe(comparison)
    readiness = comparison.readiness(gated=set(gate))
    blockers = [item for item in readiness["blockers"] if not item.startswith("trial:")]
    if not session_at(utcnow()).is_open:
        blockers.append(
            "Short execution trial requires an open regular US equity session"
        )
    result = {
        "ready": False,
        "blockers": blockers,
        "checked_at": stamp(utcnow()),
        "settings_hash": comparison.settings_hash(),
        "duration_seconds": seconds,
    }
    if blockers:
        atomic_json(comparison.root / "readiness-trial.json", result)
        return result
    manifest = comparison.manifest()
    directory = (
        comparison.root / manifest["id"] / "trials" / stamp(utcnow()).replace(":", "-")
    )
    test = Comparison(directory, readiness=lambda: {"ready": True, "blockers": []})
    test.prepare()
    test.start(gate=tuple(gate))
    from .news import NewsStore

    source = NewsStore(comparison.root / manifest["id"] / "news.sqlite3")
    destination_path = test.root / test.manifest()["id"] / "news.sqlite3"
    # SQLite backup captures a consistent queue; reset trial receipts only in the copy.
    import sqlite3

    with sqlite3.connect(source.path) as src, sqlite3.connect(destination_path) as dst:
        src.backup(dst)
        dst.execute(
            "UPDATE receipts SET status='pending',processed_at=NULL WHERE status='processed'"
        )
    stopped = threading.Event()
    runtime = ComparisonRuntime(test, stopped)
    task = asyncio.create_task(runtime.run())

    def settled():
        # Every active portfolio finished a review and drained its news queue.
        for key, ledger in test.ledgers().items():
            with ledger.connect() as db:
                finished = db.execute(
                    "SELECT COUNT(*) FROM decisions WHERE status!='running'"
                ).fetchone()[0]
            if not finished or test_store.useful_pending(key):
                return False
        return True

    from .news import NewsStore as _Store

    test_store = _Store(destination_path)
    try:
        waited = 0.0
        while waited < seconds:
            await asyncio.sleep(5)
            waited += 5
            if waited >= 30 and settled():
                break
    finally:
        stopped.set()
        await task
    snapshot = test.snapshot()
    trial_blockers = []
    for portfolio in snapshot["portfolios"]:
        if portfolio.get("blocked"):
            continue
        account = portfolio["account"]
        if Decimal(account["cash_usd"]) < 0:
            trial_blockers.append(f"{portfolio['id']}: negative cash")
        if not portfolio["decisions"]:
            trial_blockers.append(
                f"{portfolio['id']}: no completed review recorded in trial"
            )
        if portfolio["runtime"].get("last_error"):
            trial_blockers.append(f"{portfolio['id']}: strategy review error")
        if not account["valuation_complete"]:
            trial_blockers.append(f"{portfolio['id']}: incomplete valuation")
    store = NewsStore(destination_path)
    for key in test.active_keys():
        if store.useful_pending(key):
            trial_blockers.append(f"{key}: useful trial news remains unprocessed")
    # Only this disposable trial gets an artificial deadline; the measured
    # experiment always uses exactly 168 hours from Comparison.start.
    cutoff = utcnow()
    for ledger in test.ledgers().values():
        with ledger.connect() as db:
            db.execute("UPDATE experiment SET expires_at=?", (stamp(cutoff),))
    trial_manifest = test.manifest()
    trial_manifest["expires_at"] = stamp(cutoff)
    test.save(trial_manifest)
    test.finalize(cutoff, vault=directory / "vault")
    result.update(
        ready=not trial_blockers,
        blockers=trial_blockers,
        result_path=str(directory),
        completed_at=stamp(utcnow()),
        separate_from_measured_experiment=True,
    )
    atomic_json(comparison.root / "readiness-trial.json", result)
    return result
