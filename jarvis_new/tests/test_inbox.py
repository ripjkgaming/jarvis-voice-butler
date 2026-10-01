import base64
import json

import pytest
from livekit.agents.llm import ToolError

import system.inbox as inbox_mod
from system.inbox import (
    InboxTools,
    _gmail_access_token,
    format_open_meteo,
    ip_city,
    news_clean,
    open_meteo_line,
    parse_gmail_message,
    parse_news_rss,
    wttr_line,
)

RSS_FIXTURE = b"""<?xml version="1.0"?>
<rss><channel>
<item><title>School wins final &amp; celebrates</title>
<link>https://example.com/1</link>
<description>Great <b>win</b> today - Example News</description>
<source>Example News</source></item>
<item><title>Markets rally</title><link>https://example.com/2</link>
<description>Stocks up</description></item>
</channel></rss>
"""


def _gmail_fixture() -> dict:
    body = base64.urlsafe_b64encode(b"Homework is page 42.  Thanks!").decode()
    return {
        "id": "abc123",
        "snippet": "Homework is page",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Maths homework"},
                {"name": "From", "value": "Teacher <teacher@school.edu>"},
                {"name": "Date", "value": "Mon, 14 Sep 2026 08:00:00 +0000"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": body},
                }
            ],
        },
    }


def test_news_clean_strips_markup_and_source_suffix() -> None:
    assert news_clean("Win today <b>big</b> - Example News") == "Win today big"
    assert news_clean("A &amp; B") == "A & B"


def test_parse_news_rss_extracts_items() -> None:
    items = parse_news_rss(RSS_FIXTURE, 5)
    assert len(items) == 2
    assert items[0]["title"] == "School wins final & celebrates"
    assert items[0]["source"] == "Example News"
    assert items[0]["link"] == "https://example.com/1"


def test_parse_news_rss_rejects_garbage() -> None:
    assert parse_news_rss(b"not xml at all", 5) == []


def test_parse_gmail_message_extracts_fields() -> None:
    parsed = parse_gmail_message(_gmail_fixture())
    assert parsed["subject"] == "Maths homework"
    assert parsed["sender"] == "Teacher"
    assert "page 42" in parsed["body"]
    assert parsed["snippet"] == "Homework is page"


def test_inbox_tools_register_expected_ids() -> None:
    ids = [tool.id for tool in InboxTools().tools]
    for expected in (
        "gmail_status",
        "gmail_inbox",
        "gmail_read",
        "news_digest",
        "weather_now",
        "morning_briefing",
    ):
        assert expected in ids


@pytest.mark.asyncio
async def test_inbox_tools_refuse_when_not_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JARVIS_LOCAL", raising=False)
    with pytest.raises(ToolError, match="only available"):
        await InboxTools.gmail_status(InboxTools(), None)  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="only available"):
        await InboxTools.news_digest(InboxTools(), None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_gmail_refuses_without_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr("system.inbox.GMAIL_TOKEN", tmp_path / "missing.json")
    result = await InboxTools.gmail_status(InboxTools(), None)  # type: ignore[arg-type]
    assert "not connected" in result["say"]
    # No separate token and no shared token: clear re-auth hint, not a 403.
    with pytest.raises(ToolError, match="google_auth"):
        await InboxTools.gmail_inbox(InboxTools(), None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_news_digest_uses_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr("system.inbox.DATA_DIR", tmp_path)
    cache = tmp_path / "news-top.json"
    cache.write_text(json.dumps([{"title": "Cached headline"}]))
    result = await InboxTools.news_digest(InboxTools(), None, topic="")  # type: ignore[arg-type]
    assert "Cached headline" in result["say"]


@pytest.mark.asyncio
async def test_weather_now_uses_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr("system.inbox.DATA_DIR", tmp_path)
    cache = tmp_path / "weather-testville.txt"
    cache.write_text("Testville: Sunny 21C")
    result = await InboxTools.weather_now(InboxTools(), None, city="Testville")  # type: ignore[arg-type]
    assert "Sunny" in result["say"]


def test_gmail_token_refresh_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr("system.inbox.GMAIL_TOKEN", tmp_path / "missing.json")
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    # Falls back to the shared login; with no shared token either the
    # re-auth hint surfaces (never a raw 403 or traceback).
    with pytest.raises(ToolError, match="google_auth"):
        _gmail_access_token()


def test_format_open_meteo_line() -> None:
    line = format_open_meteo(
        "London", {"temperature_2m": 17.4, "weather_code": 2, "wind_speed_10m": 12.6}
    )
    assert line == "London: 17°C Partly cloudy, wind 13 km/h"


def test_format_open_meteo_rejects_empty() -> None:
    with pytest.raises(ToolError, match="unavailable"):
        format_open_meteo("London", {})


def test_open_meteo_line_parses_geocode_and_forecast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url: str, timeout: float = 15.0) -> bytes:
        if "geocoding" in url:
            return json.dumps(
                {"results": [{"name": "Paris", "latitude": 48.85, "longitude": 2.35}]}
            ).encode()
        return json.dumps(
            {
                "current": {
                    "temperature_2m": 21.2,
                    "weather_code": 0,
                    "wind_speed_10m": 8.0,
                }
            }
        ).encode()

    monkeypatch.setattr("system.inbox._http_get", fake_get)
    assert open_meteo_line("paris") == "Paris: 21°C Clear sky, wind 8 km/h"


def test_open_meteo_line_unknown_city(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "system.inbox._http_get", lambda url, timeout=15.0: b'{"results": []}'
    )
    with pytest.raises(ToolError, match="could not find"):
        open_meteo_line("nowhereville")


def test_wttr_line_rejects_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "system.inbox._http_get",
        lambda url, timeout=15.0: b"location not found: location not found",
    )
    with pytest.raises(ToolError, match="unavailable"):
        wttr_line("London")


@pytest.mark.asyncio
async def test_weather_falls_back_to_open_meteo(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr("system.inbox.DATA_DIR", tmp_path)
    monkeypatch.setattr(
        "system.inbox._http_get",
        lambda url, timeout=15.0: (_ for _ in ()).throw(TimeoutError("slow")),
    )
    monkeypatch.setattr(
        inbox_mod, "open_meteo_line", lambda city: f"{city}: 20°C Clear sky"
    )
    result = await InboxTools.weather_now(InboxTools(), None, city="Berlin")  # type: ignore[arg-type]
    assert "Berlin" in result["say"]


@pytest.mark.asyncio
async def test_weather_auto_uses_ip_city_then_open_meteo(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr("system.inbox.DATA_DIR", tmp_path)
    monkeypatch.setattr(
        inbox_mod,
        "wttr_line",
        lambda city: (_ for _ in ()).throw(
            ToolError("Weather is unavailable right now.")
        ),
    )
    monkeypatch.setattr(inbox_mod, "ip_city", lambda: "Leeds")
    monkeypatch.setattr(inbox_mod, "open_meteo_line", lambda city: f"{city}: 15°C Rain")
    result = await InboxTools.weather_now(InboxTools(), None, city="")  # type: ignore[arg-type]
    assert "Leeds" in result["say"]


def test_ip_city_parses_ipinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "system.inbox._http_get",
        lambda url, timeout=15.0: b'{"city": "Leeds", "country": "GB"}',
    )
    assert ip_city() == "Leeds"


def test_send_tools_registered() -> None:
    ids = [tool.id for tool in InboxTools().tools]
    for expected in ("confirm_email_action", "gmail_send", "gmail_reply"):
        assert expected in ids


def test_draft_key_ignores_case_and_spacing() -> None:
    from system.inbox import _draft_key

    assert _draft_key("A@b.com", "Hi", "x") == _draft_key("a@B.COM", "  hi ", "X")


def test_extract_email_handles_display_names() -> None:
    from system.inbox import _extract_email

    assert _extract_email("Teacher <t@school.edu>") == "t@school.edu"
    assert _extract_email("plain@x.io") == "plain@x.io"
    assert _extract_email("no address here") == ""


def test_mime_roundtrips_through_base64() -> None:
    from system.inbox import build_mime_b64

    raw = build_mime_b64("a@b.com", "Sub", "Body text", "<mid123>")
    decoded = base64.urlsafe_b64decode(raw.encode()).decode()
    assert "a@b.com" in decoded and "Sub" in decoded and "Body text" in decoded
    assert "mid123" in decoded


@pytest.mark.asyncio
async def test_send_refuses_without_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    calls: list = []
    monkeypatch.setattr(
        inbox_mod, "_gmail_send_api", lambda *a, **k: calls.append((a, k)) or {}
    )
    tools = InboxTools()
    with pytest.raises(ToolError, match="not authorized"):
        await InboxTools.gmail_send(tools, None, "a@b.com", "Hi", "yo")  # type: ignore[arg-type]
    assert calls == []


@pytest.mark.asyncio
async def test_send_fires_once_on_exact_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(inbox_mod, "_gmail_access_token", lambda: "tok")
    sent: list = []

    def _fake_send(token, raw, thread_id=None):
        sent.append((token, raw, thread_id))
        return {"id": "sent123"}

    monkeypatch.setattr(inbox_mod, "_gmail_send_api", _fake_send)
    tools = InboxTools()
    await InboxTools.confirm_email_action(tools, None, "A@b.com", "Hi", "yo")  # type: ignore[arg-type]
    result = await InboxTools.gmail_send(tools, None, "a@B.COM", "hi", "YO")  # type: ignore[arg-type]
    assert "Sent to a@b.com" in result["say"]
    assert len(sent) == 1 and sent[0][0] == "tok" and sent[0][2] is None
    # Single-use: second send without re-confirm refuses.
    with pytest.raises(ToolError, match="not authorized"):
        await InboxTools.gmail_send(tools, None, "a@b.com", "Hi", "yo")  # type: ignore[arg-type]
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_send_rejects_bad_recipient_after_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    tools = InboxTools()
    with pytest.raises(ToolError, match="not a valid recipient"):
        await InboxTools.confirm_email_action(tools, None, "not-an-email", "Hi", "yo")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_reply_threads_and_matches_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(inbox_mod, "_gmail_access_token", lambda: "tok")
    monkeypatch.setattr(
        inbox_mod,
        "_gmail_api",
        lambda path, token, params=None: (
            {"messages": [{"id": "m1"}]}
            if path == "/messages"
            else {
                "threadId": "t9",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Homework"},
                        {"name": "From", "value": "Teacher <t@school.edu>"},
                        {"name": "Message-ID", "value": "<orig1>"},
                    ]
                },
            }
        ),
    )
    sent: list = []

    def _fake_send(token, raw, thread_id=None):
        sent.append((token, raw, thread_id))
        return {"id": "sent9"}

    monkeypatch.setattr(inbox_mod, "_gmail_send_api", _fake_send)
    tools = InboxTools()
    await InboxTools.confirm_email_action(  # type: ignore[arg-type]
        tools, None, "t@school.edu", "Re: Homework", "Done, Sir."
    )
    result = await InboxTools.gmail_reply(tools, None, "latest", "Done, Sir.")  # type: ignore[arg-type]
    assert "Replied to t@school.edu" in result["say"]
    assert len(sent) == 1 and sent[0][2] == "t9"
    decoded = base64.urlsafe_b64decode(sent[0][1].encode()).decode()
    assert "orig1" in decoded  # In-Reply-To threading header present


def test_classify_school_and_urgent_is_high() -> None:
    from system.inbox import classify_email

    v = classify_email("Teacher <t@sji-international.com.sg>", "Reminder", "hi")
    assert v["level"] == "high" and v["human"] is True
    v = classify_email("Boss <b@corp.com>", "URGENT: call me", "hi")
    assert v["level"] == "high"


def test_classify_bulk_never_human() -> None:
    from system.inbox import classify_email

    full = {
        "payload": {
            "headers": [
                {"name": "List-Unsubscribe", "value": "<mailto:x>"},
                {"name": "From", "value": "News <news@site.com>"},
            ]
        }
    }
    v = classify_email("News <news@site.com>", "URGENT deals", "buy", full)
    assert v["level"] == "high" and v["human"] is False


def test_classify_self_and_plain() -> None:
    import os

    from system.inbox import classify_email

    os.environ["JARVIS_OWNER_EMAIL"] = "me@gmail.com"
    try:
        v = classify_email("Me <me@gmail.com>", "test", "hi")
        assert v["level"] == "skip"
        v = classify_email("Friend <f@x.com>", "hello", "how are you")
        assert v["level"] == "normal" and v["human"] is True
    finally:
        del os.environ["JARVIS_OWNER_EMAIL"]


def test_autoreply_template_names_subject() -> None:
    from system.inbox import autoreply_body

    body = autoreply_body("Exam on Friday")
    assert "Exam on Friday" in body and "automatic" in body


def _fake_urlopen_refresh(payload: dict):
    """urllib urlopen stub returning one JSON payload (separate-token flow)."""

    class _Resp:
        def read(self) -> bytes:
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open(req, timeout=None):
        return _Resp()

    return _open


def test_gmail_access_token_prefers_separate_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import urllib.request

    import google_api

    token_file = tmp_path / "gmail_token.json"
    token_file.write_text(
        json.dumps(
            {
                "client_id": "cid",
                "client_secret": "csec",
                "refresh_token": "separate-rt",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )
    )
    monkeypatch.setattr("system.inbox.GMAIL_TOKEN", token_file)
    monkeypatch.setattr(
        urllib.request, "urlopen", _fake_urlopen_refresh({"access_token": "sep-tok"})
    )

    def _boom(*a, **k):
        raise AssertionError("shared Google token must not be used")

    monkeypatch.setattr(google_api, "access_token", _boom)
    assert _gmail_access_token() == "sep-tok"


def test_gmail_access_token_falls_back_to_shared(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import google_api

    monkeypatch.setattr("system.inbox.GMAIL_TOKEN", tmp_path / "missing.json")
    monkeypatch.setattr(google_api, "_has_gmail_scope", lambda account="personal": True)
    monkeypatch.setattr(google_api, "access_token", lambda *a, **k: "shared-tok")
    assert _gmail_access_token() == "shared-tok"


def test_gmail_access_token_raises_hint_without_scopes(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import google_api

    monkeypatch.setattr("system.inbox.GMAIL_TOKEN", tmp_path / "missing.json")
    monkeypatch.setattr(
        google_api, "_has_gmail_scope", lambda account="personal": False
    )
    calls: list = []
    monkeypatch.setattr(
        google_api, "access_token", lambda *a, **k: calls.append(1) or "unused"
    )
    with pytest.raises(ToolError, match="google_auth"):
        _gmail_access_token()
    assert calls == []
    assert "Sir" in google_api.GMAIL_SCOPE_HINT


def test_gmail_access_token_shared_failure_surfaces_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import google_api

    monkeypatch.setattr("system.inbox.GMAIL_TOKEN", tmp_path / "missing.json")
    monkeypatch.setattr(google_api, "_has_gmail_scope", lambda account="personal": True)

    def _fail(*a, **k):
        raise google_api.GoogleError(google_api.SETUP_HINT)

    monkeypatch.setattr(google_api, "access_token", _fail)
    with pytest.raises(ToolError, match="google_auth"):
        _gmail_access_token()


@pytest.mark.asyncio
async def test_reply_refuses_without_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(inbox_mod, "_gmail_access_token", lambda: "tok")
    monkeypatch.setattr(
        inbox_mod,
        "_gmail_api",
        lambda path, token, params=None: (
            {"messages": [{"id": "m1"}]}
            if path == "/messages"
            else {
                "threadId": "t9",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Homework"},
                        {"name": "From", "value": "Teacher <t@school.edu>"},
                        {"name": "Message-ID", "value": "<orig1>"},
                    ]
                },
            }
        ),
    )
    calls: list = []
    monkeypatch.setattr(
        inbox_mod, "_gmail_send_api", lambda *a, **k: calls.append((a, k)) or {}
    )
    tools = InboxTools()
    with pytest.raises(ToolError, match="not authorized"):
        await InboxTools.gmail_reply(tools, None, "latest", "Done, Sir.")  # type: ignore[arg-type]
    assert calls == []


def test_autoreply_shares_owner_phone(monkeypatch) -> None:
    from system.inbox import autoreply_body

    monkeypatch.delenv("JARVIS_OWNER_PHONE", raising=False)
    assert "+65 8753 4735" in autoreply_body("Exam")
    monkeypatch.setenv("JARVIS_OWNER_PHONE", "")
    body = autoreply_body("Exam")
    assert "+65" not in body and "phone or WhatsApp" in body
    monkeypatch.setenv("JARVIS_OWNER_PHONE", "+1 555 0100")
    assert "+1 555 0100" in autoreply_body("Exam")


# --- parsing upgrades, listing, threads ---


def _b64s(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def test_parse_html_only_mail_and_attachments() -> None:
    data = {
        "id": "h1",
        "threadId": "t1",
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "headers": [{"name": "Subject", "value": "News"}],
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {
                        "data": _b64s(
                            "<style>x{}</style><p>Hello &amp; welcome</p><br>Line 2"
                        )
                    },
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "timetable.pdf",
                    "body": {},
                },
            ],
        },
    }
    parsed = parse_gmail_message(data)
    assert parsed["body"] == "Hello & welcome Line 2"
    assert parsed["unread"] is True and parsed["thread_id"] == "t1"
    assert parsed["attachments"] == ["timetable.pdf"]


def test_strip_quoted_history() -> None:
    body = "Sounds good!\n\nOn Mon, 29 Sep 2026, Bob <b@x.com> wrote:\n> old stuff"
    assert inbox_mod.strip_quoted(body) == "Sounds good!"
    assert inbox_mod.strip_quoted("> only quote") == "> only quote"


def test_auto_reply_mail_is_not_human() -> None:
    full = {
        "payload": {"headers": [{"name": "Subject", "value": "Automatic reply: Hi"}]}
    }
    assert inbox_mod._is_bulk(full, "Bob <bob@x.com>") is True
    ooo = {"payload": {"headers": [{"name": "Subject", "value": "Out of Office"}]}}
    assert inbox_mod._is_bulk(ooo, "bob@x.com") is True
    plain = {"payload": {"headers": [{"name": "Subject", "value": "Office hours?"}]}}
    assert inbox_mod._is_bulk(plain, "bob@x.com") is False


def test_auto_mime_headers() -> None:
    raw = inbox_mod.build_mime_b64(
        "a@b.c", "Re: x", "hi", "<m1>", auto=True, references="<m0>"
    )
    text = base64.urlsafe_b64decode(raw.encode()).decode()
    assert "Auto-Submitted: auto-replied" in text
    assert "References: <m0> <m1>" in text


def test_inbox_row_marks_new_and_age() -> None:
    import datetime as _dt

    now = _dt.datetime(2026, 9, 29, 12, 0, tzinfo=_dt.timezone.utc).timestamp()
    row = inbox_mod.inbox_row(
        2,
        {
            "sender": "Bob",
            "subject": "Hi",
            "unread": True,
            "date": "Mon, 29 Sep 2026 09:00:00 +0000",
        },
        now,
    )
    assert row == "2. Bob: Hi (new, 3h ago)"
    assert inbox_mod.inbox_row(1, {"sender": "A", "subject": "S"}) == "1. A: S"


def test_thread_digest_last_n() -> None:
    msgs = [
        {
            "id": str(i),
            "payload": {"headers": [{"name": "From", "value": f"P{i}"}]},
            "snippet": f"s{i}",
        }
        for i in range(4)
    ]
    out = inbox_mod.thread_digest(msgs, 2)
    assert out.startswith("(2 earlier messages skipped)")
    assert "P3" in out and "P0" not in out


@pytest.mark.asyncio
async def test_gmail_inbox_numbers_and_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(inbox_mod, "_gmail_access_token", lambda: "tok")
    calls = []

    def api(path, token, params=None):
        calls.append((path, params))
        if path == "/messages":
            return {"messages": [{"id": "a"}, {"id": "b"}]}
        return {
            "id": path[-1],
            "labelIds": ["UNREAD"] if path.endswith("a") else [],
            "payload": {
                "headers": [
                    {"name": "From", "value": f"S{path[-1]}"},
                    {"name": "Subject", "value": "Hey"},
                ]
            },
        }

    monkeypatch.setattr(inbox_mod, "_gmail_api", api)
    out = await InboxTools.gmail_inbox(InboxTools(), None)  # type: ignore[arg-type]
    assert "1. Sa: Hey (new)" in out["say"] and "2. Sb: Hey" in out["say"]
    assert calls[0][1]["labelIds"] == "INBOX"
    await InboxTools.gmail_inbox(InboxTools(), None, unread_only=True)  # type: ignore[arg-type]
    assert calls[3][1]["q"] == "is:unread"


def test_mail_log_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import mail_log

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    mail_log.record("acked", to="t@school.edu", subject="Re: Form", ts=100)
    mail_log.record("flagged", sender="Bob <b@x.com>", subject="Urgent", ts=200)
    mail_log.record("bogus", subject="ignored")
    assert [e["kind"] for e in mail_log.recent()] == ["flagged", "acked"]
    assert [e["kind"] for e in mail_log.recent(kinds=("acked",))] == ["acked"]
    assert mail_log.recent(who="bob")[0]["subject"] == "Urgent"
    assert mail_log.recent(since=150)[0]["kind"] == "flagged"


def test_passon_body_is_relaxed_and_urgent_body_is_urgent(monkeypatch) -> None:
    from system.inbox import autoreply_body, passon_body

    monkeypatch.delenv("JARVIS_OWNER_PHONE", raising=False)
    relaxed, urgent = passon_body("Lunch?"), autoreply_body("Lunch?")
    assert "Will pass it on" in relaxed and "Lunch?" in relaxed
    assert "automatic" in relaxed and "URGENT" not in relaxed
    assert "URGENT" in urgent and "Will pass it on" not in urgent
    assert "+65 8753 4735" in relaxed
    monkeypatch.setenv("JARVIS_OWNER_PHONE", "")
    assert "phone or WhatsApp" in passon_body("x")


def test_scam_body_is_dry_and_shares_no_contact() -> None:
    from system.inbox import scam_body

    body = scam_body("You won a prize")
    assert "scam" in body and "You won a prize" in body
    assert "+65" not in body and "WhatsApp" not in body
