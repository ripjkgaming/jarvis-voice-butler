"""Strict identity transport fixtures; never connects to WhatSie."""

import json

import pytest

from system import whatsapp as wa


@pytest.mark.parametrize(
    "snapshot",
    [
        {"names": ["Group", "Group"], "name": "Group", "ids": ["false_123@g.us_A"]},
        {"names": ["Group"], "name": "Group Extra", "ids": ["false_123@g.us_A"]},
        {"names": ["Group"], "name": "Group", "ids": []},
        {
            "names": ["Group"],
            "name": "Group",
            "ids": ["false_123@g.us_A", "false_456@g.us_B"],
        },
    ],
)
def test_ambiguous_missing_changed_identity_refuses(snapshot):
    assert not wa.approval_identity(snapshot, "Group")["ok"]


def test_identity_is_opaque_full_title_not_prefix():
    snapshot = {
        "names": ["Group With Full Title"],
        "name": "Group With Full Title",
        "ids": ["true_123-456@g.us_A", "false_123-456@g.us_B"],
    }
    assert wa.approval_identity(snapshot, "Group With Full Title") == {
        "ok": True,
        "name": "Group With Full Title",
        "jid": "123-456@g.us",
    }
    assert not wa.approval_identity(snapshot, "Group")["ok"]


@pytest.fixture
def transport(monkeypatch):
    calls = []
    state = {"jid": "123@g.us", "owner": True, "sent": False, "button": True}

    async def chats(n):
        return [{"name": "Group"}]

    async def opened(name):
        calls.append("open")
        return {"ok": True, "name": name}

    async def read(expr, **kwargs):
        if expr == wa._APPROVAL_SNAPSHOT_JS:
            return json.dumps(
                {
                    "names": ["Group"],
                    "name": "Group",
                    "ids": [f"false_{state['jid']}_A"],
                }
            )
        if "#main header" in expr:
            return "Group\nMembers"
        return json.dumps(
            [{"text": "hello", "me": state["sent"], "tailOut": state["sent"]}]
        )

    async def mutate(expr, **kwargs):
        calls.append(expr)
        assert "wrongidentity" in expr and "123@g.us" in expr
        assert "names.filter" in expr
        if "insertText" in expr:
            return "typed"
        state["sent"] = True
        return "clicked" if state["button"] else "nobtn"

    async def enter():
        raise AssertionError("strict approval must not send an unguarded Enter")

    monkeypatch.setattr(wa, "list_chats", chats)
    monkeypatch.setattr(wa, "_open_approval_chat_unlocked", opened)
    monkeypatch.setattr(wa, "cdp", read)
    monkeypatch.setattr(wa, "cdp_once", mutate)
    monkeypatch.setattr(wa, "cdp_press_enter", enter)
    return calls, state


@pytest.mark.asyncio
async def test_strict_send_identity_locked_one_attempt(transport):
    calls, state = transport
    out = await wa.send_approved_chat(
        {"name": "Group", "jid": "123@g.us"}, "hello", lambda: state["owner"]
    )
    assert out["ok"]
    assert len([c for c in calls if "b.click()" in c]) == 1
    assert not wa._page_lock.locked()


@pytest.mark.asyncio
async def test_replaced_conversation_never_mutates(transport):
    calls, state = transport
    state["jid"] = "456@g.us"
    out = await wa.send_approved_chat(
        {"name": "Group", "jid": "123@g.us"}, "hello", lambda: True
    )
    assert not out["ok"]
    assert calls == ["open"]


@pytest.mark.asyncio
async def test_revoked_before_or_after_typing_never_clicks(transport, monkeypatch):
    calls, state = transport
    orig = wa.cdp_once

    async def mutate(expr, **kwargs):
        out = await orig(expr, **kwargs)
        state["owner"] = False
        return out

    monkeypatch.setattr(wa, "cdp_once", mutate)
    out = await wa.send_approved_chat(
        {"name": "Group", "jid": "123@g.us"}, "hello", lambda: state["owner"]
    )
    assert not out["ok"]
    assert not any("b.click()" in c for c in calls)


@pytest.mark.asyncio
async def test_missing_send_button_never_falls_back_to_enter(transport):
    calls, state = transport
    state["button"] = False
    assert not (
        await wa.send_approved_chat(
            {"name": "Group", "jid": "123@g.us"}, "hello", lambda: True
        )
    )["ok"]


@pytest.mark.asyncio
async def test_review_requires_exact_name_and_never_composes(transport):
    calls, state = transport
    assert not (await wa.resolve_approval_recipient("Grou"))["ok"]
    assert not calls
    assert (await wa.resolve_approval_recipient("Group"))["jid"] == "123@g.us"
    assert calls == ["open"]


@pytest.mark.asyncio
async def test_exact_opener_vanished_row_never_uses_legacy_fuzzy_opener(monkeypatch):
    calls = []

    async def mutate(expr):
        calls.append(expr)
        assert "hits.length!==1" in expr
        assert "hits[0].click()" in expr
        return "unavailable"

    async def forbidden(*a, **k):
        pytest.fail("legacy fuzzy opener reached")

    monkeypatch.setattr(wa, "cdp_once", mutate)
    monkeypatch.setattr(wa, "_open_chat_unlocked", forbidden)
    assert not (await wa._open_approval_chat_unlocked("Group"))["ok"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_approved_long_title_never_uses_truncated_chat_list(
    transport, monkeypatch
):
    calls, state = transport
    title = "Exact Full Conversation " + "x" * 80
    original = wa.cdp

    async def forbidden(*a, **k):
        pytest.fail("truncated/fuzzy recipient list reached")

    async def read(expr, **kwargs):
        if expr == wa._APPROVAL_SNAPSHOT_JS:
            return json.dumps(
                {"names": [title], "name": title, "ids": ["false_123@g.us_A"]}
            )
        if "#main header" in expr:
            return title
        return await original(expr, **kwargs)

    monkeypatch.setattr(wa, "list_chats", forbidden)
    monkeypatch.setattr(wa, "cdp", read)
    assert (
        await wa.send_approved_chat(
            {"name": title, "jid": "123@g.us"}, "hello", lambda: True
        )
    )["ok"]


@pytest.mark.asyncio
async def test_authorization_rechecked_after_cdp_connection_before_mutation(
    monkeypatch,
):
    import asyncio

    import websockets

    connected, resume = asyncio.Event(), asyncio.Event()
    state = {"owner": True}
    commands = []

    class Connection:
        async def __aenter__(self):
            connected.set()
            await resume.wait()
            return self

        async def __aexit__(self, *a):
            pass

        async def send(self, data):
            commands.append(data)
            pytest.fail("revoked mutation reached CDP wire")

    monkeypatch.setattr(websockets, "connect", lambda *a, **k: Connection())
    task = asyncio.create_task(
        wa.cdp_evaluate("ws://fake", "send mutation", authorized=lambda: state["owner"])
    )
    await connected.wait()
    state["owner"] = False
    resume.set()
    assert await task is None
    assert not commands
