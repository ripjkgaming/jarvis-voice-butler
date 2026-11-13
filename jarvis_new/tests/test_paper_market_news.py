import asyncio

import pytest

from paper_market.news import (
    IronSightAdapter,
    NewsRunner,
    NewsStore,
    Screening,
    parse_screening,
    poll_once,
)


def item(title="Rates move", **kwargs):
    return {
        "title": title,
        "link": "https://news.test/a",
        "source": "Wire",
        "pubDate": "2026-11-12T00:00:00Z",
        **kwargs,
    }


def test_ingest_deduplicates_and_creates_revisions_and_portfolio_receipts(tmp_path):
    store = NewsStore(tmp_path / "news.sqlite3")
    first = store.ingest([item()], checkpoint="c1")
    assert len(first) == 1 and first[0].revision == 1
    assert store.ingest([item()], checkpoint="c2") == []
    revised = store.ingest([item("Rates cut", category="markets")])
    assert revised[0].id == first[0].id and revised[0].revision == 2
    assert store.checkpoint() == "c2"
    store.set_screening(
        first[0].id,
        1,
        Screening(
            True,
            ["SPY"],
            "Fed changes rates",
            "Moves bond yields",
            0.9,
            "Updates prior report",
        ),
    )
    for portfolio in ("codex", "copy", "bot"):
        assert len(store.useful_pending(portfolio)) == 1
    pending = store.useful_pending("copy")
    assert pending[0]["delivery_id"] == f"{first[0].id}:1"
    assert store.acknowledge("copy", [pending[0]["delivery_id"]]) == 1
    assert store.acknowledge("copy", [pending[0]["delivery_id"]]) == 0
    assert store.useful_pending("copy") == []
    assert len(store.useful_pending("codex")) == 1


def test_failed_screening_remains_retryable(tmp_path):
    store = NewsStore(tmp_path / "news.sqlite3")
    report = store.ingest([item()])[0]
    store.set_screening(report.id, report.revision, None, "temporary model error")
    assert store.pending_screening()[0].id == report.id
    assert store.useful_pending("codex") == []


def test_screening_schema_rejects_wrong_types():
    with pytest.raises(ValueError):
        parse_screening({"useful": "yes"})


def test_adapter_snapshot_ingestion_and_runner_stop(tmp_path):
    class FakeAdapter:
        def fetch(self):
            return [item()], "cursor"

    store = NewsStore(tmp_path / "news.sqlite3")
    inserted = asyncio.run(poll_once(store, FakeAdapter()))
    assert len(inserted) == 1 and store.checkpoint() == "cursor"
    assert store.recent_latencies()[0]["stage"] == "feed_fetch"
    with pytest.raises(ValueError):
        NewsRunner(store, poll_seconds=2)

    async def run():
        runner = NewsRunner(store, FakeAdapter())
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.01)
        runner.stop()
        await asyncio.wait_for(task, 1)

    asyncio.run(run())


def test_adapter_requires_http_url():
    with pytest.raises(ValueError):
        IronSightAdapter("file:///tmp/news")


def test_acknowledgement_is_revision_specific(tmp_path):
    store = NewsStore(tmp_path / "news.sqlite3")
    first = store.ingest([item()])[0]
    store.set_screening(
        first.id,
        first.revision,
        Screening(True, ["SPY"], "First", "Reason", 0.8, "Initial"),
    )
    second = store.ingest([item("Updated rates", category="new revision")])[0]
    store.set_screening(
        second.id,
        second.revision,
        Screening(True, ["SPY"], "Updated", "Reason", 0.8, "Contradicts prior"),
    )
    assert store.acknowledge("codex", [f"{first.id}:1"]) == 1
    pending = store.useful_pending("codex")
    assert [row["revision"] for row in pending] == [2]
    assert store.acknowledge("codex", [(second.id, 2)]) == 1


def test_runner_polls_while_screening_is_inflight_and_supplies_context(tmp_path):
    class CountingAdapter:
        def __init__(self):
            self.calls = 0

        def fetch(self):
            self.calls += 1
            return [], str(self.calls)

    async def run():
        store = NewsStore(tmp_path / "news.sqlite3")
        prior = store.ingest([item("Prior inflation report")])[0]
        store.set_screening(
            prior.id, 1, Screening(True, ["SPY"], "Prior", "Context", 0.8, "")
        )
        current = store.ingest(
            [item("New inflation report", link="https://news.test/b")]
        )[0]
        adapter = CountingAdapter()
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow_screen(report):
            assert report.prior_reports[0]["id"] == prior.id
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        runner = NewsRunner(store, adapter, screen=slow_screen)
        runner.poll_seconds = 0.02  # accelerate only this deterministic test
        task = asyncio.create_task(runner.run())
        await asyncio.wait_for(started.wait(), 0.5)
        before = adapter.calls
        await asyncio.sleep(0.07)
        assert adapter.calls > before
        runner.stop()
        await asyncio.wait_for(task, 0.5)
        assert cancelled.is_set()
        status = store.runner_status()
        assert status["runner"] == "stopped"
        assert status["screening_model"] == "gpt-6-luna"
        assert current.id == store.pending_screening()[0].id

    asyncio.run(run())


def test_runner_status_persists_safe_feed_error(tmp_path):
    import urllib.error

    class BrokenAdapter:
        def fetch(self):
            raise urllib.error.HTTPError(
                "http://secret.invalid", 502, "secret body", {}, None
            )

    async def run():
        store = NewsStore(tmp_path / "news.sqlite3")
        runner = NewsRunner(store, BrokenAdapter())
        runner.poll_seconds = 0.01
        task = asyncio.create_task(runner.run())
        for _ in range(30):
            if store.runner_status().get("connection") == "disconnected":
                break
            await asyncio.sleep(0.005)
        runner.stop()
        await asyncio.wait_for(task, 0.5)
        status = store.runner_status()
        assert status["last_error"] == "http_502"
        assert "secret" not in str(status)
        assert "http://" not in str(status)

    asyncio.run(run())


def test_replay_of_older_revision_does_not_create_new_revision(tmp_path):
    store = NewsStore(tmp_path / "news.sqlite3")
    original = item("Version A", id="stable-report")
    update = item("Version B", id="stable-report")
    first = store.ingest([original])
    second = store.ingest([update])
    assert [row.revision for row in first + second] == [1, 2]
    assert store.ingest([original]) == []
    assert [row.revision for row in store.pending_screening()] == [1, 2]
