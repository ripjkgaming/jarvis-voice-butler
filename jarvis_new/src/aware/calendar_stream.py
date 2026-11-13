"""Adapt the durable Always Aware message queue to the existing calendar path.

The adapter stages source messages, then delegates extraction, retries, review
rules, and calendar writes to the existing WhatsApp calendar worker.
"""

from __future__ import annotations

import datetime as dt
import time

import whatsapp_calendar as wc


def _identity(message: dict) -> tuple[str, str]:
    """Return the durable per-chat source identity used by Store acknowledgments."""
    return str(message["chat_id"]), str(message["id"])


def _context_text(context: list[dict], message: dict, limit: int = 8) -> str:
    """Format a short timestamped transcript around one source message."""
    chat_id, message_id = _identity(message)
    unique = {
        (str(item.get("chat_id", chat_id)), str(item.get("id"))): item
        for item in [*context, message]
    }
    ordered = sorted(
        unique.values(),
        key=lambda item: (
            float(item.get("timestamp") or 0),
            int(item.get("order") or 0),
        ),
    )
    index = next(
        (
            i
            for i, item in enumerate(ordered)
            if str(item.get("id")) == message_id
            and str(item.get("chat_id", chat_id)) == chat_id
        ),
        len(ordered) - 1,
    )
    start = max(0, index - limit // 2)
    selected = ordered[start : start + limit]
    lines = []
    for item in selected:
        stamp = float(item.get("timestamp") or 0)
        local = dt.datetime.fromtimestamp(stamp, wc.ex.event_timezone()).isoformat(
            timespec="minutes"
        )
        sender = "Me" if item.get("me") else str(item.get("sender") or "Other")[:80]
        marker = " [current message]" if str(item.get("id")) == message_id else ""
        lines.append(f"{local} {sender}{marker}: {str(item.get('text') or '')[:250]}")
    return "\n".join(lines)[-3000:]


def _controls_status(now: float) -> str:
    """Return Always Aware's shared on/off/paused state, failing closed."""
    try:
        from aware import state

        return state.status_word(state.load(), now)
    except Exception:
        return "off"


def _controls_open() -> bool:
    """Read controls at the moment of use, including during slow extraction."""
    return _controls_status(time.time()) == "on"


def process(store=None, now=None, **policy_kwargs) -> dict:
    """Consume queued messages, stage durable receipts, and run existing policy.

    A chat with incomplete bootstrap coverage is held in the queue. Context is
    selected from its verified history window, but only a short local transcript
    is sent for extraction. An owned Store connection is closed before return.
    Test callers may inject the existing worker's runner/lister/inserter hooks.
    """
    now = time.time() if now is None else float(now)
    if _controls_status(now) != "on":
        return {
            "staged": 0,
            "duplicates": 0,
            "waiting_for_coverage": [],
            "errors": [],
            "calendar": [],
            "status": "off-or-paused",
        }
    owned_store = store is None
    if owned_store:
        from aware.message_store import Store

        store = Store()
    try:
        # A separate eligibility filter prevents partial-history chats from
        # starving fully covered chats. The broad read is only for wait status.
        all_pending = store.pending("calendar", limit=100000)
        pending = store.pending("calendar", limit=100, complete_only=True)
        waiting: set[str] = set()
        for message in all_pending:
            checkpoint = store.chat(str(message["chat_id"]))
            if not checkpoint or not checkpoint.get("complete"):
                waiting.add(str(message["chat_id"]))
        if not pending:
            calendar = (
                wc.process_pending(now=now, control=_controls_open, **policy_kwargs)
                if _controls_open()
                else []
            )
            return {
                "staged": 0,
                "duplicates": 0,
                "waiting_for_coverage": sorted(waiting),
                "errors": [],
                "calendar": calendar,
                "status": _controls_status(now),
            }
        eligible = pending

        staged = duplicates = 0
        acknowledgements: list[tuple[str, str]] = []
        errors = []
        timelines: dict[str, list[dict]] = {}
        for message in eligible:
            if not _controls_open():
                break
            chat_id, message_id = _identity(message)
            checkpoint = store.chat(chat_id) or {}
            boundary = float(checkpoint.get("boundary") or 0)
            timestamp = float(message.get("timestamp") or 0)
            if chat_id not in timelines:
                timelines[chat_id] = store.context(chat_id, limit=100000)
            timeline = timelines[chat_id]
            window = [
                item
                for item in timeline
                if boundary <= float(item.get("timestamp") or 0) <= now
            ]
            nearby = store.context(chat_id, limit=20, around_id=message_id)
            transcript = _context_text(nearby, message)
            # A later change anywhere in the complete seven-day read prevents
            # an older invite from being auto-created, even across long chats.
            future_changes = [
                item
                for item in window
                if float(item.get("timestamp") or 0) > timestamp
                and wc._CHANGE.search(str(item.get("text") or ""))
            ]
            if future_changes:
                lines = [
                    f"{float(item['timestamp'])}: {str(item.get('text') or '')[:180]}"
                    for item in future_changes[-8:]
                ]
                transcript = (
                    transcript[-1700:]
                    + "\nLater conversation changes (review before creating):\n"
                    + "\n".join(lines)
                    + "\nCurrent source message: "
                    + str(message.get("text") or "")[:500]
                )[-3500:]
            # The fixed boundary is captured as initial-scan-time minus seven
            # days, so it also reconstructs the initial scan's wall-clock time.
            historical = timestamp <= boundary + 7 * 86400
            try:
                outcome = wc.ingest_aware_message(
                    str(checkpoint.get("name") or message.get("name") or chat_id),
                    message,
                    transcript,
                    now=now,
                    historical=historical,
                )
            except Exception as exc:
                errors.append(
                    {
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "error": type(exc).__name__,
                    }
                )
                continue
            if outcome == "duplicate":
                duplicates += 1
            elif outcome == "staged":
                staged += 1
            elif outcome == "deferred":
                errors.append(
                    {
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "error": "calendar-work-deferred",
                    }
                )
                continue
            # A staged or intentionally skipped receipt is durable before ack.
            acknowledgements.append((chat_id, message_id))

        if acknowledgements:
            store.acknowledge("calendar", acknowledgements)
        calendar = []
        if _controls_open():
            calendar = wc.process_pending(
                now=now,
                control=_controls_open,
                **policy_kwargs,
            )
        return {
            "staged": staged,
            "duplicates": duplicates,
            "waiting_for_coverage": sorted(waiting),
            "errors": errors,
            "calendar": calendar,
            "status": _controls_status(now),
        }
    finally:
        if owned_store:
            store.close()
