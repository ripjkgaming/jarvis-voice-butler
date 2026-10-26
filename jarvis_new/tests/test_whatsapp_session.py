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
