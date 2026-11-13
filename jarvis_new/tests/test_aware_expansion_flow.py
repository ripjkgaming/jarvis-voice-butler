"""Persisted scan-to-consumer flow; no real browser/model/calendar calls."""

import pytest

from aware import calendar_stream, expansion_memory, state, whatsapp_scan
from aware.message_store import Store
from system import whatsapp


@pytest.mark.asyncio
async def test_repeated_scan_and_reopened_store_do_not_repeat_consumers(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    state.update(enabled=True)
    now = 1800000000.0
    chats = [{"chat_id": f"{i}@s.whatsapp.net", "name": f"Chat {i}"} for i in range(20)]

    async def snapshot(limit):
        return chats[:limit]

    async def read(name, boundary, *, chat_id, checkpoint, on_page, **kwargs):
        messages = [
            {
                "id": f"{chat_id}-plan",
                "timestamp": now - 100,
                "order": 0,
                "text": "Dentist appointment tomorrow at 3pm",
                "sender": "Friend",
            }
        ]
        assert on_page(messages) is not False
        return {
            "ok": True,
            "complete": True,
            "chat_id": chat_id,
            "messages": messages,
            "marks_read": True,
        }

    monkeypatch.setattr(whatsapp, "list_chat_snapshot", snapshot)
    monkeypatch.setattr(whatsapp, "read_chat_history", read)
    path = tmp_path / "stream.sqlite3"
    store = Store(path)
    first = await whatsapp_scan.scan(store=store, now=now, enabled=lambda: True)
    assert first["selected"] == 20
    assert all(chat["status"] == "complete" for chat in first["chats"])
    staging = []
    monkeypatch.setattr(
        calendar_stream.wc,
        "ingest_aware_message",
        lambda chat, message, context, **kwargs: (
            staging.append((message["chat_id"], message["id"])) or "staged"
        ),
    )
    monkeypatch.setattr(calendar_stream.wc, "process_pending", lambda **kwargs: [])
    monkeypatch.setattr(expansion_memory, "_vault_append", lambda *args: True)
    assert calendar_stream.process(store=store, now=now)["staged"] == 20
    assert expansion_memory.process_pending(store=store, now=now)["recorded"] == 20
    store.close()
    store = Store(path)
    again = await whatsapp_scan.scan(store=store, now=now + 180, enabled=lambda: True)
    assert all(chat["messages"] == 0 for chat in again["chats"])
    assert calendar_stream.process(store=store, now=now + 180)["staged"] == 0
    assert expansion_memory.process_pending(store=store, now=now + 180)["recorded"] == 0
    assert len(staging) == 20
    assert expansion_memory.status()["total"] == 20
    store.close()


def test_calendar_outage_retries_without_restaging_or_browser_read(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    state.update(enabled=True)
    now = 1800000000.0
    store = Store(tmp_path / "stream.sqlite3")
    store.ingest(
        "chat",
        "A",
        [{"id": "m", "timestamp": now - 1, "text": "Confirmed meeting tomorrow"}],
        boundary=now - 7 * 86400,
        complete=True,
        now=now,
    )
    staged = []
    monkeypatch.setattr(
        calendar_stream.wc,
        "ingest_aware_message",
        lambda chat, message, context, **kwargs: (
            staged.append(message["id"]) or "staged"
        ),
    )
    attempts = []

    def process(**kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("calendar unavailable")
        return [{"id": "m", "outcome": "auto-added"}]

    monkeypatch.setattr(calendar_stream.wc, "process_pending", process)
    with pytest.raises(OSError):
        calendar_stream.process(store=store, now=now)
    assert store.pending("calendar") == []
    result = calendar_stream.process(store=store, now=now + 180)
    assert result["calendar"][0]["outcome"] == "auto-added"
    assert staged == ["m"]
    assert len(attempts) == 2
    store.close()
