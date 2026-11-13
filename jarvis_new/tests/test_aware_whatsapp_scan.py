from __future__ import annotations

import pytest

from aware import whatsapp_scan
from system import whatsapp

CHAT_ID = "123@s.whatsapp.net"


def _tag_chat(rows):
    return [{**row, "chat_id": CHAT_ID} for row in rows]


@pytest.fixture(autouse=True)
def _isolate_aware_state(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis"))


class Store:
    def __init__(self, checkpoints=None):
        self.checkpoints = checkpoints or {}
        self.writes = []

    def chat(self, chat_id):
        return self.checkpoints.get(chat_id, {})

    def ingest(self, chat_id, name, messages, **kwargs):
        self.writes.append((chat_id, name, list(messages), kwargs))
        self.checkpoints[chat_id] = {
            "chat_id": chat_id,
            "name": name,
            "boundary": kwargs["boundary"],
            "complete": kwargs["complete"],
            "last_success": kwargs["now"] if kwargs["complete"] else 0,
            "last_timestamp": max((m["timestamp"] for m in messages), default=0),
        }
        return list(messages)


@pytest.mark.asyncio
async def test_scan_bootstraps_first_20_with_seven_day_boundary_and_persists_each(
    monkeypatch,
):
    now = 1_800_000_000.0
    chats = [
        {"name": f"Chat {i}", "chat_id": f"{i}@s.whatsapp.net", "pinned": i < 2}
        for i in range(24)
    ]
    monkeypatch.setattr(
        whatsapp, "list_chat_snapshot", lambda limit=20: _async(chats[:limit])
    )
    seen = []

    async def read(name, boundary, **kwargs):
        seen.append((name, boundary, kwargs))
        return {
            "ok": True,
            "complete": True,
            "chat_id": next(c["chat_id"] for c in chats if c["name"] == name),
            "messages": [
                {"id": name, "timestamp": now - 100, "order": 0, "text": name}
            ],
            "error": "",
            "marks_read": True,
        }

    monkeypatch.setattr(whatsapp, "read_chat_history", read)
    store = Store()
    result = await whatsapp_scan.scan(store=store, now=now, enabled=lambda: True)

    assert len(result["chats"]) == 20
    assert len(store.writes) == 20
    assert all(outcome["status"] == "complete" for outcome in result["chats"])
    assert all(boundary == now - 7 * 86400 for _, boundary, _ in seen)
    assert result["chats"][0]["marks_read"] is True
    assert {w[0] for w in store.writes} == {c["chat_id"] for c in chats[:20]}


@pytest.mark.asyncio
async def test_scan_uses_checkpoint_for_returning_chat_and_keeps_partial_retryable(
    monkeypatch,
):
    now = 1_800_000_000.0
    chat_id = "123@s.whatsapp.net"
    old_checkpoint = {
        "chat_id": chat_id,
        "boundary": now - 20 * 86400,
        "complete": False,
        "last_message_timestamp": now - 1000,
        "last_message_order": 1,
        "last_message_id": "old",
    }
    monkeypatch.setattr(
        whatsapp,
        "list_chat_snapshot",
        lambda limit=20: _async([{"name": "Renamed", "chat_id": chat_id}]),
    )
    calls = []

    async def read(name, boundary, **kwargs):
        calls.append((name, boundary, kwargs))
        return {
            "ok": True,
            "complete": False,
            "chat_id": chat_id,
            "messages": [
                {"id": "new", "timestamp": now - 10, "order": 0, "text": "new"}
            ],
            "error": "history paging stalled before boundary",
            "marks_read": True,
        }

    monkeypatch.setattr(whatsapp, "read_chat_history", read)
    store = Store({chat_id: old_checkpoint})
    result = await whatsapp_scan.scan(store=store, now=now, enabled=lambda: True)

    assert calls[0][1] == old_checkpoint["boundary"]
    assert calls[0][2]["checkpoint"] == old_checkpoint
    assert result["chats"][0]["status"] == "partial"
    assert result["chats"][0]["retryable"] is True
    assert store.writes[0][3]["complete"] is False
    assert store.writes[0][3]["error"] == "history paging stalled before boundary"


@pytest.mark.asyncio
async def test_scan_never_uses_display_name_as_stable_identity(monkeypatch):
    monkeypatch.setattr(
        whatsapp, "list_chat_snapshot", lambda limit=20: _async([{"name": "No JID"}])
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("must not open a chat with an inferred identity")

    monkeypatch.setattr(whatsapp, "read_chat_history", forbidden)
    result = await whatsapp_scan.scan(
        store=Store(), now=1_800_000_000.0, enabled=lambda: True
    )

    assert result["chats"] == [
        {
            "slot": 1,
            "status": "unavailable",
            "retryable": True,
            "messages": 0,
            "error": "stable WhatsApp chat ID unavailable",
            "marks_read": False,
        }
    ]


@pytest.mark.asyncio
async def test_scan_checks_pause_between_chats_and_returns_outcome_for_unscanned(
    monkeypatch,
):
    chats = [{"name": f"Chat {i}", "chat_id": f"{i}@s.whatsapp.net"} for i in range(3)]
    monkeypatch.setattr(
        whatsapp, "list_chat_snapshot", lambda limit=20: _async(chats[:limit])
    )
    reads = []

    async def read(name, boundary, **kwargs):
        reads.append(name)
        return {
            "ok": True,
            "complete": True,
            "chat_id": kwargs.get("chat_id"),
            "messages": [],
            "error": "",
            "marks_read": True,
        }

    monkeypatch.setattr(whatsapp, "read_chat_history", read)
    result = await whatsapp_scan.scan(
        store=Store(), now=1_800_000_000.0, enabled=lambda: len(reads) < 1
    )

    assert reads == ["Chat 0"]
    assert [row["status"] for row in result["chats"]] == [
        "complete",
        "paused",
        "paused",
    ]


@pytest.mark.asyncio
async def test_date_reader_pages_past_60_until_boundary_and_reports_stall(monkeypatch):
    now = 1_800_000_000.0
    boundary = now - 7 * 86400
    messages = [
        {
            "id": f"msg-{i}",
            "chat_id": CHAT_ID,
            "pre": "[10:00 am, 12/11/2026] Alice: ",
            "text": f"message {i}",
            "me": False,
            "timestamp": str(now - (119 - i) * 2 * 3600),
        }
        for i in range(120)
    ]
    pages = [messages[60:], messages[:60]]
    current = {"page": 0}

    async def opened(*args):
        return {"ok": True, "name": args[-1]}

    async def fake_cdp(expr, timeout=25):
        if "scrollHeight" in expr:
            return True
        if "scrollTop" in expr:
            if current["page"] + 1 < len(pages):
                current["page"] += 1
                return "moved"
            return "head"
        if "msg-container" in expr:
            return __import__("json").dumps(pages[current["page"]])
        return None

    monkeypatch.setattr(whatsapp, "_open_chat_unlocked", opened)
    monkeypatch.setattr(whatsapp, "_open_chat_id_unlocked", opened)
    monkeypatch.setattr(whatsapp, "cdp", fake_cdp)
    result = await whatsapp.read_chat_history(
        "Alice", boundary, chat_id=CHAT_ID, max_pages=5, settle_s=0
    )

    assert result["ok"] and result["complete"] is True
    assert result["pages"] == 2
    assert len(result["messages"]) > 60
    assert all(row["timestamp"] >= boundary for row in result["messages"])
    assert result["marks_read"] is True


@pytest.mark.asyncio
async def test_scroll_head_without_boundary_is_partial_and_not_fabricated(monkeypatch):
    now = 1_800_000_000.0
    boundary = now - 7 * 86400
    rows = [
        {
            "id": "fresh",
            "pre": "[10:00 am, 12/11/2026] Alice: ",
            "text": "recent",
            "timestamp": str(now - 60),
        }
    ]
    rows = _tag_chat(rows)

    async def opened(name):
        return {"ok": True, "name": name}

    async def fake_cdp(expr, timeout=25):
        if "scrollHeight" in expr:
            return True
        if "scrollTop" in expr:
            return "head"
        if "msg-container" in expr:
            return __import__("json").dumps(rows)
        return None

    monkeypatch.setattr(whatsapp, "_open_chat_unlocked", opened)
    monkeypatch.setattr(whatsapp, "cdp", fake_cdp)
    result = await whatsapp.read_chat_history(
        "Alice", boundary, chat_id=CHAT_ID, max_pages=5, settle_s=0
    )

    assert result["complete"] is False
    assert result["coverage"] == "partial"
    assert result["error"] == "history ended before coverage boundary"


@pytest.mark.asyncio
async def test_incremental_reader_uses_completed_cursor_not_partial_watermark(
    monkeypatch,
):
    now = 1_800_000_000.0
    boundary = now - 7 * 86400
    rows = [
        {
            "id": key,
            "chat_id": CHAT_ID,
            "pre": "[10:00 am, 12/11/2026] Alice: ",
            "text": key,
            "timestamp": str(ts),
        }
        for key, ts in (
            ("A", now - 300),
            ("B", now - 200),
            ("C", now - 100),
            ("D", now - 50),
        )
    ]

    async def opened(name):
        return {"ok": True, "name": name}

    async def fake_cdp(expr, timeout=25):
        if "scrollHeight" in expr:
            return True
        if "scrollTop" in expr:
            return "head"
        if "msg-container" in expr:
            return __import__("json").dumps(rows)
        return None

    monkeypatch.setattr(whatsapp, "_open_chat_unlocked", opened)
    monkeypatch.setattr(whatsapp, "cdp", fake_cdp)
    checkpoint = {
        "complete": True,
        "last_message_id": "D",
        "last_message_timestamp": now - 50,
        "last_message_order": 3,
        "incremental_cursor_id": "B",
        "incremental_cursor_timestamp": now - 200,
        "incremental_cursor_order": 1,
    }
    result = await whatsapp.read_chat_history(
        "Alice", boundary, chat_id=CHAT_ID, checkpoint=checkpoint, settle_s=0
    )

    assert [item["id"] for item in result["messages"]] == ["B", "C", "D"]
    assert result["complete"] is True


@pytest.mark.asyncio
async def test_incremental_cursor_includes_same_timestamp_overlap_in_dom_order(
    monkeypatch,
):
    timestamp = 1_800_000_000.0
    rows = [
        {
            "id": key,
            "chat_id": CHAT_ID,
            "pre": "[10:00 am, 12/11/2026] Alice: ",
            "text": key,
            "timestamp": str(timestamp),
        }
        for key in ("cursor-z", "new-a")
    ]

    async def opened(name):
        return {"ok": True, "name": name}

    async def fake_cdp(expr, timeout=25):
        if "scrollHeight" in expr:
            return True
        if "scrollTop" in expr:
            return "head"
        if "msg-container" in expr:
            return __import__("json").dumps(rows)
        return None

    monkeypatch.setattr(whatsapp, "_open_chat_unlocked", opened)
    monkeypatch.setattr(whatsapp, "cdp", fake_cdp)
    result = await whatsapp.read_chat_history(
        "Alice",
        timestamp - 7 * 86400,
        chat_id=CHAT_ID,
        checkpoint={
            "complete": True,
            "incremental_cursor_id": "cursor-z",
            "incremental_cursor_timestamp": timestamp,
            "incremental_cursor_order": 0,
        },
        settle_s=0,
    )

    assert result["complete"] is True
    assert [item["id"] for item in result["messages"]] == ["cursor-z", "new-a"]
    assert [item["order"] for item in result["messages"]] == [0, 1]


@pytest.mark.asyncio
async def test_incremental_read_without_observed_cursor_is_partial(monkeypatch):
    now = 1_800_000_000.0
    cursor_timestamp = now - 200
    pages = [
        [
            {
                "id": "after",
                "chat_id": CHAT_ID,
                "pre": "[10:00 am, 12/11/2026] Alice: ",
                "text": "after cursor",
                "timestamp": str(now - 50),
            }
        ],
        [
            {
                "id": "before",
                "chat_id": CHAT_ID,
                "pre": "[10:00 am, 12/11/2026] Alice: ",
                "text": "before cursor",
                "timestamp": str(now - 300),
            }
        ],
    ]
    current = {"page": 0}

    async def opened(name):
        return {"ok": True, "name": name}

    async def fake_cdp(expr, timeout=25):
        if "scrollHeight" in expr:
            return True
        if "scrollTop" in expr:
            current["page"] += 1
            return "moved"
        if "msg-container" in expr:
            return __import__("json").dumps(pages[current["page"]])
        return None

    monkeypatch.setattr(whatsapp, "_open_chat_unlocked", opened)
    monkeypatch.setattr(whatsapp, "cdp", fake_cdp)
    result = await whatsapp.read_chat_history(
        "Alice",
        now - 7 * 86400,
        chat_id=CHAT_ID,
        checkpoint={
            "complete": True,
            "incremental_cursor_id": "missing-cursor",
            "incremental_cursor_timestamp": cursor_timestamp,
            "incremental_cursor_order": 0,
        },
        settle_s=0,
        max_pages=3,
    )

    assert result["complete"] is False
    assert result["error"] == "incremental checkpoint message not found"
    assert [item["id"] for item in result["messages"]] == ["after"]


@pytest.mark.asyncio
async def test_bad_timestamp_on_earlier_page_prevents_later_complete_boundary(
    monkeypatch,
):
    now = 1_800_000_000.0
    boundary = now - 7 * 86400
    pages = [
        [
            {
                "id": "unknown-time",
                "chat_id": CHAT_ID,
                "pre": "timestamp not available",
                "text": "uncertain",
                "timestamp": "",
            },
            {
                "id": "recent",
                "chat_id": CHAT_ID,
                "pre": "[10:00 am, 12/11/2026] Alice: ",
                "text": "recent",
                "timestamp": str(now - 30),
            },
        ],
        [
            {
                "id": "old",
                "chat_id": CHAT_ID,
                "pre": "[10:00 am, 01/11/2026] Alice: ",
                "text": "old",
                "timestamp": str(boundary - 30),
            }
        ],
    ]
    current = {"page": 0}

    async def opened(name):
        return {"ok": True, "name": name}

    async def fake_cdp(expr, timeout=25):
        if "scrollHeight" in expr:
            return True
        if "scrollTop" in expr:
            current["page"] += 1
            return "moved"
        if "msg-container" in expr:
            return __import__("json").dumps(pages[current["page"]])
        return None

    monkeypatch.setattr(whatsapp, "_open_chat_unlocked", opened)
    monkeypatch.setattr(whatsapp, "cdp", fake_cdp)
    result = await whatsapp.read_chat_history(
        "Alice", boundary, chat_id=CHAT_ID, max_pages=3, settle_s=0
    )

    assert result["complete"] is False
    assert result["error"] == "message timestamp unavailable"


@pytest.mark.asyncio
async def test_page_callback_persists_before_more_history_is_requested(monkeypatch):
    now = 1_800_000_000.0
    boundary = now - 7 * 86400
    page = [
        {
            "id": "first-page",
            "chat_id": CHAT_ID,
            "pre": "[10:00 am, 12/11/2026] Alice: ",
            "text": "new",
            "timestamp": str(now - 1),
        }
    ]
    calls = []

    async def opened(name):
        return {"ok": True, "name": name}

    async def fake_cdp(expr, timeout=25):
        calls.append(expr)
        if "scrollHeight" in expr:
            return True
        if "scrollTop" in expr:
            return "moved"
        if "msg-container" in expr:
            return __import__("json").dumps(page)
        return None

    def persist(messages):
        assert [message["id"] for message in messages] == ["first-page"]
        return False

    monkeypatch.setattr(whatsapp, "_open_chat_unlocked", opened)
    monkeypatch.setattr(whatsapp, "cdp", fake_cdp)
    result = await whatsapp.read_chat_history(
        "Alice", boundary, chat_id=CHAT_ID, on_page=persist, max_pages=5, settle_s=0
    )

    assert result["complete"] is False
    assert result["error"] == "scan stopped by Always Aware controls"
    assert not any("scrollTop" in expr for expr in calls)


def test_whatsapp_jid_and_timestamp_parsing_are_explicit():
    assert (
        whatsapp.stable_chat_id("15551234567@s.whatsapp.net")
        == "15551234567@s.whatsapp.net"
    )
    assert whatsapp.stable_chat_id("Alice") is None
    assert whatsapp.message_timestamp("1800000000") == 1_800_000_000


@pytest.mark.asyncio
async def test_scan_counts_messages_persisted_page_by_page_once(monkeypatch):
    from aware.message_store import Store as SQLiteStore

    now = 1_800_000_000.0
    chat_id = "15551234567@s.whatsapp.net"
    monkeypatch.setattr(
        whatsapp,
        "list_chat_snapshot",
        lambda limit=20: _async([{"name": "Alice", "chat_id": chat_id}]),
    )
    messages = [
        {"id": "a", "timestamp": now - 10, "order": 0, "text": "old"},
        {"id": "b", "timestamp": now - 5, "order": 0, "text": "new"},
    ]

    async def read(name, boundary, *, on_page, **kwargs):
        on_page(messages)
        return {
            "ok": True,
            "complete": True,
            "chat_id": chat_id,
            "messages": messages,
            "error": "",
            "marks_read": True,
        }

    monkeypatch.setattr(whatsapp, "read_chat_history", read)
    store = SQLiteStore(":memory:")
    result = await whatsapp_scan.scan(store=store, now=now, enabled=lambda: True)

    assert result["chats"][0]["status"] == "complete"
    assert result["chats"][0]["messages"] == 2
    assert len(store.context(chat_id, limit=20)) == 2


async def _async(value):
    return value
