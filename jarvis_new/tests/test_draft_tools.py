"""Hermetic tests for src/draft_tools.py (no Gmail, no network)."""

from __future__ import annotations

import base64
import time

import pytest
from livekit.agents.llm import ToolError

import draft_engine
import system.inbox as inbox_mod
from draft_tools import DraftTools
from system.inbox import InboxTools


def _home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))


def _draft(did: str = "m1", **kw) -> dict:
    rec = {
        "id": did,
        "thread_id": "t9",
        "to": "t@school.edu",
        "subject": "Re: Homework",
        "body": "Done, Sir.",
        "summary": "Teacher asks about homework.",
        "sender": "Teacher <t@school.edu>",
        "created": time.time(),
        "status": "pending",
        "priority": "normal",
    }
    rec.update(kw)
    return rec


def _gmail_api_fake(path: str, token: str, params=None) -> dict:
    if path == "/messages":
        return {"messages": [{"id": "m1"}]}
    return {
        "threadId": "t9",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Homework"},
                {"name": "From", "value": "Teacher <t@school.edu>"},
                {"name": "Message-ID", "value": "<orig1>"},
            ]
        },
    }


def _fake_gmail(monkeypatch: pytest.MonkeyPatch, sent: list) -> None:
    monkeypatch.setattr(inbox_mod, "_gmail_access_token", lambda: "tok")
    monkeypatch.setattr(inbox_mod, "_gmail_api", _gmail_api_fake)

    def _fake_send(token, raw, thread_id=None):
        sent.append((token, raw, thread_id))
        return {"id": "sent9"}

    monkeypatch.setattr(inbox_mod, "_gmail_send_api", _fake_send)


def test_tools_register_expected_ids() -> None:
    ids = [tool.id for tool in DraftTools(InboxTools()).tools]
    for expected in (
        "draft_list",
        "draft_read",
        "draft_revise",
        "draft_discard",
        "draft_send",
    ):
        assert expected in ids


@pytest.mark.asyncio
async def test_draft_list_empty(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _home(monkeypatch, tmp_path)
    tools = DraftTools(InboxTools())
    result = await DraftTools.draft_list(tools, None)  # type: ignore[arg-type]
    assert "No reply drafts" in result["say"]


@pytest.mark.asyncio
async def test_draft_list_with_two(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _home(monkeypatch, tmp_path)
    draft_engine.write_draft(_draft("m1"))
    draft_engine.write_draft(_draft("m2", subject="Re: Trip"))
    tools = DraftTools(InboxTools())
    result = await DraftTools.draft_list(tools, None)  # type: ignore[arg-type]
    assert "2 drafts" in result["say"]
    assert "m1" in result["say"] and "m2" in result["say"]


@pytest.mark.asyncio
async def test_draft_read(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _home(monkeypatch, tmp_path)
    draft_engine.write_draft(_draft())
    tools = DraftTools(InboxTools())
    result = await DraftTools.draft_read(tools, None, "m1")  # type: ignore[arg-type]
    assert "t@school.edu" in result["say"]
    assert "Re: Homework" in result["say"]
    assert "Done, Sir." in result["say"]


@pytest.mark.asyncio
async def test_draft_revise_updates_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _home(monkeypatch, tmp_path)
    draft_engine.write_draft(_draft())
    monkeypatch.setattr(
        draft_engine,
        "revise_reply",
        lambda record, instruction, **kw: ("Shorter body.", None),
    )
    tools = DraftTools(InboxTools())
    result = await DraftTools.draft_revise(tools, None, "m1", "shorter")  # type: ignore[arg-type]
    assert "Shorter body." in result["say"]
    assert draft_engine.read_draft("m1")["body"] == "Shorter body."


@pytest.mark.asyncio
async def test_draft_revise_warning_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _home(monkeypatch, tmp_path)
    draft_engine.write_draft(_draft())
    monkeypatch.setattr(
        draft_engine,
        "revise_reply",
        lambda record, instruction, **kw: (None, "model busy"),
    )
    tools = DraftTools(InboxTools())
    with pytest.raises(ToolError, match="model busy"):
        await DraftTools.draft_revise(tools, None, "m1", "shorter")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_draft_discard_then_read_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _home(monkeypatch, tmp_path)
    draft_engine.write_draft(_draft())
    tools = DraftTools(InboxTools())
    result = await DraftTools.draft_discard(tools, None, "m1")  # type: ignore[arg-type]
    assert "Discarded" in result["say"]
    assert draft_engine.read_draft("m1")["status"] == "discarded"
    with pytest.raises(ToolError, match="already discarded"):
        await DraftTools.draft_read(tools, None, "m1")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_draft_send_without_confirm_raises_with_readback(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _home(monkeypatch, tmp_path)
    draft = _draft()
    draft_engine.write_draft(draft)
    sent: list = []
    monkeypatch.setattr(
        inbox_mod, "_gmail_send_api", lambda *a, **k: sent.append((a, k)) or {}
    )
    tools = DraftTools(InboxTools())
    with pytest.raises(ToolError) as excinfo:
        await DraftTools.draft_send(tools, None, "m1")  # type: ignore[arg-type]
    message = str(excinfo.value)
    assert draft["to"] in message
    assert draft["subject"] in message
    assert draft["body"] in message
    assert sent == []


@pytest.mark.asyncio
async def test_draft_send_wrong_confirm_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _home(monkeypatch, tmp_path)
    draft = _draft()
    draft_engine.write_draft(draft)
    sent: list = []
    _fake_gmail(monkeypatch, sent)
    inbox = InboxTools()
    tools = DraftTools(inbox)
    await InboxTools.confirm_email_action(  # type: ignore[arg-type]
        inbox, None, draft["to"], draft["subject"], "Something else."
    )
    with pytest.raises(ToolError, match="Not authorized"):
        await DraftTools.draft_send(tools, None, "m1")  # type: ignore[arg-type]
    assert sent == []


@pytest.mark.asyncio
async def test_draft_send_correct_flow_single_use(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _home(monkeypatch, tmp_path)
    draft = _draft()
    draft_engine.write_draft(draft)
    sent: list = []
    _fake_gmail(monkeypatch, sent)
    inbox = InboxTools()
    tools = DraftTools(inbox)
    await InboxTools.confirm_email_action(  # type: ignore[arg-type]
        inbox, None, draft["to"], draft["subject"], draft["body"]
    )
    result = await DraftTools.draft_send(tools, None, "m1")  # type: ignore[arg-type]
    assert "Replied to t@school.edu" in result["say"]
    assert len(sent) == 1
    assert sent[0][0] == "tok" and sent[0][2] == "t9"
    decoded = base64.urlsafe_b64decode(sent[0][1].encode()).decode()
    assert "orig1" in decoded  # In-Reply-To threading header present
    assert draft_engine.read_draft("m1")["status"] == "sent"
    assert inbox._confirmed_draft is None  # single-use: burned by the send
    with pytest.raises(ToolError, match="already sent"):
        await DraftTools.draft_send(tools, None, "m1")  # type: ignore[arg-type]
    assert len(sent) == 1


def test_tools_register_drafts_ui_ids() -> None:
    ids = [tool.id for tool in DraftTools(InboxTools()).tools]
    assert "open_drafts_ui" in ids
    assert "close_drafts_ui" in ids


@pytest.mark.asyncio
async def test_open_drafts_ui_empty_opens_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _home(monkeypatch, tmp_path)
    import drafts_ui

    monkeypatch.setattr(drafts_ui, "pending_drafts", lambda: [])

    def _boom(*a, **k):
        raise AssertionError("shell must not run when empty")

    import projects

    monkeypatch.setattr(projects, "shell_verb", _boom)
    tools = DraftTools(InboxTools())
    result = await DraftTools.open_drafts_ui(tools, None)  # type: ignore[arg-type]
    assert result["say"] == "No drafts, Sir."


@pytest.mark.asyncio
async def test_open_drafts_ui_nonempty_opens(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _home(monkeypatch, tmp_path)
    import drafts_ui

    monkeypatch.setattr(
        drafts_ui, "pending_drafts", lambda: [_draft("m1"), _draft("m2")]
    )
    seen: list = []
    monkeypatch.setattr(
        drafts_ui,
        "open_drafts",
        lambda run=None: seen.append("open") or ("2 drafts, Sir.", True),
    )
    tools = DraftTools(InboxTools())
    result = await DraftTools.open_drafts_ui(tools, None)  # type: ignore[arg-type]
    assert "2" in result["say"]
    assert seen == ["open"]


@pytest.mark.asyncio
async def test_close_drafts_ui(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _home(monkeypatch, tmp_path)
    import drafts_ui

    seen: list = []
    monkeypatch.setattr(
        drafts_ui, "close_drafts", lambda run=None: seen.append("close") or True
    )
    tools = DraftTools(InboxTools())
    result = await DraftTools.close_drafts_ui(tools, None)  # type: ignore[arg-type]
    assert "closed" in result["say"].lower()
    assert seen == ["close"]


@pytest.mark.asyncio
async def test_discard_calls_sync_close(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _home(monkeypatch, tmp_path)
    import drafts_ui

    seen: list = []
    monkeypatch.setattr(
        drafts_ui, "sync_close", lambda run=None: seen.append("sync") or True
    )
    draft_engine.write_draft(_draft())
    draft_engine.write_draft(_draft("m2"))
    tools = DraftTools(InboxTools())
    await DraftTools.draft_discard(tools, None, "m1")  # type: ignore[arg-type]
    assert seen == ["sync"]


@pytest.mark.asyncio
async def test_send_calls_sync_close(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _home(monkeypatch, tmp_path)
    import drafts_ui

    seen: list = []
    monkeypatch.setattr(
        drafts_ui, "sync_close", lambda run=None: seen.append("sync") or True
    )
    draft = _draft()
    draft_engine.write_draft(draft)
    sent: list = []
    _fake_gmail(monkeypatch, sent)
    inbox = InboxTools()
    tools = DraftTools(inbox)
    await InboxTools.confirm_email_action(  # type: ignore[arg-type]
        inbox, None, draft["to"], draft["subject"], draft["body"]
    )
    await DraftTools.draft_send(tools, None, "m1")  # type: ignore[arg-type]
    assert len(sent) == 1
    assert seen == ["sync"]
