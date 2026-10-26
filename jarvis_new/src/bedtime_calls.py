"""Staged early-event bedtime invitation; no thread or production hook yet.

Only 23:00 Singapore, only next-day timed starts in 08:20-08:30 inclusive.
Uses the existing update-call opt-in and CallQueue gates. No other-night plan.
Calendar reads contain no writes; event content never enters diagnostics.
"""

from __future__ import annotations

import datetime as dt
import time
import urllib.parse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ZONE = ZoneInfo("Asia/Singapore")
TTL = 45
MAX_PAGES = 3
_checked_day: str | None = None


class CalendarIncompleteError(Exception):
    """A complete day was not obtained within the bounded read."""


def event_start(event: dict) -> dt.datetime | None:
    """Normalize timed entries; ambiguous/missing local-zone times fail closed."""
    if event.get("status") == "cancelled":
        return None
    if any(
        isinstance(a, dict)
        and a.get("self") is True
        and a.get("responseStatus") == "declined"
        for a in event.get("attendees", []) or []
    ):
        return None
    start = event.get("start")
    if not isinstance(start, dict) or not isinstance(start.get("dateTime"), str):
        return None
    try:
        value = dt.datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
        if value.tzinfo is None:
            name = start.get("timeZone")
            if not isinstance(name, str) or not name:
                return None
            zone = ZoneInfo(name)
            first, second = (
                value.replace(tzinfo=zone, fold=0),
                value.replace(tzinfo=zone, fold=1),
            )
            if first.utcoffset() != second.utcoffset():
                return None
            if (
                first.astimezone(dt.timezone.utc).astimezone(zone).replace(tzinfo=None)
                != value
            ):
                return None
            value = first
        return value.astimezone(ZONE)
    except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
        return None


def has_early_event(events: list[dict], tomorrow: dt.date) -> bool:
    low = dt.datetime.combine(tomorrow, dt.time(8, 20), ZONE)
    high = dt.datetime.combine(tomorrow, dt.time(8, 30), ZONE)
    return any(
        (start := event_start(event)) is not None and low <= start <= high
        for event in events
        if isinstance(event, dict)
    )


def read_calendar_day(day: dt.date) -> list[dict]:
    """Bounded complete read, separate from the rolling 12-hour source cache."""
    import google_api
    from proactive.sources.calendar_source import calendar_account, calendar_id

    token = google_api._calendar_token(account=calendar_account())
    begin = dt.datetime.combine(day, dt.time(), ZONE)
    end = begin + dt.timedelta(days=1)
    params = {
        "timeMin": begin.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timeMax": end.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": 250,
    }
    calendar = urllib.parse.quote(calendar_id(), safe="")
    events, seen_tokens = [], set()
    for _ in range(MAX_PAGES):
        data = google_api._request_json(
            "GET",
            f"{google_api.CALENDAR_BASE}/{calendar}/events?{urllib.parse.urlencode(params)}",
            token,
        )
        if not isinstance(data, dict):
            raise CalendarIncompleteError()
        items = data.get("items", [])
        if not isinstance(items, list) or any(
            not isinstance(item, dict) for item in items
        ):
            raise CalendarIncompleteError()
        events.extend(items)
        next_page = data.get("nextPageToken")
        if next_page is None or next_page == "":
            return events
        if not isinstance(next_page, str) or next_page in seen_tokens:
            raise CalendarIncompleteError()
        seen_tokens.add(next_page)
        params["pageToken"] = next_page
    raise CalendarIncompleteError()


def _log(message: str) -> None:
    from system import log_action

    log_action("proactive", message)


def poll_schedule(
    queue, now: float | None = None, *, reader=None, now_fn=None, log_fn=None
) -> bool:
    """Offer one fresh invitation through CallQueue; callers own activation."""
    global _checked_day
    clock = now_fn or time.time
    now = clock() if now is None else now
    local = dt.datetime.fromtimestamp(now, ZONE)
    due = local.replace(hour=23, minute=0, second=0, microsecond=0).timestamp()
    day = local.date().isoformat()
    if not 0 <= now - due < TTL or _checked_day == day:
        return False
    with queue.db() as db:
        settings = db.execute(
            "SELECT enabled,updates FROM control WHERE id=1"
        ).fetchone()
    if not settings or not settings[0] or not settings[1]:
        return False
    _checked_day = day
    reader = reader or read_calendar_day
    log = log_fn or _log
    try:
        events = reader(local.date() + dt.timedelta(days=1))
        qualifies = has_early_event(events, local.date() + dt.timedelta(days=1))
    except Exception:
        log("bedtime calendar-unavailable; no invitation")
        return False
    delivery_now = clock()
    if not qualifies or not 0 <= delivery_now - due < TTL:
        return False
    # Generic text: no event title/location or private content enters the ledger.
    return queue.offer(
        "update",
        f"bedtime:{day}",
        "You have an early event tomorrow. Please go to bed now and wake up at 06:00.",
        delivery_now,
        deadline=due + TTL,
    )
