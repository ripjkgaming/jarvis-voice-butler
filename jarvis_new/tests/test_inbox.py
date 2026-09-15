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
    monkeypatch.setattr("system.inbox.GMAIL_TOKEN", tmp_path / "missing.json")
    result = await InboxTools.gmail_status(InboxTools(), None)  # type: ignore[arg-type]
    assert "not connected" in result["say"]
    with pytest.raises(ToolError, match="not connected"):
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
    with pytest.raises(ToolError, match="not connected"):
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
