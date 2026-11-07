import asyncio
import json

import pytest

from system import whatsapp as wa


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_leak_shared_page_lock():
    async with wa._page_session():

        async def wait():
            async with wa._page_session():
                raise AssertionError("must wait")

        task = asyncio.create_task(wait())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with wa._page_session():
        assert wa._page_lock.locked()
    assert not wa._page_lock.locked()


@pytest.mark.asyncio
async def test_read_transactions_do_not_overlap(monkeypatch):
    order = []

    async def read(name, n):
        order.append(name + ":start")
        await asyncio.sleep(0.01)
        order.append(name + ":end")
        return {"ok": True}

    monkeypatch.setattr(wa, "_read_chat_unlocked", read)
    await asyncio.gather(wa.read_chat("A"), wa.read_chat("B"))
    assert order in (
        ["A:start", "A:end", "B:start", "B:end"],
        ["B:start", "B:end", "A:start", "A:end"],
    )


@pytest.fixture
def session(monkeypatch):
    calls = []

    async def chats(n):
        return [{"name": "Friend"}]

    async def opened(name):
        return {"ok": True, "name": name}

    monkeypatch.setattr(wa, "list_chats", chats)
    monkeypatch.setattr(wa, "_open_chat_unlocked", opened)

    async def enter():
        calls.append("enter")
        return True

    monkeypatch.setattr(wa, "cdp_press_enter", enter)
    return calls


@pytest.mark.asyncio
async def test_single_send_requires_new_exact_own_bubble(monkeypatch, session):
    reads = 0

    async def cdp(expr, timeout=25):
        nonlocal reads
        if "#main header" in expr:
            return "Friend\nonline"
        reads += 1
        # An incoming message with identical text cannot verify a send.
        return json.dumps(
            [
                {
                    "text": "hello",
                    "me": reads > 1,
                    "pre": "[10:07 pm, 11/09/2026] Owner: ",
                }
            ]
        )

    async def mutate(expr, timeout=25):
        session.append(expr)
        assert "wrongchat" in expr
        return "typed" if "insertText" in expr else "clicked"

    monkeypatch.setattr(wa, "cdp", cdp)
    monkeypatch.setattr(wa, "cdp_once", mutate)
    assert (await wa.send_chat("Friend", "hello"))["ok"]
    assert len(session) == 2
    assert "draftpresent" in session[0]
    assert "selectAll" not in session[0]
    assert not any("KeyboardEvent" in expr for expr in session)


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", ["draftpresent", "wrongchat", None])
async def test_composer_refusal_never_sends(monkeypatch, session, refusal):
    async def cdp(expr, timeout=25):
        return "[]"

    async def mutate(expr, timeout=25):
        session.append(expr)
        return refusal

    monkeypatch.setattr(wa, "cdp", cdp)
    monkeypatch.setattr(wa, "cdp_once", mutate)
    assert not (await wa.send_chat("Friend", "hello"))["ok"]
    assert len(session) == 1


@pytest.mark.asyncio
async def test_fuzzy_recipient_cannot_send(monkeypatch, session):
    assert not (await wa.send_chat("Frien", "hello"))["ok"]
    assert not session


@pytest.mark.asyncio
async def test_uncertain_mutation_is_never_replayed(monkeypatch):
    calls = []
    monkeypatch.setattr(wa, "page_ws_url", lambda: "ws://mock")

    async def evaluate(*args):
        calls.append(args)
        raise OSError("transport dropped after action")

    monkeypatch.setattr(wa, "cdp_evaluate", evaluate)
    assert await wa.cdp_once("send()") is None
    assert len(calls) == 1


def test_header_matching_is_exact_not_prefix():
    assert wa._same_chat("Friend\nonline", "Friend")
    assert not wa._same_chat("Friend Work\nonline", "Friend")


@pytest.mark.asyncio
async def test_read_chat_retries_once_when_header_changes(monkeypatch):
    calls = []

    async def read(name, n):
        calls.append(name)
        if len(calls) == 1:
            return {"ok": False, "err": "conversation changed while reading"}
        return {"ok": True, "name": name, "messages": []}

    monkeypatch.setattr(wa, "_read_chat_unlocked", read)
    out = await wa.read_chat("Alice")
    assert out["ok"] is True
    assert calls == ["Alice", "Alice"]


@pytest.mark.asyncio
async def test_read_chat_does_not_retry_other_failures(monkeypatch):
    calls = []

    async def read(name, n):
        calls.append(name)
        return {"ok": False, "err": "conversation did not open"}

    monkeypatch.setattr(wa, "_read_chat_unlocked", read)
    assert (await wa.read_chat("Alice"))["err"] == "conversation did not open"
    assert len(calls) == 1


def test_calendar_time_at_end_of_message_is_preserved():
    rows = [
        {
            "pre": "[10:00, 06/11/2026] Clinic: ",
            "text": "Appointment tomorrow 15:00",
            "textSource": "message",
        }
    ]
    assert wa.parse_messages(rows)[0]["text"] == "Appointment tomorrow 15:00"


def test_copyable_fallback_strips_only_its_rendered_timestamp():
    rows = [
        {
            "pre": "[10:00, 06/11/2026] Clinic: ",
            "text": "Appointment tomorrow 15:00 10:00",
            "textSource": "copyable",
            "meta": "10:00",
        }
    ]
    assert wa.parse_messages(rows)[0]["text"] == "Appointment tomorrow 15:00"


@pytest.mark.asyncio
async def test_open_virtualized_chat_walks_past_top(monkeypatch):
    async def chats(n):
        return [{"name": "Lower chat"}]

    async def no_sleep(*args):
        pass

    clicks = []
    scrolls = []

    async def cdp(expr, **kw):
        if "const rows=" in expr:
            clicks.append(expr)
            return "fired:Lower chat" if len(clicks) > 2 else "norow:10"
        if "scrollTop" in expr:
            scrolls.append(expr)
            return 1
        if "#main header" in expr:
            return "Lower chat"
        return None

    monkeypatch.setattr(wa, "list_chats", chats)
    monkeypatch.setattr(wa, "cdp", cdp)
    monkeypatch.setattr(wa.asyncio, "sleep", no_sleep)
    assert (await wa._open_chat_unlocked("Lower chat"))["ok"]
    assert any("scrollTop+=" in expr for expr in scrolls)


@pytest.mark.asyncio
async def test_calendar_read_never_substitutes_shorter_fuzzy_chat(monkeypatch):
    async def chats(n):
        return [{"name": "Badminton"}]

    async def cdp(expr, **kw):
        if "const rows=" in expr:
            assert "t.includes(want)" not in expr
            return "norow:1"
        return 1

    async def no_sleep(*a):
        pass

    monkeypatch.setattr(wa, "list_chats", chats)
    monkeypatch.setattr(wa, "cdp", cdp)
    monkeypatch.setattr(wa.asyncio, "sleep", no_sleep)
    out = await wa._open_chat_unlocked("Saturday Badminton 12-1pm")
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_duplicate_exact_chat_names_are_rejected(monkeypatch):
    async def chats(n):
        return [{"name": "Coach"}, {"name": "Coach"}]

    monkeypatch.setattr(wa, "list_chats", chats)
    out = await wa._open_chat_unlocked("Coach")
    assert not out["ok"] and "ambiguous" in out["err"]
