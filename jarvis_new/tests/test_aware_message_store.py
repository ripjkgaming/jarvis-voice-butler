"""Durable WhatsApp message storage and consumer queues."""

import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from aware.message_store import Store


def test_default_path_is_private_and_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    store = Store()
    store.ingest(
        "chat-1",
        "Mina",
        [{"id": "m1", "timestamp": 10, "text": "hello"}],
        boundary=0,
        complete=True,
        now=20,
    )
    path = tmp_path / "aware" / "messages.sqlite3"
    assert path.exists()
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert store.chat("chat-1")["last_message_id"] == "m1"
    assert Store().context("chat-1") == store.context("chat-1")


def test_ingest_is_deduplicated_and_orders_equal_timestamps():
    store = Store(":memory:")
    messages = [
        {"id": "b", "timestamp": 10, "order": 2, "text": "second", "me": True},
        {"id": "a", "timestamp": 10, "order": 1, "text": "first", "sender": "Mina"},
    ]
    added = store.ingest("chat", "Mina", messages, boundary=0, complete=True, now=20)
    assert [item["id"] for item in added] == ["a", "b"]
    assert (
        store.ingest(
            "chat", "Mina renamed", messages, boundary=0, complete=True, now=21
        )
        == []
    )
    assert [item["id"] for item in store.context("chat")] == ["a", "b"]
    assert store.context("chat")[1]["me"] is True
    assert store.chat("chat")["name"] == "Mina renamed"


def test_consumer_queues_are_independent_and_ack_requires_composite_ids():
    store = Store(":memory:")
    store.ingest(
        "chat-a",
        "A",
        [{"id": "same", "timestamp": 1, "text": "a"}],
        boundary=0,
        complete=False,
        now=2,
    )
    store.ingest(
        "chat-b",
        "B",
        [{"id": "same", "timestamp": 1, "text": "b"}],
        boundary=0,
        complete=True,
        now=2,
    )
    assert len(store.pending("calendar")) == 2
    assert len(store.pending("memory")) == 2
    store.acknowledge("calendar", [("chat-a", "same")])
    assert [(m["chat_id"], m["id"]) for m in store.pending("calendar")] == [
        ("chat-b", "same")
    ]
    assert len(store.pending("memory")) == 2
    with pytest.raises((TypeError, ValueError)):
        store.acknowledge("calendar", ["same"])


def test_complete_only_queue_skips_partial_chats_without_acknowledging_them():
    store = Store(":memory:")
    store.ingest(
        "partial",
        "A",
        [{"id": "p", "timestamp": 1, "text": "partial"}],
        boundary=0,
        complete=False,
        now=2,
        error="history page failed",
    )
    store.ingest(
        "complete",
        "B",
        [{"id": "c", "timestamp": 2, "text": "complete"}],
        boundary=0,
        complete=True,
        now=3,
    )
    assert [item["id"] for item in store.pending("calendar", complete_only=True)] == [
        "c"
    ]
    assert {item["id"] for item in store.pending("calendar")} == {"p", "c"}


def test_context_can_center_around_source_and_fills_window_at_edges():
    store = Store(":memory:")
    messages = [
        {"id": f"m{i}", "timestamp": i, "order": i, "text": str(i)} for i in range(7)
    ]
    store.ingest("chat", "A", messages, boundary=0, complete=True, now=8)
    assert [item["id"] for item in store.context("chat", 5, around_id="m3")] == [
        "m1",
        "m2",
        "m3",
        "m4",
        "m5",
    ]
    assert [item["id"] for item in store.context("chat", 4, around_id="m0")] == [
        "m0",
        "m1",
        "m2",
        "m3",
    ]
    assert store.context("chat", around_id="unknown") == []


def test_partial_scan_keeps_coverage_open_and_retry_closes_it():
    store = Store(":memory:")
    store.ingest(
        "chat",
        "A",
        [{"id": "m1", "timestamp": 1, "text": "x"}],
        boundary=-7,
        complete=False,
        now=2,
        error="history unavailable",
    )
    partial = store.chat("chat")
    assert partial["boundary"] == -7
    assert partial["complete"] is False
    assert partial["error"] == "history unavailable"
    store.ingest(
        "chat",
        "A",
        [{"id": "m2", "timestamp": 2, "text": "y"}],
        boundary=0,
        complete=True,
        now=3,
    )
    assert store.chat("chat")["boundary"] == -7
    assert store.chat("chat")["complete"] is True
    assert store.chat("chat")["error"] == ""
    assert store.chat("chat")["last_message_id"] == "m2"
    assert store.chat("chat")["last_scan_complete"] is True
    assert store.chat("chat")["bootstrap_cursor_id"] == "m1"
    assert store.chat("chat")["incremental_cursor_id"] == "m2"


def test_partial_incremental_scan_does_not_advance_forward_cursor():
    store = Store(":memory:")
    store.ingest(
        "chat",
        "A",
        [{"id": "m1", "timestamp": 1, "text": "x"}],
        boundary=0,
        complete=True,
        now=2,
    )
    store.ingest(
        "chat",
        "A",
        [{"id": "m3", "timestamp": 3, "text": "z"}],
        boundary=0,
        complete=False,
        now=4,
        error="page failed",
    )
    checkpoint = store.chat("chat")
    assert checkpoint["complete"] is True  # historical bootstrap remains done
    assert checkpoint["last_scan_complete"] is False
    assert checkpoint["incremental_cursor_id"] == "m1"


@pytest.mark.parametrize(
    "message",
    [
        {"id": "", "timestamp": 1},
        {"id": "   ", "timestamp": 1},
        {"id": "m", "timestamp": float("nan")},
        {"id": "m", "timestamp": float("inf")},
        {"id": "m", "timestamp": 1, "order": float("nan")},
        {"id": "m", "timestamp": 1, "order": float("inf")},
    ],
)
def test_invalid_ids_and_non_finite_ordering_are_rejected(message):
    store = Store(":memory:")
    with pytest.raises(ValueError):
        store.ingest("chat", "A", [message], boundary=0, complete=True, now=2)


def test_concurrent_connections_deduplicate_and_keep_readers_writable(tmp_path):
    db = tmp_path / "shared.sqlite3"
    stores = [Store(db) for _ in range(8)]

    def write(index):
        return stores[index].ingest(
            "chat",
            "A",
            [{"id": "same", "timestamp": 1, "text": "one"}],
            boundary=0,
            complete=True,
            now=2 + index,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(8)))
    assert sum(map(len, results)) == 1
    assert len(stores[0].context("chat")) == 1
    assert stores[-1].chat("chat")["last_message_id"] == "same"


def test_failed_ingest_does_not_advance_checkpoint(tmp_path):
    db = tmp_path / "broken.sqlite3"
    store = Store(db)
    with pytest.raises(TypeError):
        store.ingest(
            "chat",
            "A",
            [{"id": "bad", "timestamp": object()}],
            boundary=0,
            complete=True,
            now=2,
        )
    assert store.chat("chat") is None
