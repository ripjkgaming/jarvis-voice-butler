import pytest
from livekit.agents.llm import ToolError

from system.daily import (
    DailyTools,
    build_outline,
    day_events,
    format_day,
    parse_ics_events,
    render_html,
    render_markdown,
)

SAMPLE_ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20260914T080000Z
DTEND:20260914T093000Z
SUMMARY:Mathematics
LOCATION:Room 12
DESCRIPTION:Algebra homework p.42
END:VEVENT
BEGIN:VEVENT
DTSTART:20260915T080000Z
DTEND:20260915T093000Z
SUMMARY:Physics
LOCATION:Lab 3
DESCRIPTION:Generated from SIMS.net - ignore
END:VEVENT
END:VCALENDAR
"""


def test_parse_ics_events_extracts_fields() -> None:
    events = parse_ics_events(SAMPLE_ICS)
    assert len(events) == 2
    assert events[0]["t"] == "Mathematics"
    assert events[0]["loc"] == "Room 12"
    assert "Algebra" in events[0]["desc"]
    # Boilerplate-only description is cleaned.
    assert events[1]["desc"] == ""


def test_day_events_and_format() -> None:
    events = parse_ics_events(SAMPLE_ICS)
    assert len(day_events(events, "2026-09-14")) == 1
    assert "Mathematics" in format_day(day_events(events, "2026-09-14"), "2026-09-14")
    assert "No classes" in format_day([], "2026-09-16")


def test_build_outline_is_deterministic() -> None:
    a = build_outline("photosynthesis", 5)
    b = build_outline("photosynthesis", 5)
    assert a == b
    assert len(a) == 5
    assert all(head and bullets for head, bullets in a)


def test_render_markdown_and_html() -> None:
    sections = [("Intro", ["point one", "point two"])]
    md = render_markdown("Test", sections)
    assert "# Test" in md and "## Intro" in md
    hx = render_html("Test", sections)
    assert "<h1>Test</h1>" in hx and "point one" in hx


def test_daily_tools_register_expected_ids() -> None:
    ids = [tool.id for tool in DailyTools().tools]
    for expected in (
        "school_day",
        "daily_briefing",
        "study_plan",
        "study_tick",
        "build_slides",
        "build_document",
        "whatsapp_status",
        "whatsapp_chats",
        "whatsapp_read",
        "whatsapp_draft",
    ):
        assert expected in ids


@pytest.mark.asyncio
async def test_daily_tools_refuse_when_not_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JARVIS_LOCAL", raising=False)
    with pytest.raises(ToolError, match="only available"):
        await DailyTools.school_day(DailyTools(), None)  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="only available"):
        await DailyTools.build_slides(DailyTools(), None, topic="x")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_school_day_no_calendar(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr("system.daily.SCHOOL_ICS", tmp_path / "missing.ics")
    result = await DailyTools.school_day(DailyTools(), None, when="today")  # type: ignore[arg-type]
    assert "No school calendar" in result["say"]


@pytest.mark.asyncio
async def test_school_day_reads_ics(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    ics = tmp_path / "school.ics"
    ics.write_text(SAMPLE_ICS)
    monkeypatch.setattr("system.daily.SCHOOL_ICS", ics)
    monkeypatch.setattr("system.daily.SCHOOL_JSON", tmp_path / "school.json")
    result = await DailyTools.school_day(DailyTools(), None, when="2026-09-14")  # type: ignore[arg-type]
    assert "Mathematics" in result["say"]


@pytest.mark.asyncio
async def test_build_slides_writes_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr("system.daily.DOCS_DIR", tmp_path)
    result = await DailyTools.build_slides(  # type: ignore[arg-type]
        DailyTools(), None, topic="photosynthesis", slides=3
    )
    assert "3 slides" in result["say"]
    files = list(tmp_path.glob("*photosynthesis*"))
    assert len(files) >= 2


@pytest.mark.asyncio
async def test_build_document_rejects_empty_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    with pytest.raises(ToolError, match="about what"):
        await DailyTools.build_document(DailyTools(), None, topic="  ")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_whatsapp_draft_queues_for_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr("system.daily.WA_DRAFTS", tmp_path / "drafts.json")
    result = await DailyTools.whatsapp_draft(  # type: ignore[arg-type]
        DailyTools(), None, chat="Mum", text="running late, home at six"
    )
    assert "Approve it on your phone" in result["say"]


@pytest.mark.asyncio
async def test_whatsapp_draft_keeps_full_history_for_infinite_retention(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Infinite retention: drafting beyond 50 items must not trim history."""
    import json

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    drafts_path = tmp_path / "drafts.json"
    monkeypatch.setattr("system.daily.WA_DRAFTS", drafts_path)
    seed = [{"chat": "Mum", "text": f"msg {n}", "ts": 0.0} for n in range(55)]
    drafts_path.write_text(json.dumps(seed))

    await DailyTools.whatsapp_draft(  # type: ignore[arg-type]
        DailyTools(), None, chat="Mum", text="msg 56"
    )

    kept = json.loads(drafts_path.read_text())
    assert len(kept) == 56


@pytest.mark.asyncio
async def test_study_plan_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr("system.daily.STUDY_PLAN_SRC", tmp_path / "missing.json")
    result = await DailyTools.study_plan(DailyTools(), None, action="today")  # type: ignore[arg-type]
    assert result["ok"] is False
