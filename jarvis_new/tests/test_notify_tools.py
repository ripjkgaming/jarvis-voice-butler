"""Hermetic tests for src/notify_tools.py (no notify-send, no TTS)."""

from __future__ import annotations

import pytest

import notify
from notify_tools import NotifyTools


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    notify._seen.clear()
    yield
    notify._seen.clear()


def test_tools_register_expected_ids() -> None:
    ids = [tool.id for tool in NotifyTools().tools]
    assert ids == ["notify"]


@pytest.mark.asyncio
async def test_notify_sends_via_notify_send(monkeypatch) -> None:
    calls: list = []

    def fake_send(text, **kw):
        calls.append((text, kw))
        return {"route": "notification", "ok": True}

    monkeypatch.setattr(notify, "send", fake_send)
    tools = NotifyTools()
    result = await NotifyTools.notify(tools, None, message="test is done")  # type: ignore[arg-type]
    assert result == {"say": "Notified, Sir."}
    assert calls[0][0] == "test is done"
    assert calls[0][1]["kind"] == "assistant"
    assert calls[0][1]["source"] == "agent"


@pytest.mark.asyncio
async def test_notify_urgent_passes_through(monkeypatch) -> None:
    calls: list = []

    def fake_send(text, **kw):
        calls.append(kw)
        return {"route": "announce", "ok": True}

    monkeypatch.setattr(notify, "send", fake_send)
    tools = NotifyTools()
    result = await NotifyTools.notify(tools, None, message="fire", urgency="urgent")  # type: ignore[arg-type]
    assert result == {"say": "Notified, Sir."}
    assert calls[0]["urgency"] == "urgent"


@pytest.mark.asyncio
async def test_notify_bad_urgency_falls_back_to_info(monkeypatch) -> None:
    calls: list = []

    def fake_send(text, **kw):
        calls.append(kw)
        return {"route": "notification", "ok": True}

    monkeypatch.setattr(notify, "send", fake_send)
    tools = NotifyTools()
    result = await NotifyTools.notify(tools, None, message="hi", urgency="critical")  # type: ignore[arg-type]
    assert result == {"say": "Notified, Sir."}
    assert calls[0]["urgency"] == "info"


@pytest.mark.asyncio
async def test_notify_empty_message_says_so(monkeypatch) -> None:
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"route": "empty"})
    tools = NotifyTools()
    result = await NotifyTools.notify(tools, None, message="   ")  # type: ignore[arg-type]
    assert result["say"]


@pytest.mark.asyncio
async def test_notify_never_raises(monkeypatch) -> None:
    def bad_send(*a, **k):
        raise RuntimeError("notify down")

    monkeypatch.setattr(notify, "send", bad_send)
    tools = NotifyTools()
    result = await NotifyTools.notify(tools, None, message="hi")  # type: ignore[arg-type]
    assert result["say"]
