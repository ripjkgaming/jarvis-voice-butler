"""Read-only calendar provenance and narrowly scoped dismiss actions."""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import time

import event_extractor as ex


def _provider_checks():
    import whatsapp_calendar as wc

    try:
        value = json.loads(
            (wc.home() / "auto_calendar_provider_checks.json").read_text()
        )
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def record_provider_check(event, *, account, calendar_id, now=None):
    """Record observed provider state without rewriting historical add receipts."""
    import whatsapp_calendar as wc

    if not event.get("id") or event.get("status") not in (
        "confirmed",
        "tentative",
        "cancelled",
    ):
        raise ValueError("A confirmed provider response is required")
    with wc._lock("auto_calendar_provider_checks"):
        checks = _provider_checks()
        checks[str(event["id"])] = {
            "event_id": str(event["id"]),
            "status": event["status"],
            "checked_at": time.time() if now is None else now,
            "provider_updated": event.get("updated"),
            "account": account,
            "calendar_id": calendar_id,
        }
        path = wc.home() / "auto_calendar_provider_checks.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(checks, indent=2))
        tmp.chmod(0o600)
        os.replace(tmp, path)


def _display(row):
    event = row.get("event") or {}
    from proactive.sources import calendar_source

    check = _provider_checks().get(str(row.get("google_id") or ""), {})
    if check and (
        check.get("account") != row.get("account", calendar_source.calendar_account())
        or check.get("calendar_id")
        != row.get("calendar_id", calendar_source.calendar_id())
    ):
        check = {}
    result = {
        "event": event,
        "id": str(row.get("id") or row.get("msg_id") or ""),
        "title": event.get("title") or row.get("title") or "WhatsApp calendar change",
        "date": event.get("date", ""),
        "start": event.get("start", ""),
        "location": event.get("location", ""),
        "source": row.get("source", "mail"),
        "sender": row.get("sender", ""),
        "status": row.get("status") or row.get("outcome") or "pending",
        "error": row.get("last_error", ""),
        "reason": (
            "A change or tentative plan needs your review."
            if row.get("outcome") == "review-required"
            else "Event extraction is unavailable; retrying automatically."
            if row.get("outcome") == "extract-retry"
            else ""
        ),
        "when": row.get("when", ""),
        "google_id": row.get("google_id", ""),
        "created": row.get("created", 0),
    }
    if check:
        checked = (
            dt.datetime.fromtimestamp(check["checked_at"])
            .astimezone()
            .strftime("%d %b %Y %H:%M %Z")
        )
        result.update(
            provider_status=check["status"],
            provider_checked_at=check["checked_at"],
            provider_updated=check.get("provider_updated"),
        )
        result["reason"] = (
            f"Current calendar status: {check['status']} (checked {checked}). The added receipt is historical."
        )
    return result


def status():
    import google_api
    import notify
    import whatsapp_calendar as wc
    from proactive.sources import calendar_source

    state = wc._load()
    rows = state.get("messages", {})
    pending = [_display(row) for row in ex.pending()]
    pending.extend(
        _display(row)
        for row in rows.values()
        if row.get("outcome") == "review-required"
    )
    retry = [
        _display(row) for row in rows.values() if row.get("outcome") == "extract-retry"
    ]
    with contextlib.suppress(OSError, ValueError):
        for path in ex.suggestions_dir().glob("*.json"):
            row = json.loads(path.read_text())
            if row.get("status") == "retry":
                retry.append(_display(row))
    retry.extend(
        _display(row) for row in rows.values() if row.get("outcome") == "extract-retry"
    )
    queued = []
    with contextlib.suppress(OSError, ValueError):
        queued = json.loads(notify.queue_path().read_text())
    count = (
        sum(
            1
            for item in queued
            if isinstance(item, dict)
            and (
                item.get("source") in ("calendar", "whatsapp")
                or item.get("kind")
                in ("calendar", "calendar-added", "proactive-calendar")
            )
        )
        if isinstance(queued, list)
        else 0
    )
    configured = google_api.token_path(calendar_source.calendar_account()).exists()
    last_scan = state.get("last_scan")
    verified = {}
    with contextlib.suppress(OSError, ValueError):
        data = json.loads((wc.home() / "auto_calendar_connection.json").read_text())
        if isinstance(data, dict):
            verified = data
    calendar_verified = (
        bool(verified.get("connected"))
        and verified.get("account") == calendar_source.calendar_account()
        and verified.get("calendar_id") == calendar_source.calendar_id()
    )
    return {
        "ok": True,
        "enabled": ex.auto_enabled(),
        "timezone": google_api.local_timezone(),
        "whatsapp": {
            "enabled": ex.enabled("whatsapp"),
            "connected": bool(state.get("connected"))
            and bool(last_scan and time.time() - last_scan < 300),
            "last_scan": last_scan,
            "scanned_chats": state.get("scanned_chats", 0),
            "read_failures": state.get("read_failures", 0),
            "messages_read": state.get("messages_read", 0),
            "read_retries": len(state.get("read_jobs", {})),
            "detail": (
                f"Last scan: {state.get('scanned_chats', 0)} chats read, {state.get('read_failures', 0)} failed; {state.get('messages_read', 0)} messages retrieved. "
                if last_scan
                else "No completed scan yet. "
            )
            + f"{len(state.get('read_jobs', {}))} conversation reads waiting to retry. "
            + "Reads up to 20 unread chats every 90 seconds; opening chats marks them read. History retrieval is bounded.",
        },
        "calendar": {
            "configured": configured,
            "connected": calendar_verified,
            "verified_at": verified.get("verified_at"),
            "account": calendar_source.calendar_account(),
            "calendar_id": calendar_source.calendar_id(),
            "detail": "Google Calendar events connection verified."
            if calendar_verified
            else (
                "Credentials configured; connection needs verification."
                if configured
                else "Connect Google Calendar with write permission."
            ),
        },
        "presence": {
            "state": "input",
            "detail": "Reminders wait for sustained activity at your desk.",
        },
        "pending": pending,
        "retry": retry,
        "recent": [_display(row) for row in reversed(ex._ledger_load()[-50:])],
        "reminders": {
            "deferred": count,
            "detail": "Queued reminders survive restarts and announce after you return.",
        },
    }


def dismiss(body):
    import whatsapp_calendar as wc

    if (
        not isinstance(body, dict)
        or body.get("action") != "dismiss"
        or not isinstance(body.get("id"), str)
    ):
        return 400, {"ok": False, "error": "Choose one item to dismiss."}
    key = body["id"]
    with wc._lock():
        state = wc._load()
        row = state.get("messages", {}).get(key)
        if row and row.get("outcome") == "review-required":
            row["outcome"] = "dismissed"
            row.pop("body", None)
            wc._save(state)
            return 200, {"ok": True}
    record = ex.get(key)
    if record and record.get("status") == "pending" and ex.set_status(key, "dismissed"):
        return 200, {"ok": True}
    return 404, {
        "ok": False,
        "error": "This item is no longer pending. Refresh the calendar activity.",
    }
