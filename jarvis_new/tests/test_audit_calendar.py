"""Calendar creation edge cases; all requests use a fake transport."""

import pytest
from test_google_api import FakeOpener, _token_home

import google_api


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("23:59", "", "2026-07-01T00:59:00"),
        ("23:30", "00:30", "2026-07-01T00:30:00"),
    ],
)
def test_calendar_create_rolls_end_into_next_day(
    tmp_path, monkeypatch, start, end, expected
):
    _token_home(tmp_path, monkeypatch)
    monkeypatch.setenv("JARVIS_TZ", "Asia/Singapore")
    opener = FakeOpener({"access_token": "fake"}, {"id": "created-event"})
    google_api.calendar_create(
        {"title": "Late study", "date": "2026-06-30", "start": start, "end": end},
        opener=opener,
    )
    assert opener.calls[-1][2]["end"]["dateTime"] == expected


@pytest.mark.parametrize(
    "fields",
    [
        {"date": "2026-02-30"},
        {"start": "25:00"},
        {"start": "09:00", "end": "99:00"},
        {"start": "09:00", "end": "09:00"},
        {"end_date": "2026-06-01"},
        {"end_date": "2026-02-30"},
    ],
)
def test_invalid_event_rejected_before_http(fields, monkeypatch):
    def unexpected_token(**kwargs):
        pytest.fail("Invalid event reached token/network path")

    monkeypatch.setattr(google_api, "_calendar_token", unexpected_token)
    opener = FakeOpener()
    with pytest.raises(google_api.GoogleError):
        google_api.calendar_create(
            {"title": "Study", "date": "2026-06-30", **fields}, opener=opener
        )
    assert opener.calls == []


def test_calendar_create_requires_server_event_id(tmp_path, monkeypatch):
    _token_home(tmp_path, monkeypatch)
    opener = FakeOpener({"access_token": "fake"}, {})
    with pytest.raises(google_api.GoogleError):
        google_api.calendar_create(
            {"title": "Study", "date": "2026-06-30"}, opener=opener
        )


@pytest.mark.asyncio
async def test_calendar_import_routes_configured_account_and_retains_failures(
    monkeypatch,
):
    import event_suggest
    from system.workspace_tools import WorkspaceTools

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_CALENDAR_ACCOUNT", "school")
    monkeypatch.setenv("JARVIS_CALENDAR_ID", "classes@example.test")
    calls = []
    events = [
        {"title": "Study", "date": "2026-06-30"},
        {"title": "Exam", "date": "2026-07-01"},
    ]
    tools = WorkspaceTools()
    tools._pending_events = events

    def listing(*args, **kwargs):
        calls.append(kwargs)
        return []

    def creating(event, **kwargs):
        calls.append(kwargs)
        if event["title"] == "Exam":
            raise google_api.GoogleError("Temporary service failure")
        return {"id": "created"}

    monkeypatch.setattr(google_api, "calendar_list", listing)
    monkeypatch.setattr(google_api, "calendar_create", creating)
    event_suggest.save("mail1", "Teacher", "Dates", events)
    result = await tools.confirm_calendar_import(None)
    assert "Added 1" in result["say"] and "1 failed" in result["say"]
    assert calls == [{"account": "school", "calendar_id": "classes@example.test"}] * 3
    assert tools._pending_events == events[1:]
    assert event_suggest.load()["events"] == events[1:]


@pytest.mark.asyncio
async def test_calendar_import_deduplicates_current_batch(monkeypatch):
    from system.workspace_tools import WorkspaceTools

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    tools = WorkspaceTools()
    tools._pending_events = [{"title": "Study", "date": "2026-06-30"}] * 2
    created = []
    monkeypatch.setattr(google_api, "calendar_list", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        google_api,
        "calendar_create",
        lambda event, **kwargs: created.append(event) or {"id": "new"},
    )
    result = await tools.confirm_calendar_import(None)
    assert len(created) == 1
    assert "1 were already there" in result["say"]


def test_calendar_create_readonly_token_requests_reconnect_before_network(
    tmp_path, monkeypatch
):
    import json

    _token_home(tmp_path, monkeypatch)
    google_api.token_path().write_text(
        json.dumps(
            {
                "refresh_token": "fake",
                "scopes": ["https://www.googleapis.com/auth/calendar.readonly"],
            }
        )
    )
    opener = FakeOpener()
    with pytest.raises(google_api.GoogleError, match=r"[Cc]alendar"):
        google_api.calendar_create(
            {"title": "Study", "date": "2026-06-30"}, opener=opener
        )
    assert opener.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event", [{"title": "Bad", "date": "2026-02-30"}, {"title": "Bad"}, None]
)
async def test_calendar_import_malformed_event_is_retryable_tool_error(
    monkeypatch, event
):
    from jarvis_rt import ToolError

    from system.workspace_tools import WorkspaceTools

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    tools = WorkspaceTools()
    tools._pending_events = [event]

    def unexpected(*args, **kwargs):
        pytest.fail("Invalid calendar preview must not reach API")

    monkeypatch.setattr(google_api, "calendar_list", unexpected)
    with pytest.raises(ToolError):
        await tools.confirm_calendar_import(None)
    assert tools._pending_events == [event]


@pytest.mark.asyncio
async def test_calendar_import_preserves_suggestion_replaced_during_request(
    monkeypatch,
):
    import event_suggest
    from system.workspace_tools import WorkspaceTools

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    tools = WorkspaceTools()
    old = [{"title": "Old", "date": "2026-06-30"}]
    new = [{"title": "New", "date": "2026-07-01"}]
    tools._pending_events = old
    event_suggest.save("old", "Teacher", "Old", old)
    monkeypatch.setattr(google_api, "calendar_list", lambda *args, **kwargs: [])

    def creating(*args, **kwargs):
        event_suggest.save("new", "Teacher", "New", new)
        return {"id": "created"}

    monkeypatch.setattr(google_api, "calendar_create", creating)
    await tools.confirm_calendar_import(None)
    assert event_suggest.load()["msg_id"] == "new"
    assert event_suggest.load()["events"] == new


@pytest.mark.asyncio
async def test_calendar_import_failed_dedupe_scan_never_creates_and_preserves_retry(
    monkeypatch,
):
    from jarvis_rt import ToolError

    import event_suggest
    from system.workspace_tools import WorkspaceTools

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    tools = WorkspaceTools()
    events = [{"title": "Study", "date": "2026-06-30"}]
    event_suggest.save("pending", "Teacher", "Dates", events)

    def unavailable(*args, **kwargs):
        raise google_api.GoogleError("Google did not respond")

    def unexpected(*args, **kwargs):
        pytest.fail("Must not create when duplicate detection is unavailable")

    monkeypatch.setattr(google_api, "calendar_list", unavailable)
    monkeypatch.setattr(google_api, "calendar_create", unexpected)
    with pytest.raises(ToolError):
        await tools.confirm_calendar_import(None)
    assert tools._pending_events == events
    assert event_suggest.load()["events"] == events


@pytest.mark.asyncio
async def test_calendar_import_distinguishes_times_and_matches_existing_instants(
    monkeypatch,
):
    from system.workspace_tools import WorkspaceTools

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_TZ", "Asia/Singapore")
    tools = WorkspaceTools()
    tools._pending_events = [
        {"title": "Study", "date": "2026-06-30", "start": "9:00"},
        {"title": "Study", "date": "2026-06-30", "start": "15:00"},
        {"title": "Study", "date": "2026-06-30", "start": "15:00"},
    ]
    monkeypatch.setattr(
        google_api,
        "calendar_list",
        lambda *args, **kwargs: [
            {"summary": "Study", "start": {"dateTime": "2026-06-30T01:00:00Z"}}
        ],
    )
    created = []
    monkeypatch.setattr(
        google_api,
        "calendar_create",
        lambda event, **kwargs: created.append(event) or {"id": "new"},
    )
    result = await tools.confirm_calendar_import(None)
    assert [e["start"] for e in created] == ["15:00"]
    assert "Added 1" in result["say"] and "2 were already there" in result["say"]
