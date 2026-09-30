"""Email -> calendar suggestions.

When mail mentions something datable, Jarvis stores the extracted events here
and asks Sir out loud ("shall I book it?"). A later "yes" reaches
confirm_calendar_import, which falls back to this file when nothing is pending
in memory, so a suggestion made by the background mail watcher is still
bookable inside the announcement call. Nothing is ever booked without a yes.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from pathlib import Path

TTL_S = 24 * 3600.0
ENV = "JARVIS_EMAIL_EVENTS"

_MONTHS = (
    "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|"
    "april|june|july|august|september|october|november|december"
)
_DAYS = "mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_CUE = re.compile(
    rf"\b(?:{_MONTHS})\b|\b(?:{_DAYS})\b|\b(?:today|tonight|tomorrow|next week)\b|"
    r"\b\d{1,2}[:.]\d{2}\s*(?:am|pm)?\b|\b\d{1,2}\s*(?:am|pm)\b|"
    r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\b\d{1,2}(?:st|nd|rd|th)\b",
    re.IGNORECASE,
)


def enabled() -> bool:
    return os.environ.get(ENV, "1").strip().lower() not in ("0", "false", "off", "no")


def has_date_cue(text: str) -> bool:
    """Cheap prefilter so only mail that looks datable costs a model call. Pure."""
    return bool(_CUE.search(text or ""))


def store_path() -> Path:
    home = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(home) if home else Path.home() / ".jarvis") / "event_suggestions.json"


def future_only(events: list[dict], today: dt.date | None = None) -> list[dict]:
    """Drop events dated before today. Pure."""
    today = today or dt.date.today()
    out = []
    for e in events:
        try:
            if dt.date.fromisoformat(e["date"]) >= today:
                out.append(e)
        except (KeyError, ValueError):
            continue
    return out


def save(msg_id: str, sender: str, subject: str, events: list[dict]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "ts": time.time(),
                "msg_id": msg_id,
                "sender": sender,
                "subject": subject,
                "events": events,
            }
        )
    )


def load(now: float | None = None) -> dict | None:
    """The pending suggestion, or None when absent, unreadable or older than 24 h."""
    try:
        data = json.loads(store_path().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("events"):
        return None
    if (time.time() if now is None else now) - float(data.get("ts") or 0) > TTL_S:
        return None
    return data


def clear() -> None:
    try:
        store_path().unlink()
    except OSError:
        pass


def speak_line(sender: str, events: list[dict]) -> str:
    """One spoken question for the suggestion. Pure."""
    from system.workspace_tools import describe_events

    who = re.sub(r'<.*?>|"', "", sender or "").strip() or "someone"
    return (
        f"Sir, the email from {who[:40]} mentions {describe_events(events, 2)}. "
        "Shall I book it in your calendar?"
    )
