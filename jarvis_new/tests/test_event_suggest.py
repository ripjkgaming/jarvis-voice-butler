"""Email -> calendar: cue prefilter, suggestion store, extractor, confirm fallback."""

import datetime as dt
import json
import time

import backend_model
import event_suggest


def test_date_cue_prefilter() -> None:
    assert event_suggest.has_date_cue("Wedding details for Oct 3")
    assert event_suggest.has_date_cue("dentist tomorrow at 3pm")
    assert event_suggest.has_date_cue("see you 14:30")
    assert not event_suggest.has_date_cue("Hi, how are you? Weekly deals inside")


def test_future_only_drops_past_and_malformed() -> None:
    today = dt.date(2026, 9, 30)
    events = [
        {"title": "old", "date": "2026-09-29"},
        {"title": "today", "date": "2026-09-30"},
        {"title": "later", "date": "2026-10-03"},
        {"title": "bad", "date": "soon"},
    ]
    kept = event_suggest.future_only(events, today)
    assert [e["title"] for e in kept] == ["today", "later"]


def test_store_roundtrip_expiry_and_clear(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert event_suggest.load() is None
    event_suggest.save("m1", "Riley <r@x.org>", "Yo", [{"title": "Wedding", "date": "2026-10-03"}])
    got = event_suggest.load()
    assert got and got["events"][0]["title"] == "Wedding"
    assert event_suggest.load(now=time.time() + event_suggest.TTL_S + 60) is None
    event_suggest.clear()
    assert event_suggest.load() is None


def test_speak_line_asks_and_names_sender() -> None:
    line = event_suggest.speak_line(
        '"Riley Chow" <r@x.org>', [{"title": "Wedding", "date": "2026-10-03", "start": "18:00"}]
    )
    assert "Riley Chow" in line and "Wedding" in line and line.endswith("calendar?")
    assert "<" not in line


def test_kill_switch(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_EMAIL_EVENTS", "0")
    assert not event_suggest.enabled()
    monkeypatch.delenv("JARVIS_EMAIL_EVENTS")
    assert event_suggest.enabled()


def test_extract_from_email_parses_and_fences(monkeypatch) -> None:
    seen = {}

    def fake(prompt, **kw):
        seen["prompt"], seen["system"] = prompt, kw.get("system")
        return (
            json.dumps([{"title": "Wedding", "date": "2026-10-03", "start": "18:00"}]),
            None,
        )

    monkeypatch.setattr(backend_model.claude_cli, "claude_reply", fake)
    events, warning = backend_model.extract_events_from_email(
        "Wedding Saturday 3 Oct 6pm", today=dt.date(2026, 9, 30)
    )
    assert warning is None and events[0]["title"] == "Wedding"
    assert "<<<EMAIL_START>>>" in seen["prompt"] and "UNTRUSTED" in seen["system"]


def test_extract_from_email_none_is_not_a_warning(monkeypatch) -> None:
    monkeypatch.setattr(backend_model.claude_cli, "claude_reply", lambda p, **k: ("[]", None))
    assert backend_model.extract_events_from_email("hello there") == ([], None)
    assert backend_model.extract_events_from_email("   ")[1] is not None


async def test_confirm_uses_saved_suggestion_when_nothing_pending(tmp_path, monkeypatch) -> None:
    import google_api
    from system.workspace_tools import WorkspaceTools

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    event_suggest.save("m1", "Riley", "Yo", [{"title": "Wedding", "date": "2026-10-03", "start": "18:00"}])
    created = []
    monkeypatch.setattr(google_api, "calendar_list", lambda *a, **k: [])
    monkeypatch.setattr(google_api, "calendar_create", lambda e, **k: created.append(e) or {})
    out = await WorkspaceTools().confirm_calendar_import._func(WorkspaceTools(), None)
    assert created and created[0]["title"] == "Wedding"
    assert "Added 1" in out["say"]
    assert event_suggest.load() is None  # consumed
