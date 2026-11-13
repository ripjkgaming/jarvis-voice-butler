"""Calendar queue consumes durable Always Aware messages exactly once."""

import datetime as dt

import pytest

from aware import calendar_stream
from aware.message_store import Store

NOW = dt.datetime(2026, 11, 12, 12, tzinfo=dt.timezone.utc).timestamp()


@pytest.fixture
def calendar_home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_TZ", "Asia/Singapore")
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_CHATGPT", "1")
    monkeypatch.setenv("JARVIS_WHATSAPP_EVENTS", "1")
    monkeypatch.setattr("chatgpt_cli.shutil.which", lambda _: "/usr/bin/codex")
    import whatsapp_calendar as wc

    monkeypatch.setattr(wc, "process_pending", lambda **_kwargs: [])
    from aware import state

    state.save({**state.load(), "enabled": True})
    return tmp_path


def seeded_store():
    store = Store(":memory:")
    store.ingest(
        "stable-chat-1",
        "Mina",
        [
            {
                "id": "prior",
                "timestamp": NOW - 120,
                "order": 1,
                "text": "Could we meet tomorrow?",
                "sender": "Mina",
            },
            {
                "id": "answer",
                "timestamp": NOW - 60,
                "order": 2,
                "text": "yes, 3 works",
                "sender": "Me",
                "me": True,
            },
        ],
        boundary=NOW - 7 * 86400,
        complete=True,
        now=NOW,
    )
    return store


def test_calendar_adapter_stages_complete_history_with_context_and_original_time(
    calendar_home,
):
    store = seeded_store()

    result = calendar_stream.process(store=store, now=NOW)

    assert result["staged"] == 2
    import whatsapp_calendar as wc

    rows = wc._load()["messages"]
    assert len(rows) == 2
    answer = next(row for row in rows.values() if row["source_message_id"] == "answer")
    assert (
        answer["date"]
        == dt.datetime.fromtimestamp(NOW - 60, dt.timezone.utc)
        .astimezone(dt.timezone(dt.timedelta(hours=8)))
        .isoformat()
    )
    assert "Could we meet tomorrow?" in answer["body"]
    assert answer["historical"] is True
    assert answer["outgoing"] is True
    assert [(m["chat_id"], m["id"]) for m in store.pending("calendar")] == []


def test_partial_bootstrap_does_not_ack_or_stage(monkeypatch, calendar_home):
    import whatsapp_calendar as wc

    store = Store(":memory:")
    store.ingest(
        "chat",
        "Mina",
        [{"id": "m1", "timestamp": NOW, "text": "meeting tomorrow"}],
        boundary=NOW - 7 * 86400,
        complete=False,
        now=NOW,
        error="history unavailable",
    )
    monkeypatch.setattr(
        wc,
        "ingest_aware_message",
        lambda *a, **k: pytest.fail("partial bootstrap cannot stage"),
    )

    result = calendar_stream.process(store=store, now=NOW)

    assert result["waiting_for_coverage"] == ["chat"]
    assert [(m["chat_id"], m["id"]) for m in store.pending("calendar", limit=1000)] == [
        ("chat", "m1")
    ]


def test_duplicate_consumer_delivery_stages_once_forever(monkeypatch, calendar_home):
    import whatsapp_calendar as wc

    store = seeded_store()
    # Store may redeliver after a crash between staging and queue acknowledgment.
    monkeypatch.setattr(store, "acknowledge", lambda *a, **k: None)

    first = calendar_stream.process(store=store, now=NOW)
    second = calendar_stream.process(store=store, now=NOW + 1)

    assert first["staged"] == 2
    assert second["duplicates"] == 2
    assert len(wc._load()["messages"]) == 2


def test_failed_staging_keeps_durable_message_queued(monkeypatch, calendar_home):
    import whatsapp_calendar as wc

    store = seeded_store()
    monkeypatch.setattr(
        wc,
        "ingest_aware_message",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )

    result = calendar_stream.process(store=store, now=NOW)
    assert len(result["errors"]) == 2
    assert len(store.pending("calendar")) == 2


def test_calendar_auto_sidecar_pauses_reads_when_aware_owns_ingestion(monkeypatch):
    import asyncio

    import whatsapp_calendar as wc

    called = []

    class WA:
        async def list_chats(self, _limit):
            called.append("list")
            return []

    monkeypatch.setenv("JARVIS_AWARE", "1")
    monkeypatch.setattr(wc, "_aware_owns_ingestion", lambda: True)
    assert asyncio.run(wc.run(WA(), now=NOW, automatic=True)) == []
    assert called == []


def test_calendar_stream_obeys_aware_off_control(monkeypatch, calendar_home):
    store = seeded_store()
    monkeypatch.setenv("JARVIS_AWARE", "0")

    result = calendar_stream.process(store=store, now=NOW)

    assert result["status"] == "off-or-paused"
    assert len(store.pending("calendar")) == 2


def test_calendar_disabled_message_remains_queued_for_retry(monkeypatch, calendar_home):
    store = seeded_store()
    monkeypatch.setenv("JARVIS_WHATSAPP_EVENTS", "0")

    result = calendar_stream.process(store=store, now=NOW)

    assert len(result["errors"]) == 2
    assert len(store.pending("calendar")) == 2
    import whatsapp_calendar as wc

    assert wc._load().get("aware_receipts", {}) == {}


def test_calendar_action_control_is_checked_before_provider_write(
    monkeypatch, calendar_home
):
    import event_extractor as ex

    record = {
        "id": "wa-paused",
        "source": "whatsapp",
        "event": ex.validate(
            {
                "has_event": True,
                "title": "Dentist",
                "date": "2026-11-15",
                "start": "15:00",
                "all_day": False,
                "confidence": 0.95,
            },
            NOW,
        ),
    }
    result = ex.auto_add_event(
        record,
        now=NOW,
        lister=lambda *args, **kwargs: [],
        inserter=lambda _event: pytest.fail("disabled controls cannot write"),
        control=lambda: False,
    )

    assert result["status"] == "paused"


def test_incomplete_chat_queue_does_not_starve_complete_chats(calendar_home):
    store = Store(":memory:")
    store.ingest(
        "partial",
        "Partial",
        [
            {"id": f"p{i}", "timestamp": NOW - 1000 + i, "text": "old message"}
            for i in range(105)
        ],
        boundary=NOW - 7 * 86400,
        complete=False,
        now=NOW,
        error="partial history",
    )
    store.ingest(
        "complete",
        "Complete",
        [{"id": "c1", "timestamp": NOW - 10, "text": "Meeting tomorrow at 3pm"}],
        boundary=NOW - 7 * 86400,
        complete=True,
        now=NOW,
    )

    result = calendar_stream.process(store=store, now=NOW)

    assert result["waiting_for_coverage"] == ["partial"]
    assert result["staged"] == 1
    assert [(m["chat_id"], m["id"]) for m in store.pending("calendar", limit=1000)] == [
        ("partial", f"p{i}") for i in range(105)
    ]


def test_cancellation_beyond_first_batch_and_recent_context_quarantines_invite(
    calendar_home,
):
    import whatsapp_calendar as wc

    store = Store(":memory:")
    messages = [
        {
            "id": "invite",
            "timestamp": NOW - 2000,
            "order": 0,
            "text": "Meeting tomorrow at 3pm",
        }
    ]
    messages.extend(
        {
            "id": f"filler-{i}",
            "timestamp": NOW - 1999 + i,
            "order": i,
            "text": "ordinary chat",
        }
        for i in range(120)
    )
    messages.append(
        {
            "id": "cancel",
            "timestamp": NOW - 1000,
            "order": 121,
            "text": "Meeting cancelled",
        }
    )
    store.ingest(
        "stable-chat",
        "Mina",
        messages,
        boundary=NOW - 7 * 86400,
        complete=True,
        now=NOW,
    )

    result = calendar_stream.process(store=store, now=NOW)
    invitation = next(
        row
        for row in wc._load()["messages"].values()
        if row.get("source_message_id") == "invite"
    )

    assert result["staged"] == 1
    assert "Meeting cancelled" in invitation["body"]
    assert wc._CHANGE.search(invitation["body"])

    later = calendar_stream.process(store=store, now=NOW + 1)
    assert later["staged"] == 1
    assert any(
        item["message_id"] == "cancel" for item in wc._load()["aware_receipts"].values()
    )


def test_calendar_write_is_journaled_before_call_and_reconciles_timeout(
    monkeypatch, calendar_home
):
    import event_extractor as ex

    record = {
        "id": "wa-row-id",
        "source": "whatsapp",
        "source_chat_id": "chat-stable",
        "source_message_id": "message-original",
        "sender": "Mina",
        "event": ex.validate(
            {
                **{
                    "has_event": True,
                    "title": "Dentist",
                    "date": "2026-11-15",
                    "start": "15:00",
                    "all_day": False,
                    "confidence": 0.95,
                }
            },
            NOW,
        ),
    }
    insert_ids = []
    memories = []
    monkeypatch.setattr(ex.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(
        ex,
        "_record_calendar_memory",
        lambda item, event, gid, now: memories.append((item, event, gid, now)),
    )

    def insert(event):
        receipt = next(
            row for row in ex._attempts_load() if row.get("msg_id") == record["id"]
        )
        assert receipt["status"] == "writing"
        assert receipt["google_id"] == event["jarvis_id"]
        assert receipt["source_message_id"] == "message-original"
        insert_ids.append(event["jarvis_id"])
        raise TimeoutError("provider response lost")

    first = ex.auto_add_event(
        record,
        now=NOW,
        lister=lambda *args, **kwargs: [],
        inserter=insert,
    )
    assert first["status"] == "failed"
    assert len(set(insert_ids)) == 1
    assert memories == []

    second = ex.auto_add_event(
        record,
        now=NOW + 1,
        lister=lambda *args, **kwargs: [],
        inserter=lambda _event: pytest.fail("must reconcile, not create again"),
        getter=lambda event_id, **kwargs: {"id": event_id, "status": "confirmed"},
    )

    assert second == {"status": "added", "google_id": insert_ids[0]}
    assert len(memories) == 1
    assert memories[0][0]["source_message_id"] == "message-original"
    assert memories[0][2] == insert_ids[0]
