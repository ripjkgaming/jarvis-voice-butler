"""Verified manual Google Calendar actions for the voice tool boundary."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import google_api
from action_outcome import ActionOutcome


def _target():
    from proactive.sources import calendar_source

    return calendar_source.calendar_id(), calendar_source.calendar_account()


def _same(provider, event):
    if provider.get("status") == "cancelled":
        return False
    expected = google_api.event_body(event)
    if (
        str(provider.get("summary") or "").strip().casefold()
        != expected["summary"].strip().casefold()
    ):
        return False
    for field in ("start", "end"):
        actual = provider.get(field) or {}
        want = expected[field]
        if "date" in want:
            if actual.get("date") != want["date"]:
                return False
        else:
            try:
                actual_time = dt.datetime.fromisoformat(
                    actual["dateTime"].replace("Z", "+00:00")
                )
                if actual_time.tzinfo is None:
                    actual_time = actual_time.replace(
                        tzinfo=ZoneInfo(
                            actual.get("timeZone") or google_api.local_timezone()
                        )
                    )
                want_time = dt.datetime.fromisoformat(want["dateTime"]).replace(
                    tzinfo=ZoneInfo(want["timeZone"])
                )
                if actual_time.timestamp() != want_time.timestamp():
                    return False
            except (KeyError, ValueError, TypeError):
                return False
    return (
        str(provider.get("location") or "").strip()
        == str(expected.get("location") or "").strip()
    )


def _result(outcome, *, say=None, event=None, provider=None):
    result = outcome.result()
    result["ok"] = outcome.verified
    if say:
        result["say"] = say
    if event:
        result["event"] = {
            k: event.get(k, "") for k in ("title", "date", "start", "end", "location")
        }
        result["timezone"] = google_api.local_timezone()
    if provider:
        result["event_id"] = provider.get("id", "")
        result["url"] = provider.get("htmlLink", "")
    return result


def add(event):
    """Synchronous; never returns verified until exact-ID GET matches fields."""
    from event_extractor import event_timezone

    if not isinstance(event, dict) or not str(event.get("title") or "").strip():
        raise ValueError("I need the event title and exact date and time.")
    day = dt.date.fromisoformat(str(event.get("date") or ""))
    all_day = event.get("all_day") is True
    if not all_day and (
        not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(event.get("start") or ""))
        or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(event.get("end") or ""))
    ):
        raise ValueError(
            "I need exact start and end times in HH:MM; morning is not an exact time."
        )
    event = {**event, "date": day.isoformat()}
    if all_day:
        event.update(start="", end="")
    google_api.event_body(event)
    if (
        not all_day
        and dt.datetime.fromisoformat(day.isoformat() + "T" + event["start"])
        .replace(tzinfo=event_timezone())
        .timestamp()
        < time.time()
    ):
        raise ValueError(
            "That calendar time is in the past; confirm the date and year."
        )
    calendar, account = _target()
    identity = "|".join(
        (
            account,
            calendar,
            event["title"].strip().casefold(),
            event["date"],
            event.get("start", ""),
            event.get("end", ""),
        )
    )
    event["jarvis_id"] = "jarvis" + hashlib.sha256(identity.encode()).hexdigest()
    attempted = False
    try:
        # Read existing events first; an unavailable duplicate check stops writes.
        items = google_api.calendar_list(
            dt.datetime.combine(day, dt.time(), tzinfo=event_timezone()).isoformat(),
            dt.datetime.combine(
                day + dt.timedelta(days=1), dt.time(), tzinfo=event_timezone()
            ).isoformat(),
            calendar_id=calendar,
            account=account,
        )
        existing = next((item for item in items if _same(item, event)), None)
        if existing:
            made = existing
        else:
            attempted = True
            made = google_api.calendar_create(event, calendar, account)
    except Exception:
        return _result(
            ActionOutcome(
                "calendar.create",
                "failed",
                "unknown" if attempted else "not_started",
                surface="google_calendar",
                reason="provider_write_failed",
            ),
            say="I could not confirm the Google Calendar write, Sir. It is not verified.",
        )
    try:
        provider = google_api.calendar_get(str(made["id"]), calendar, account)
    except Exception:
        return _result(
            ActionOutcome(
                "calendar.create",
                "dispatched_unverified",
                "started",
                "unavailable",
                "google_calendar",
                reason="provider_readback_unavailable",
            ),
            say="The Calendar request was sent, but the saved event is not verified, Sir.",
            event=event,
            provider=made,
        )
    if provider.get("id") != made.get("id") or not _same(provider, event):
        return _result(
            ActionOutcome(
                "calendar.create",
                "dispatched_unverified",
                "started",
                "mismatched",
                "google_calendar",
                reason="provider_readback_mismatch",
            ),
            say="Google Calendar returned different event details; the requested result is not verified, Sir.",
            event=event,
            provider=provider,
        )
    outcome = ActionOutcome(
        "calendar.create",
        "verified",
        "started",
        "matched",
        "google_calendar",
        evidence={"provider_id_matched": True, "fields_matched": True},
    )
    when = (
        f"{event['date']} {event.get('start', '')}-{event.get('end', '')} {google_api.local_timezone()}"
        if not all_day
        else f"{event['date']}, all day"
    )
    try:
        directory = Path(os.environ.get("JARVIS_HOME") or Path.home() / ".jarvis")
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "manual_calendar_receipts.jsonl").open("a") as handle:
            os.chmod(handle.name, 0o600)
            handle.write(
                json.dumps(
                    {
                        "created": time.time(),
                        "source": "owner_calendar_tool",
                        "account": account,
                        "calendar_id": calendar,
                        "event_id": provider["id"],
                        "url": provider.get("htmlLink", ""),
                        "event": {
                            k: event.get(k, "")
                            for k in ("title", "date", "start", "end", "location")
                        },
                        "timezone": google_api.local_timezone(),
                        "verified": True,
                        "already_present": bool(existing),
                    }
                )
                + "\n"
            )
    except OSError:
        pass  # Provider verification remains true even if local audit storage fails.
    return _result(
        outcome,
        say=f"{'Already on' if existing else 'Added to'} Google Calendar: {event['title']}, {when}. I checked the saved event.",
        event=event,
        provider=provider,
    )


def events_on(date):
    from event_extractor import event_timezone

    day = dt.date.fromisoformat(date)
    calendar, account = _target()
    items = google_api.calendar_list(
        dt.datetime.combine(day, dt.time(), tzinfo=event_timezone()).isoformat(),
        dt.datetime.combine(
            day + dt.timedelta(days=1), dt.time(), tzinfo=event_timezone()
        ).isoformat(),
        calendar_id=calendar,
        account=account,
    )
    response = ActionOutcome(
        "calendar.read",
        "verified",
        "not_started",
        "matched",
        "google_calendar",
        evidence={"provider_response": True, "event_count": len(items)},
    ).result()
    response.update(
        {
            "ok": True,
            "date": date,
            "timezone": google_api.local_timezone(),
            "events": [
                {
                    "id": e.get("id"),
                    "title": e.get("summary"),
                    "start": e.get("start"),
                    "end": e.get("end"),
                }
                for e in items
                if e.get("status") != "cancelled"
            ],
            "say": f"Google Calendar has {len(items)} event(s) on {date}.",
        }
    )
    return response
