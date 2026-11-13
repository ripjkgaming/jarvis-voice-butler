"""Read WhatsApp events into the shared calendar ledger; never sends messages.

The bridge sidecar reads up to 20 unread chats per pass. Reading a chat marks
it read in WhatsApp. Fully read messages are durably staged before extraction.
Only incoming messages with a known timestamp in the last seven days qualify.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

import event_extractor as ex

_DATE_HINT = re.compile(
    r"\b(?:today|tomorrow|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday|appointment|meeting|exam|deadline|class|flight|reservation|confirmed|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b|\b\d{1,2}[:/]\d{2}|\b\d{1,2}\s*(?:am|pm)\b|\d{4}-\d{2}-\d{2}",
    re.I,
)

_CHANGE = re.compile(
    r"\b(cancel(?:led|ed|lation)?|reschedul\w*|postpon\w*|instead|tentative|maybe|might|perhaps|if we|if you|could we|shall we|are you free|are you fine|or anytime|or on|or monday|or tuesday|or wednesday|or thursday|or friday|or saturday|or sunday|would you|can we|ignore (?:all|previous)|system prompt)\b",
    re.I,
)
_CONTEXT_REPLY = re.compile(
    r"\b(?:yes|yep|yeah|sure|ok|okay|works|fine|confirmed|sounds good|"
    r"that works|count me in|i can make it|i'll be there|ill be there|"
    r"not free|can't make it|cannot make it|no thanks)\b",
    re.I,
)


def home() -> Path:
    return Path(os.environ.get("JARVIS_HOME") or Path.home() / ".jarvis")


@contextlib.contextmanager
def _lock(name="whatsapp_calendar"):
    home().mkdir(parents=True, exist_ok=True)
    with (home() / f"{name}.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _load():
    try:
        data = json.loads((home() / "whatsapp_calendar.json").read_text())
        return data if isinstance(data, dict) else {"messages": {}}
    except (OSError, ValueError):
        return {"messages": {}}


def _save(data):
    # Source receipts are permanent: deleting old rows could let a WhatsApp
    # replay trigger a second extraction or calendar action.
    data.setdefault("messages", {})
    data.setdefault("aware_receipts", {})
    target = home() / "whatsapp_calendar.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.chmod(0o600)
    os.replace(tmp, target)


def message_date(message: dict, now: float):
    raw = str(message.get("timestamp") or message.get("when") or "").strip()
    if not raw:
        return None
    try:
        if isinstance(message.get("timestamp"), (int, float)):
            return dt.datetime.fromtimestamp(message["timestamp"], ex.event_timezone())
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return (
            parsed.replace(tzinfo=ex.event_timezone())
            if parsed.tzinfo is None
            else parsed
        )
    except ValueError:
        pass
    # WhatsApp data-pre-plain-text dates follow the user's configured date order.
    day_first = os.environ.get("JARVIS_WA_DATE_ORDER", "DMY").upper() != "MDY"
    date_fmt = "%d/%m/%Y" if day_first else "%m/%d/%Y"
    for clock in ("%H:%M", "%I:%M %p", "%I:%M%p"):
        for year_fmt in (date_fmt, date_fmt.replace("%Y", "%y")):
            for fmt in (f"{clock}, {year_fmt}", f"{clock} {year_fmt}"):
                with contextlib.suppress(ValueError):
                    return dt.datetime.strptime(raw, fmt).replace(
                        tzinfo=ex.event_timezone()
                    )
    return None


def ingest_messages(
    chat: str, messages: list[dict], *, now=None, is_group=False
) -> int:
    """Persist sanitized incoming message candidates, without models/network."""
    import phone_session
    from wa_autoreply import blocklist, group_mentions_owner

    if phone_session.private():
        return 0

    if not ex.enabled("whatsapp") or chat.strip().lower() in blocklist():
        return 0
    now = time.time() if now is None else now
    added = 0
    with _lock():
        state = _load()
        rows = state.setdefault("messages", {})
        for message in messages[-30:]:
            if message.get("me") or any(
                message.get(k) for k in ("blocked", "guest", "private")
            ):
                continue
            text = str(message.get("text") or "").strip()[:3500]
            stamp = message_date(message, now)
            if (
                not text
                or stamp is None
                or not -300
                <= now - stamp.timestamp()
                <= max(
                    1,
                    min(30, int(os.environ.get("JARVIS_WHATSAPP_LOOKBACK_DAYS", "7"))),
                )
                * 86400
            ):
                continue
            if not _DATE_HINT.search(text) and not _CHANGE.search(text):
                continue
            group_invitation = bool(
                re.search(
                    r"\b(?:all (?:members|participants|players|saturday shuttlers)|everyone|@all|@everyone)\b",
                    text,
                    re.I,
                )
                and re.search(
                    r"\b(?:training|meeting|session|practice|class|event)\b", text, re.I
                )
                and _DATE_HINT.search(text)
            )
            if is_group and not (group_mentions_owner([message]) or group_invitation):
                continue
            identity = [
                chat.casefold(),
                str(message.get("sender") or ""),
                stamp.isoformat(),
                text,
            ]
            if message.get("id"):
                identity = ["whatsapp-message-id", str(message["id"])[:240]]
            key = "wa-" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()
            if key in rows:
                continue
            rows[key] = {
                "id": key,
                "source": "whatsapp",
                "sender": chat[:120],
                "subject": "WhatsApp appointment",
                "date": stamp.isoformat(),
                "body": text,
                "created": now,
            }
            added += 1
        _save(state)
    return added


def ingest_aware_message(
    chat: str,
    message: dict,
    context: str,
    *,
    now=None,
    historical=False,
) -> str:
    """Durably stage one Store message and retain its source receipt forever.

    The default ``ingest_messages`` remains unchanged for existing callers.
    Always Aware passes both directions and a bounded transcript.  Its
    historical candidates are still subject to the existing extractor's
    future-event validation, cancellation quarantine, and review policy.
    Returns ``staged``, ``skipped``, or ``duplicate`` for its permanent receipt.
    """
    import phone_session
    from wa_autoreply import blocklist

    chat_id = str(message.get("chat_id") or "")
    source_id = str(message.get("id") or "")
    # Durable Store identities are mandatory; a display name is not stable.
    if not chat_id or not source_id:
        raise ValueError(
            "Always Aware calendar messages require stable chat and message IDs"
        )
    identity = json.dumps([chat_id, source_id], separators=(",", ":"))
    receipt = "aware-" + hashlib.sha256(identity.encode()).hexdigest()
    now = time.time() if now is None else float(now)
    text = str(message.get("text") or "").strip()[:3500]
    stamp = message_date({"timestamp": message.get("timestamp")}, now)
    with _lock():
        state = _load()
        receipts = state.setdefault("aware_receipts", {})
        if receipt in receipts:
            return "duplicate"
        # Persist a terminal receipt for intentionally excluded messages too:
        # an unchanged message is never repeatedly reconsidered downstream.
        reason = ""
        if phone_session.private():
            reason = "private-session"
        elif not ex.enabled("whatsapp"):
            reason = "disabled"
        elif chat.strip().casefold() in blocklist():
            reason = "blocked-chat"
        elif not text or stamp is None:
            reason = "missing-text-or-timestamp"
        elif not (
            _DATE_HINT.search(text)
            or _CHANGE.search(text)
            or (
                _CONTEXT_REPLY.search(text)
                and (_DATE_HINT.search(context) or _CHANGE.search(context))
            )
        ):
            reason = "no-calendar-signal"
        if reason:
            if reason in ("private-session", "disabled"):
                # These controls can change without new WhatsApp input. Keep
                # the durable Store message queued for the next active pass.
                return "deferred"
            receipts[receipt] = {
                "chat_id": chat_id,
                "message_id": source_id,
                "status": reason,
                "at": now,
            }
            _save(state)
            return "skipped"

        # Preserve events from either participant.  A short source-centered
        # transcript gives extraction the context needed to resolve replies.
        body = str(context or text).strip()[:3500]
        key = "wa-" + receipt.removeprefix("aware-")
        rows = state.setdefault("messages", {})
        if key not in rows:
            rows[key] = {
                "id": key,
                "source": "whatsapp",
                "sender": chat[:120],
                "subject": "WhatsApp appointment",
                "date": stamp.isoformat(),
                "body": body,
                "created": now,
                "source_chat_id": chat_id,
                "source_message_id": source_id,
                "historical": bool(historical),
                "outgoing": bool(message.get("me")),
            }
        receipts[receipt] = {
            "chat_id": chat_id,
            "message_id": source_id,
            "calendar_id": key,
            "status": "staged",
            "at": now,
        }
        _save(state)
    return "staged"


def _aware_owns_ingestion() -> bool:
    """True when the Always Aware coordinator has claimed WhatsApp ingestion."""
    legacy = os.environ.get("JARVIS_AWARE_CALENDAR_LEGACY", "").strip().lower()
    if legacy in ("1", "true", "yes", "on"):
        return False
    explicit = os.environ.get("JARVIS_AWARE_CALENDAR_OWNER", "").strip().lower()
    return explicit not in ("0", "false", "no", "off")


def _aware_controls_allow(now=None) -> bool:
    """Honor Always Aware's global off, pause, and kill controls."""
    try:
        from aware import state as aware_state

        current = time.time() if now is None else now
        state = aware_state.load()
        return (
            aware_state.env_allows()
            and not aware_state.killed()
            and bool(state.get("enabled"))
            and not aware_state.paused(state, current)
        )
    except Exception:
        # If Aware control state cannot be read, fail closed for its owned run.
        return False


def process_pending(
    *, now=None, runner=None, lister=None, inserter=None, notify_fn=None, control=None
):
    """Serialize extraction and calendar writes across watcher/manual invocations."""
    if not ex.enabled("whatsapp"):
        return []
    now = time.time() if now is None else now
    out = []
    with _lock("whatsapp_calendar_worker"):
        with _lock():
            snapshot = _load()
        rows = snapshot.get("messages", {})
        changes = {}
        for candidate in rows.values():
            if control is not None and not control():
                break
            if _CHANGE.search(candidate.get("body", "")):
                sender = candidate.get("sender", "")
                changes[sender] = max(
                    changes.get(sender, ""), candidate.get("date", "")
                )
        for row in rows.values():
            if control is not None and not control():
                break
            if row.get("outcome") not in (None, "retry-queued", "extract-retry"):
                continue
            if float(row.get("retry_at") or 0) > now:
                continue
            if _CHANGE.search(row.get("body", "")) or row.get(
                "date", ""
            ) <= changes.get(row.get("sender", ""), ""):
                outcome = "review-required"
            else:
                outcome = ex.suggest(
                    row,
                    now=now,
                    runner=runner,
                    lister=lister,
                    inserter=inserter,
                    notify_fn=notify_fn,
                    control=control,
                )
            if outcome == "paused":
                break
            # Merge into the latest state: ingestion can proceed while the model
            # runs, and a slow extraction never blocks a voice read's staging.
            with _lock():
                state = _load()
                current = state.get("messages", {}).get(row["id"])
                if current is None:
                    continue
                if outcome.startswith("no-event:"):
                    current["last_error"] = outcome.split(":", 1)[1][:120]
                    current["attempts"] = int(current.get("attempts") or 0) + 1
                    current["retry_at"] = now + min(
                        3600, 60 * 2 ** min(current["attempts"] - 1, 6)
                    )
                    outcome = "extract-retry"
                else:
                    current.pop("last_error", None)
                    current.pop("retry_at", None)
                current["outcome"] = outcome
                current["processed"] = now
                if outcome not in ("retry-queued", "review-required", "extract-retry"):
                    current.pop("body", None)
                state["last_processed"] = now
                _save(state)
            out.append({"id": row["id"], "chat": row["sender"], "outcome": outcome})
    return out


def request_reconciliation(*, now=None):
    """Ask the running watcher to discover and durably queue up to 30 chats.

    No browser or calendar action occurs in this caller. The single normal
    watcher consumes this request, avoiding competing CDP consumers.
    """
    with _lock():
        state = _load()
        state["reconciliation_requested_at"] = time.time() if now is None else now
        _save(state)


async def run(wa=None, *, now=None, scan_all=False, automatic=False, **kwargs):
    """Read actual full chats. scan_all is an explicit bounded initial import."""
    if automatic:
        # The Always Aware coordinator is the single automatic reader and
        # calendar queue consumer by default. Legacy auto scanning is an
        # explicit fallback and still obeys Always Aware's off/pause/kill.
        if not _aware_controls_allow(now):
            return []
        if _aware_owns_ingestion():
            return []
    if not ex.enabled("whatsapp"):
        return [{"outcome": "disabled"}]
    if wa is None:
        from system import whatsapp as wa
    now = time.time() if now is None else now
    with _lock():
        scan_all = bool(scan_all or _load().get("reconciliation_requested_at"))
    chats = await wa.list_chats(30)
    scanned = 0
    read_failures = 0
    messages_read = 0
    with _lock():
        initial = _load()
        jobs = initial.setdefault("read_jobs", {})
        retry_names = {
            name for name, job in jobs.items() if float(job.get("retry_at") or 0) <= now
        }
    listed = {str(chat.get("name") or ""): chat for chat in chats}
    for name in retry_names:
        listed.setdefault(name, {"name": name, "unread": 0})
    candidates = [
        chat
        for name, chat in listed.items()
        if scan_all or int(chat.get("unread") or 0) or name in retry_names
    ]
    # Due durable jobs take priority over a busy inbox of newly unread chats.
    candidates.sort(key=lambda chat: str(chat.get("name") or "") not in retry_names)
    # Persist the entire bounded discovery set before any read can clear unread
    # markers. Remaining candidates survive process exit and the next pass.
    from wa_autoreply import blocklist

    blocked = blocklist()
    candidates = [
        chat
        for chat in candidates
        if str(chat.get("name") or "").strip()
        and str(chat.get("name") or "").casefold() not in blocked
    ][:30]
    with _lock():
        state = _load()
        jobs = state.setdefault("read_jobs", {})
        for chat in candidates:
            jobs.setdefault(
                str(chat["name"]),
                {"status": "queued", "attempts": 0, "retry_at": now, "created": now},
            )
        if scan_all and candidates:
            state.pop("reconciliation_requested_at", None)
            state["last_reconciliation"] = {
                "started": now,
                "names": [str(chat["name"]) for chat in candidates],
            }
        _save(state)
    for chat in candidates[:20]:
        name = str(chat.get("name") or "")
        if not name or name.casefold() in blocklist():
            continue
        with _lock():
            state = _load()
            jobs = state.setdefault("read_jobs", {})
            job = jobs.setdefault(name, {})
            if float(job.get("retry_at") or 0) > now:
                continue
            attempts = int(job.get("attempts") or 0) + 1
            # Persist before opening: WhatsApp may mark read even if a later
            # DOM read/storage operation crashes. Unread state is not a receipt.
            job.update(
                status="reading",
                attempts=attempts,
                retry_at=now + min(3600, 60 * 2 ** min(attempts - 1, 6)),
            )
            _save(state)
        try:
            read = await wa.read_chat(name, 30)
            if not read.get("ok"):
                raise RuntimeError(str(read.get("err") or "WhatsApp read failed")[:120])
            if str(read.get("name") or "").casefold() != name.casefold():
                raise RuntimeError(
                    "WhatsApp returned a different conversation identity"
                )
            messages_read += len(read.get("messages") or [])
            ingest_messages(
                name,
                read.get("messages") or [],
                now=now,
                is_group=read.get("group_state") != "contact",
            )
            with _lock():
                state = _load()
                state.setdefault("read_jobs", {}).pop(name, None)
                state["read_history"] = [
                    *state.get("read_history", []),
                    {
                        "name": name,
                        "status": "read",
                        "at": now,
                        "messages": len(read.get("messages") or []),
                    },
                ][-100:]
                _save(state)
            scanned += 1
        except Exception as exc:
            read_failures += 1
            with _lock():
                state = _load()
                job = state.setdefault("read_jobs", {}).setdefault(name, {})
                error = f"{type(exc).__name__}: {exc}"[:160]
                job.update(status="retry", last_error=error)
                state["read_history"] = [
                    *state.get("read_history", []),
                    {"name": name, "status": "retry", "at": now, "error": error},
                ][-100:]
                _save(state)
    with _lock():
        state = _load()
        state.update(
            last_scan=now,
            scanned_chats=scanned,
            read_failures=read_failures,
            messages_read=messages_read,
            connected=bool(chats),
        )
        _save(state)
    return await asyncio.to_thread(process_pending, now=now, **kwargs)


def start_thread(interval=90.0, first_delay=20.0):
    stop = threading.Event()

    def loop():
        if stop.wait(first_delay):
            return
        while not stop.is_set():
            try:
                asyncio.run(run(automatic=True))
            except Exception as exc:
                from system import log_action

                log_action("calendar", f"whatsapp scan failed: {type(exc).__name__}")
            if stop.wait(interval):
                return

    thread = threading.Thread(target=loop, name="whatsapp-calendar", daemon=True)
    thread.start()
    return thread, stop
