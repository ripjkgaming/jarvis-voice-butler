"""Inbox + briefings: Gmail (readonly), news headlines, weather, morning bundle.

Ported from proven laptop Jarvis logic (~/jarvis/src/new_jarvis/):
- gmail.py: Google OAuth token at ~/jarvis/data/gmail_token.json
  (readonly scope). REST via urllib: refresh access token, list/search
  messages, read one. Sending was never supported and is not ported.
- skill_news: Google News RSS (no key), cached 15 min per query.
- skill_weather: wttr.in one-liner (no key), cached 30 min per city.

Every tool refuses unless JARVIS_LOCAL=1. Network failures become
user-facing ToolErrors, never tracebacks.
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local
from system.core import DATA_DIR, TODOS_PATH

GMAIL_TOKEN = Path.home() / "jarvis" / "data" / "gmail_token.json"
NEWS_CACHE_TTL = 15 * 60
WEATHER_CACHE_TTL = 30 * 60


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _http_get(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "jarvis"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def news_clean(s: str) -> str:
    """TTS-friendly headline: no tags/entities, no trailing ' - Source'."""
    s = re.sub(r"<[^>]+>", "", s or "")
    s = html.unescape(s)
    s = re.sub(r"\s+-\s+[^-]{2,40}$", "", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_news_rss(raw: bytes, n: int) -> list[dict]:
    """Google News RSS bytes -> [{title, link, desc, source}]. Pure."""
    try:
        root = ET.fromstring(raw)
    except Exception:
        return []
    items = []
    for item in root.iter("item"):
        title = news_clean(item.findtext("title") or "")
        if not title:
            continue
        src_el = item.find("source")
        items.append(
            {
                "title": title[:200],
                "link": (item.findtext("link") or "").strip(),
                "desc": news_clean(item.findtext("description") or "")[:300],
                "source": (
                    src_el.text.strip()[:60]
                    if src_el is not None and src_el.text
                    else ""
                ),
            }
        )
        if len(items) >= n:
            break
    return items


def parse_gmail_message(data: dict) -> dict:
    """Gmail get(full) payload -> {id, subject, sender, date, snippet, body}.

    Pure: walks MIME parts for the first text/plain block.
    """
    headers = {
        h.get("name", "").lower(): h.get("value", "")
        for h in data.get("payload", {}).get("headers", [])
        if isinstance(h, dict)
    }

    def _decode(d: str) -> str:
        try:
            pad = "=" * (-len(d) % 4)
            return base64.urlsafe_b64decode(d + pad).decode(errors="replace")
        except Exception:
            return ""

    body = ""

    def _walk(part: dict) -> None:
        nonlocal body
        if body or not isinstance(part, dict):
            return
        mime = part.get("mimeType", "")
        data_b64 = (part.get("body") or {}).get("data", "")
        if mime == "text/plain" and data_b64:
            body = _decode(data_b64)
            return
        for sub in part.get("parts", []) or []:
            _walk(sub)

    _walk(data.get("payload", {}))
    body = re.sub(r"\s+", " ", body).strip()[:2000]
    sender = headers.get("from", "")
    sender = re.sub(r"<[^>]*>", "", sender).strip()[:80]
    return {
        "id": data.get("id", ""),
        "subject": headers.get("subject", "(no subject)")[:150],
        "sender": sender or "unknown",
        "date": headers.get("date", "")[:60],
        "snippet": re.sub(r"\s+", " ", data.get("snippet", "")).strip()[:300],
        "body": body,
    }


def _gmail_access_token() -> str:
    """Refresh-token grant via urllib. Raises ToolError when unusable."""
    saved = _read_json(GMAIL_TOKEN, None)
    if not isinstance(saved, dict) or not saved.get("refresh_token"):
        raise ToolError(
            "Gmail is not connected. On the full laptop Jarvis say "
            "'connect gmail' once; I only read an existing connection."
        )
    payload = urllib.parse.urlencode(
        {
            "client_id": saved.get("client_id", ""),
            "client_secret": saved.get("client_secret", ""),
            "refresh_token": saved["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode()
    req = urllib.request.Request(
        saved.get("token_uri") or "https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            fresh = json.loads(r.read().decode())
    except Exception as exc:
        raise ToolError(f"Gmail sign-in expired ({exc}). Reconnect it.") from exc
    token = fresh.get("access_token", "")
    if not token:
        raise ToolError("Gmail sign-in expired. Reconnect it.")
    # Best-effort: persist the fresh access token for next time.
    try:
        saved["token"] = token
        GMAIL_TOKEN.write_text(json.dumps(saved))
    except Exception:
        pass
    return token


def _gmail_api(path: str, token: str, params: dict | None = None) -> dict:
    url = f"https://gmail.googleapis.com/gmail/v1/users/me{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as exc:
        raise ToolError(f"Gmail did not respond ({exc}).") from exc


WMO_DESCRIPTIONS = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Icy fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers",
    81: "Showers",
    82: "Heavy showers",
    85: "Snow showers",
    86: "Snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with hail",
}


def format_open_meteo(name: str, current: dict) -> str:
    """Open-Meteo `current` block -> spoken one-liner. Pure."""
    try:
        temp = round(float(current["temperature_2m"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError("Weather is unavailable right now.") from exc
    try:
        desc = WMO_DESCRIPTIONS.get(int(current.get("weather_code", 3)), "Cloudy")
    except (TypeError, ValueError):
        desc = "Cloudy"
    try:
        wind = round(float(current.get("wind_speed_10m", 0)))
    except (TypeError, ValueError):
        wind = 0
    return f"{name}: {temp}°C {desc}, wind {wind} km/h"


def open_meteo_line(city: str) -> str:
    """City -> weather line via Open-Meteo geocoding + forecast (no key)."""
    geo_raw = _http_get(
        "https://geocoding-api.open-meteo.com/v1/search?name="
        f"{urllib.parse.quote(city)}&count=1&language=en&format=json",
        timeout=10.0,
    )
    try:
        results = json.loads(geo_raw.decode()).get("results") or []
    except Exception as exc:
        raise ToolError(f"Weather lookup failed ({exc}).") from exc
    if not results:
        raise ToolError(f"I could not find '{city}' on the map.")
    g = results[0]
    wx_raw = _http_get(
        "https://api.open-meteo.com/v1/forecast?"
        f"latitude={g['latitude']}&longitude={g['longitude']}"
        "&current=temperature_2m,weather_code,wind_speed_10m&wind_speed_unit=kmh",
        timeout=10.0,
    )
    try:
        current = json.loads(wx_raw.decode()).get("current") or {}
    except Exception as exc:
        raise ToolError(f"Weather lookup failed ({exc}).") from exc
    name = str(g.get("name", city))[:60]
    return format_open_meteo(name, current)


def ip_city() -> str:
    """Best-guess city from IP (used only when the user names no city)."""
    try:
        data = json.loads(_http_get("https://ipinfo.io/json", timeout=10.0).decode())
    except Exception as exc:
        raise ToolError(f"Weather is unavailable ({exc}).") from exc
    city = str(data.get("city", "")).strip()[:60]
    if not city:
        raise ToolError("Weather is unavailable right now.")
    return city


def wttr_line(city: str) -> str:
    """wttr.in one-liner. Raises ToolError on outage or unknown place."""
    try:
        line = (
            _http_get(
                f"https://wttr.in/{urllib.parse.quote(city)}?format=3", timeout=10.0
            )
            .decode(errors="ignore")
            .strip()[:200]
        )
    except Exception as exc:
        raise ToolError(f"Weather is unavailable ({exc}).") from exc
    if not line or "not found" in line.lower() or "unknown" in line.lower():
        raise ToolError("Weather is unavailable right now.")
    return line


def _news_cached(url: str, key: str, n: int) -> list[dict]:
    cache = DATA_DIR / f"news-{re.sub(r'[^a-z0-9]+', '_', key.lower())[:40]}.json"
    try:
        if cache.exists() and time.time() - cache.stat().st_mtime < NEWS_CACHE_TTL:
            data = json.loads(cache.read_text())
            if isinstance(data, list):
                return data[:n]
    except Exception:
        pass
    try:
        items = parse_news_rss(_http_get(url), n)
    except Exception as exc:
        raise ToolError(f"News is unavailable ({exc}).") from exc
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(items))
    except Exception:
        pass
    return items


class InboxTools:
    """Gmail/news/weather/briefing. Register via .tools on the SystemAgent."""

    @property
    def tools(self) -> list:
        return [
            self.gmail_status,
            self.gmail_inbox,
            self.gmail_read,
            self.news_digest,
            self.weather_now,
            self.morning_briefing,
        ]

    @function_tool()
    async def gmail_status(self, context: RunContext) -> dict[str, str]:
        """Is Gmail connected (readonly OAuth)?"""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        saved = _read_json(GMAIL_TOKEN, None)
        if isinstance(saved, dict) and saved.get("refresh_token"):
            account = saved.get("account", "")
            return {
                "say": f"Gmail connected{f' as {account}' if account else ''}, read-only."
            }
        return {"say": "Gmail is not connected."}

    @function_tool()
    async def gmail_inbox(
        self, context: RunContext, query: str = "", n: int = 5
    ) -> dict[str, str]:
        """Latest inbox mail, optionally filtered by Gmail search query.

        Args:
            query: Gmail search (e.g. "from:teacher subject:homework").
            n: How many (1-10).
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        n = max(1, min(10, int(n or 5)))
        token = await asyncio.to_thread(_gmail_access_token)
        params: dict = {"maxResults": n}
        if (query or "").strip():
            params["q"] = query.strip()[:200]
        listed = await asyncio.to_thread(_gmail_api, "/messages", token, params)
        msgs = listed.get("messages", []) or []
        if not msgs:
            return {"say": "Inbox is clear for that."}
        rows = []
        for m in msgs:
            full = await asyncio.to_thread(
                _gmail_api, f"/messages/{m['id']}", token, {"format": "full"}
            )
            parsed = parse_gmail_message(full)
            rows.append(f"{parsed['sender']}: {parsed['subject']}")
        log_action("gmail", f"inbox q={query[:40]} n={len(rows)}")
        return {"say": ("Latest mail: " + "; ".join(rows))[:900]}

    @function_tool()
    async def gmail_read(
        self, context: RunContext, ref: str = "latest"
    ) -> dict[str, str]:
        """Read one email fully: latest, a message id, or "N" for Nth latest.

        Args:
            ref: "latest", a Gmail message id, or a number 1-10.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        token = await asyncio.to_thread(_gmail_access_token)
        ref = (ref or "latest").strip()
        if ref.lower() == "latest" or ref.isdigit():
            idx = 0 if ref.lower() == "latest" else max(0, min(9, int(ref) - 1))
            listed = await asyncio.to_thread(
                _gmail_api, "/messages", token, {"maxResults": idx + 1}
            )
            msgs = listed.get("messages", []) or []
            if len(msgs) <= idx:
                raise ToolError("No such email in the inbox.")
            msg_id = msgs[idx]["id"]
        else:
            msg_id = ref[:100]
        full = await asyncio.to_thread(
            _gmail_api, f"/messages/{msg_id}", token, {"format": "full"}
        )
        parsed = parse_gmail_message(full)
        log_action("gmail", f"read {msg_id[:20]}")
        text = parsed["body"] or parsed["snippet"]
        return {
            "say": (f"From {parsed['sender']}: {parsed['subject']}. {text[:1200]}")[
                :1500
            ]
        }

    @function_tool()
    async def news_digest(
        self, context: RunContext, topic: str = "", n: int = 5
    ) -> dict[str, str]:
        """Top headlines, optionally filtered by topic or search words.

        Args:
            topic: Empty for top stories, or words like "technology".
            n: How many (1-10).
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        n = max(1, min(10, int(n or 5)))
        q = (topic or "").strip()[:100]
        if q:
            url = (
                "https://news.google.com/rss/search?q="
                f"{urllib.parse.quote(q)}&hl=en-GB&gl=GB&ceid=GB:en"
            )
            label, key = f"news on {q}", f"search-{q}"
        else:
            url = "https://news.google.com/rss?hl=en-GB&gl=GB&ceid=GB:en"
            label, key = "top stories", "top"
        items = await asyncio.to_thread(_news_cached, url, key, n)
        if not items:
            return {"say": "No headlines right now."}
        log_action("news", f"{label} n={len(items)}")
        heads = "; ".join(i["title"] for i in items)
        return {"say": (f"{label}: {heads}")[:1000]}

    @function_tool()
    async def weather_now(self, context: RunContext, city: str = "") -> dict[str, str]:
        """Current weather one-liner (Open-Meteo, wttr.in fallback, 30-min cache).

        Args:
            city: City name, or empty for IP-based location.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        city = (city or "").strip()[:60]
        key = re.sub(r"[^a-z0-9]+", "_", (city or "auto").lower())[:30]
        cache = DATA_DIR / f"weather-{key}.txt"
        try:
            if (
                cache.exists()
                and time.time() - cache.stat().st_mtime < WEATHER_CACHE_TTL
            ):
                return {"say": cache.read_text()[:200]}
        except Exception:
            pass
        errors: list[str] = []
        line = ""
        if city:
            # Named city: Open-Meteo first (structured, reliable), wttr fallback.
            for attempt in (lambda: open_meteo_line(city), lambda: wttr_line(city)):
                try:
                    line = await asyncio.to_thread(attempt)
                    break
                except ToolError as exc:
                    errors.append(str(exc))
        else:
            # No city: wttr IP lookup first, then IP city -> Open-Meteo.
            try:
                line = await asyncio.to_thread(wttr_line, "auto")
            except ToolError as exc:
                errors.append(str(exc))
                try:
                    guessed = await asyncio.to_thread(ip_city)
                    line = await asyncio.to_thread(open_meteo_line, guessed)
                except ToolError as exc2:
                    errors.append(str(exc2))
        if not line:
            raise ToolError(
                errors[-1] if errors else "Weather is unavailable right now."
            )
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(line)
        except Exception:
            pass
        log_action("weather", line[:100])
        return {"say": line}

    @function_tool()
    async def morning_briefing(self, context: RunContext) -> dict[str, str]:
        """Spoken bundle: weather + top headlines + school today + todos."""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        parts: list[str] = []
        import contextlib

        with contextlib.suppress(ToolError):
            parts.append((await self.weather_now(context))["say"])
        with contextlib.suppress(ToolError):
            heads = await self.news_digest(context, "", 3)
            parts.append(heads["say"])
        try:
            from system.daily import day_events, format_day, load_school_events

            today = datetime.date.today().isoformat()
            parts.append(
                "School: " + format_day(day_events(load_school_events(), today), today)
            )
        except Exception:
            pass
        try:
            items = _read_json(TODOS_PATH, [])
            open_items = [i for i in items if not i.get("done")]
            if open_items:
                enum = "; ".join(
                    f"{n + 1}. {i['text']}" for n, i in enumerate(open_items[-4:])
                )
                parts.append("Todos: " + enum)
        except Exception:
            pass
        say = " ".join(parts)[:1400] or "Nothing to brief this morning."
        log_action("briefing", "morning")
        return {"say": say}
