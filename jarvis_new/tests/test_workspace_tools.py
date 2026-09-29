"""Hermetic tests for src/system/workspace_tools.py (modules faked, no network)."""

import pytest
from livekit.agents.llm import ToolError

import docgen
import google_api
import notion_api
from system.workspace_tools import WorkspaceTools, _draft_key, parse_rows_text


def _local(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")


def test_tools_register_expected_ids() -> None:
    ids = [tool.id for tool in WorkspaceTools().tools]
    for expected in (
        "drive_find",
        "read_google_doc",
        "create_google_doc",
        "read_sheet",
        "add_sheet_rows",
        "notion_find",
        "notion_read",
        "notion_add_note",
        "confirm_workspace_action",
        "generate_document",
    ):
        assert expected in ids


def test_parse_rows_text_separators() -> None:
    assert parse_rows_text("a; b\nc;d") == [["a", "b"], ["c", "d"]]
    assert parse_rows_text("a, b") == [["a", "b"]]
    assert parse_rows_text("  \n ") == []
    assert "sheet_rows" in _draft_key("sheet_rows", "Budget", "a;b")


@pytest.mark.asyncio
async def test_refuses_when_not_local(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_LOCAL", raising=False)
    with pytest.raises(ToolError, match="only available"):
        await WorkspaceTools.drive_find(WorkspaceTools(), None, query="x")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_drive_find_lists_hits(monkeypatch) -> None:
    _local(monkeypatch)
    monkeypatch.setattr(
        google_api,
        "drive_search",
        lambda q, **k: [{"name": "Budget"}, {"name": "Notes"}],
    )
    got = await WorkspaceTools.drive_find(WorkspaceTools(), None, query="bud")  # type: ignore[arg-type]
    assert "Budget" in got["say"]


@pytest.mark.asyncio
async def test_drive_find_empty(monkeypatch) -> None:
    _local(monkeypatch)
    monkeypatch.setattr(google_api, "drive_search", lambda q, **k: [])
    got = await WorkspaceTools.drive_find(WorkspaceTools(), None, query="zzz")  # type: ignore[arg-type]
    assert "Nothing in Drive" in got["say"]


@pytest.mark.asyncio
async def test_drive_find_maps_google_error(monkeypatch) -> None:
    _local(monkeypatch)

    def _boom(q, **k):
        raise google_api.GoogleError("Google isn't connected yet, Sir.")

    monkeypatch.setattr(google_api, "drive_search", _boom)
    with pytest.raises(ToolError, match="connected yet"):
        await WorkspaceTools.drive_find(WorkspaceTools(), None, query="x")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_read_google_doc_and_export_fallback(monkeypatch) -> None:
    _local(monkeypatch)
    monkeypatch.setattr(
        google_api,
        "resolve_file",
        lambda ref, **k: {"id": "d1", "name": "Notes", "mimeType": "x"},
    )
    monkeypatch.setattr(google_api, "drive_export", lambda fid, **k: b"plain text here")
    got = await WorkspaceTools.read_google_doc(
        WorkspaceTools(), None, name_or_id="notes"
    )  # type: ignore[arg-type]
    assert "plain text here" in got["say"]

    monkeypatch.setattr(
        google_api,
        "resolve_file",
        lambda ref, **k: {
            "id": "d2",
            "name": "Doc",
            "mimeType": "application/vnd.google-apps.document",
        },
    )
    monkeypatch.setattr(google_api, "docs_read", lambda did, **k: "doc body")
    got = await WorkspaceTools.read_google_doc(WorkspaceTools(), None, name_or_id="doc")  # type: ignore[arg-type]
    assert "doc body" in got["say"]


@pytest.mark.asyncio
async def test_create_google_doc_needs_no_confirm(monkeypatch) -> None:
    _local(monkeypatch)
    seen: dict = {}
    monkeypatch.setattr(
        google_api,
        "docs_create",
        lambda t, h, *a, **k: seen.update(title=t, html=h) or {"id": "n1"},
    )
    tools = WorkspaceTools()
    got = await tools.create_google_doc(None, title="Plan", content="a\n\nb")  # type: ignore[arg-type]
    assert "Created Plan" in got["say"] and seen["title"] == "Plan"
    assert "<p>a</p>" in seen["html"]  # paragraphs preserved


@pytest.mark.asyncio
async def test_read_sheet_formats_rows(monkeypatch) -> None:
    _local(monkeypatch)
    monkeypatch.setattr(
        google_api, "resolve_file", lambda ref, **k: {"id": "s1", "name": "Budget"}
    )
    monkeypatch.setattr(
        google_api, "sheets_read", lambda sid, r, **k: [["a", "b"], ["c"]]
    )
    got = await WorkspaceTools.read_sheet(WorkspaceTools(), None, name_or_id="budget")  # type: ignore[arg-type]
    assert "a | b" in got["say"]


@pytest.mark.asyncio
async def test_add_sheet_rows_confirm_gate(monkeypatch) -> None:
    _local(monkeypatch)
    calls: list = []
    monkeypatch.setattr(
        google_api, "resolve_file", lambda ref, **k: {"id": "s1", "name": "B"}
    )
    monkeypatch.setattr(
        google_api, "sheets_append", lambda sid, r, rows, **k: calls.append(rows) or {}
    )
    tools = WorkspaceTools()
    # Ungated write refuses.
    with pytest.raises(ToolError, match="not authorized"):
        await tools.add_sheet_rows(None, name_or_id="Budget", rows_text="a; b")  # type: ignore[arg-type]
    # Confirm with different content does not authorize this one.
    await tools.confirm_workspace_action(
        None, kind="sheet_rows", target="Budget", content="other"
    )  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="not authorized"):
        await tools.add_sheet_rows(None, name_or_id="Budget", rows_text="a; b")  # type: ignore[arg-type]
    # Exact confirm authorizes once.
    await tools.confirm_workspace_action(
        None, kind="sheet_rows", target="Budget", content="a; b"
    )  # type: ignore[arg-type]
    got = await tools.add_sheet_rows(None, name_or_id="Budget", rows_text="a; b")  # type: ignore[arg-type]
    assert "Added 1 rows" in got["say"] and calls == [[["a", "b"]]]
    # Single-use: second write refuses.
    with pytest.raises(ToolError, match="not authorized"):
        await tools.add_sheet_rows(None, name_or_id="Budget", rows_text="a; b")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_confirm_rejects_unknown_kind(monkeypatch) -> None:
    _local(monkeypatch)
    with pytest.raises(ToolError, match="only confirm"):
        await WorkspaceTools.confirm_workspace_action(  # type: ignore[arg-type]
            WorkspaceTools(), None, kind="delete_all", target="x", content="y"
        )


@pytest.mark.asyncio
async def test_notion_read_resolves_title(monkeypatch) -> None:
    _local(monkeypatch)
    monkeypatch.setattr(
        notion_api, "search", lambda q, **k: [{"id": "p1", "title": "Holiday plan"}]
    )
    monkeypatch.setattr(notion_api, "read_page", lambda pid, **k: "sun and sea")
    got = await WorkspaceTools.notion_read(WorkspaceTools(), None, name_or_id="holiday")  # type: ignore[arg-type]
    assert "Holiday plan" in got["say"] and "sun and sea" in got["say"]


@pytest.mark.asyncio
async def test_notion_add_note_confirm_gate(monkeypatch) -> None:
    _local(monkeypatch)
    calls: list = []
    monkeypatch.setattr(
        notion_api, "search", lambda q, **k: [{"id": "p1", "title": "Plan"}]
    )
    monkeypatch.setattr(
        notion_api, "append_to_page", lambda pid, t, **k: calls.append((pid, t)) or {}
    )
    tools = WorkspaceTools()
    with pytest.raises(ToolError, match="not authorized"):
        await tools.notion_add_note(None, page="Plan", text="hi")  # type: ignore[arg-type]
    await tools.confirm_workspace_action(
        None, kind="notion_note", target="Plan", content="hi"
    )  # type: ignore[arg-type]
    got = await tools.notion_add_note(None, page="Plan", text="hi")  # type: ignore[arg-type]
    assert "Noted" in got["say"] and calls == [("p1", "hi")]


@pytest.mark.asyncio
async def test_notion_find_empty(monkeypatch) -> None:
    _local(monkeypatch)
    monkeypatch.setattr(notion_api, "search", lambda q, **k: [])
    got = await WorkspaceTools.notion_find(WorkspaceTools(), None, query="zzz")  # type: ignore[arg-type]
    assert "Nothing in Notion" in got["say"]


@pytest.mark.asyncio
async def test_generate_document_needs_no_confirm(monkeypatch) -> None:
    _local(monkeypatch)
    monkeypatch.setattr(
        docgen, "draft_fields_from_request", lambda kind, req, **k: {"client": "Acme"}
    )
    monkeypatch.setattr(
        docgen,
        "deliver",
        lambda kind, fields, **k: {
            "title": "Invoice X",
            "pdf": "/tmp/x.pdf",
            "say": "Ready, Sir.",
        },
    )
    got = await WorkspaceTools.generate_document(  # type: ignore[arg-type]
        WorkspaceTools(), None, kind="invoice", request="invoice Acme for 1 x at 5"
    )
    assert got["say"] == "Ready, Sir." and got["pdf"] == "/tmp/x.pdf"


@pytest.mark.asyncio
async def test_generate_document_maps_docgen_error(monkeypatch) -> None:
    _local(monkeypatch)

    def _boom(kind, req, **k):
        raise docgen.DocgenError("What should the invoice say, Sir?")

    monkeypatch.setattr(docgen, "draft_fields_from_request", _boom)
    with pytest.raises(ToolError, match="What should"):
        await WorkspaceTools.generate_document(WorkspaceTools(), None, request="")  # type: ignore[arg-type]


# --- calendar import helpers ---

from system.workspace_tools import _event_key, describe_events


def test_describe_events_formats_preview() -> None:
    events = [
        {"title": "Mock: Maths", "date": "2026-06-01", "start": "09:00"},
        {"title": "Sports Day", "date": "2026-06-02", "start": ""},
    ]
    say = describe_events(events)
    assert "Mock: Maths" in say and "09:00" in say
    assert "Sports Day" in say
    # All-day entry has no "at" suffix.
    assert "Sports Day, " in say


def test_describe_events_truncates_with_more() -> None:
    events = [{"title": f"E{i}", "date": "2026-06-01", "start": ""} for i in range(10)]
    say = describe_events(events, limit=8)
    assert "and 2 more" in say
    assert "E9" not in say


def test_event_key_normalizes() -> None:
    assert _event_key("Mock: Maths!", "2026-06-01") == _event_key(
        "mock maths", "2026-06-01"
    )
    assert _event_key("Maths", "2026-06-01") != _event_key("Maths", "2026-06-02")
    assert _event_key("Maths", "2026-06-01") != _event_key("Physics", "2026-06-01")


@pytest.mark.asyncio
async def test_confirm_calendar_import_nothing_pending(monkeypatch) -> None:
    _local(monkeypatch)
    tools = WorkspaceTools()
    assert tools._pending_events == []
    with pytest.raises(ToolError, match="nothing waiting"):
        await tools.confirm_calendar_import(None)  # type: ignore[arg-type]


# --- background backend behaviour ---

import asyncio as _bg_asyncio
import time as _bg_time
from pathlib import Path as _BgPath
from types import SimpleNamespace as _SimpleNamespace

import backend_model as _backend_model


class _FakeSession:
    """Minimal voice session: records generate_reply instructions and say lines."""

    def __init__(self, fail_reply: bool = False) -> None:
        self.instructions: str | None = None
        self.said: list[str] = []
        self._fail = fail_reply

    async def generate_reply(self, instructions: str | None = None, **kwargs):  # type: ignore[no-untyped-def]
        if self._fail:
            raise RuntimeError("realtime balked")
        self.instructions = instructions
        return "ok"

    async def say(self, text: str, **kwargs):  # type: ignore[no-untyped-def]
        self.said.append(text)
        return "ok"


def _fake_ctx(session: _FakeSession):  # type: ignore[no-untyped-def]
    return _SimpleNamespace(session=session)


@pytest.mark.asyncio
async def test_ask_backend_returns_at_once_and_delivers_answer(monkeypatch) -> None:
    _local(monkeypatch)

    def _think(task: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        _bg_time.sleep(0.2)
        return ("ANSWER", None)

    monkeypatch.setattr(_backend_model, "backend_think", _think)
    tools = WorkspaceTools()
    session = _FakeSession()
    ctx = _fake_ctx(session)

    got = await tools.ask_backend(ctx, task="plan the away day carefully")  # type: ignore[arg-type]
    assert "On it" in got["say"]
    # Background job is still running: reply not delivered yet.
    assert session.instructions is None
    assert len(tools._tasks) >= 1

    await _bg_asyncio.gather(*list(tools._tasks))
    assert session.instructions is not None and "ANSWER" in session.instructions


@pytest.mark.asyncio
async def test_ask_backend_generate_reply_failure_falls_back_to_say(monkeypatch) -> None:
    _local(monkeypatch)

    def _think(task: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        return ("ANSWER", None)

    monkeypatch.setattr(_backend_model, "backend_think", _think)
    tools = WorkspaceTools()
    session = _FakeSession(fail_reply=True)
    ctx = _fake_ctx(session)

    got = await tools.ask_backend(ctx, task="do a careful plan")  # type: ignore[arg-type]
    assert "On it" in got["say"]
    await _bg_asyncio.gather(*list(tools._tasks))
    assert session.said, "expected fallback say when generate_reply raises"
    assert "backend has finished" in session.said[0].lower()


@pytest.mark.asyncio
async def test_ask_backend_warning_mentions_failure(monkeypatch) -> None:
    _local(monkeypatch)

    def _think(task: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        return ("", "disk exploded")

    monkeypatch.setattr(_backend_model, "backend_think", _think)
    tools = WorkspaceTools()
    session = _FakeSession()
    ctx = _fake_ctx(session)

    await tools.ask_backend(ctx, task="plan something big")  # type: ignore[arg-type]
    await _bg_asyncio.gather(*list(tools._tasks))
    assert session.instructions is not None
    assert "failed" in session.instructions.lower()
    assert "disk exploded" in session.instructions


@pytest.mark.asyncio
async def test_import_schedule_background_flow_and_busy(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _local(monkeypatch)
    schedule = tmp_path / "mock-timetable.pdf"
    schedule.write_text("mock")
    events = [
        {"title": "Maths mock", "date": "2026-06-01", "start": "09:00"},
        {"title": "Physics mock", "date": "2026-06-02", "start": "13:00"},
    ]

    def _find(hint: str = "", *args, **kwargs):  # type: ignore[no-untyped-def]
        return schedule

    def _extract(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        _bg_time.sleep(0.2)
        return (events, None)

    monkeypatch.setattr(_backend_model, "find_schedule_file", _find)
    monkeypatch.setattr(_backend_model, "extract_events", _extract)

    tools = WorkspaceTools()
    session = _FakeSession()
    ctx = _fake_ctx(session)

    got = await tools.import_schedule_to_calendar(ctx, file_hint="mock")  # type: ignore[arg-type]
    assert "Reading" in got["say"]
    # Still extracting in the background.
    assert tools._importing is True
    assert session.instructions is None

    await _bg_asyncio.gather(*list(tools._tasks))
    assert tools._pending_events == events
    assert tools._importing is False
    assert session.instructions is not None
    assert "2" in session.instructions
    assert "confirm_calendar_import" in session.instructions

    # A second call while a read is in flight is refused politely.
    tools._importing = True
    busy = await tools.import_schedule_to_calendar(ctx, file_hint="mock")  # type: ignore[arg-type]
    assert "still reading" in busy["say"].lower()


@pytest.mark.asyncio
async def test_import_schedule_warning_mentions_failure(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _local(monkeypatch)
    schedule = tmp_path / "mock-timetable.pdf"
    schedule.write_text("mock")
    monkeypatch.setattr(
        _backend_model, "find_schedule_file", lambda hint="", *a, **k: schedule
    )
    monkeypatch.setattr(
        _backend_model,
        "extract_events",
        lambda path, *a, **k: ([], "file is gibberish"),
    )
    tools = WorkspaceTools()
    session = _FakeSession()
    ctx = _fake_ctx(session)

    await tools.import_schedule_to_calendar(ctx, file_hint="mock")  # type: ignore[arg-type]
    await _bg_asyncio.gather(*list(tools._tasks))
    assert tools._importing is False
    assert session.instructions is not None
    assert "failed" in session.instructions.lower()


@pytest.mark.asyncio
async def test_import_schedule_no_file_raises(monkeypatch) -> None:
    _local(monkeypatch)
    monkeypatch.setattr(
        _backend_model, "find_schedule_file", lambda hint="", *a, **k: None
    )
    tools = WorkspaceTools()
    with pytest.raises(ToolError, match="can't find"):
        await tools.import_schedule_to_calendar(  # type: ignore[arg-type]
            _fake_ctx(_FakeSession()), file_hint="missing"
        )
