"""Incremental, durable WhatsApp history scans for Always Aware.

All message payloads remain untrusted data. This module only persists reader
results; calendar and memory consumers work from the independent message queue.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import math
import time
import uuid
from typing import Any, Callable

from aware import state

BOOTSTRAP_SECONDS = 7 * 24 * 60 * 60
CHAT_LIMIT = 20


def _control_status(now: float) -> str:
    if not state.env_allows() or state.killed():
        return "off"
    current = state.load()
    if not current.get("enabled"):
        return "off"
    if state.paused(current, now):
        return "paused"
    return "on"


def _stable_jid(value: Any) -> str | None:
    from system.whatsapp import stable_chat_id

    return stable_chat_id(str(value or ""))


async def scan(
    store=None,
    now: float | None = None,
    *,
    enabled: Callable[[], bool] | None = None,
    chat_limit: int = CHAT_LIMIT,
) -> dict:
    """Read first 20 chats and atomically persist each outcome before moving on.

    ``now`` is the scan start time, so a partial initial read retains the same
    seven-day boundary on retry. Store errors and incomplete history remain
    explicit and retryable. Chat names are labels only, never identities.
    """
    from aware.message_store import Store

    now = float(time.time() if now is None else now)
    owns_store = store is None
    store = Store() if owns_store else store
    lock_path = state.aware_dir() / "whatsapp-scan.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock_path.open("a+") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {
                    "outcome": "running",
                    "retry": "attach",
                    "selected": 0,
                    "chats": [],
                }
            try:
                return await _scan_locked(
                    store, now, enabled=enabled, chat_limit=chat_limit
                )
            finally:
                with contextlib.suppress(OSError):
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        if owns_store:
            store.close()


async def _scan_locked(store, now: float, *, enabled, chat_limit: int) -> dict:
    from system import whatsapp

    check = enabled or (lambda: _control_status(time.time()) == "on")
    control_status = (
        (lambda: "on" if enabled() else "paused")
        if enabled
        else lambda: _control_status(time.time())
    )
    scan_id = uuid.uuid4().hex
    boundary = now - BOOTSTRAP_SECONDS
    if not check():
        return {
            "outcome": "skip",
            "reason": control_status(),
            "scan_id": scan_id,
            "started_at": now,
            "selected": 0,
            "chats": [],
        }
    try:
        selected = await whatsapp.list_chat_snapshot(
            min(CHAT_LIMIT, max(0, int(chat_limit)))
        )
        list_error = "" if selected else "WhatsApp chat list unavailable"
    except Exception as exc:
        selected = []
        list_error = f"chat list failed: {type(exc).__name__}: {str(exc)[:120]}"

    outcomes: list[dict] = []

    def receipt():
        slots = []
        for idx, _chat in enumerate(selected):
            done = outcomes[idx] if idx < len(outcomes) else None
            slots.append(
                {
                    "slot": idx + 1,
                    **(
                        done
                        or {
                            "status": "pending",
                            "retryable": True,
                            "messages": 0,
                            "error": "",
                        }
                    ),
                }
            )
        row = {
            "scan_id": scan_id,
            "started_at": now,
            "boundary": boundary,
            "selected": len(selected),
            "chats": slots,
        }
        state.write_private(
            state.aware_dir() / "whatsapp-scan.json",
            json.dumps(row, separators=(",", ":")),
        )

    receipt()
    for index, chat in enumerate(selected):
        outcomes.append(
            {
                "slot": index + 1,
                "status": "pending",
                "retryable": True,
                "messages": 0,
                "error": "",
                "marks_read": False,
            }
        )
        name = str(chat.get("name") or "")[:120]
        chat_id = _stable_jid(chat.get("chat_id"))
        if not chat_id:
            outcomes[index] = _outcome(
                index + 1, "unavailable", "stable WhatsApp chat ID unavailable"
            )
            receipt()
            continue
        if not check():
            control = control_status()
            outcomes[index] = _outcome(
                index + 1,
                "paused" if control == "paused" else "off",
                "Always Aware controls stopped the scan",
            )
            receipt()
            continue
        outcomes[index]["status"] = "running"
        receipt()
        try:
            checkpoint = store.chat(chat_id)
        except Exception as exc:
            outcomes[index] = _outcome(
                index + 1, "error", f"checkpoint read failed: {type(exc).__name__}"
            )
            receipt()
            continue
        checkpoint = checkpoint or {}
        try:
            chat_boundary = float(checkpoint.get("boundary") or boundary)
            if not math.isfinite(chat_boundary):
                raise ValueError("non-finite boundary")
        except (TypeError, ValueError, OverflowError):
            outcomes[index] = _outcome(
                index + 1, "error", "invalid saved history boundary"
            )
            receipt()
            continue
        page_inserted = 0

        def persist_page(
            page_messages,
            chat_id=chat_id,
            name=name,
            chat_boundary=chat_boundary,
            index=index,
        ):
            nonlocal page_inserted
            if not check():
                return False
            inserted_page = store.ingest(
                chat_id,
                name,
                page_messages,
                boundary=chat_boundary,
                complete=False,
                now=now,
                error="",
            )
            page_inserted += len(inserted_page)
            outcomes[index] = {
                "slot": index + 1,
                "status": "running",
                "retryable": True,
                "messages": page_inserted,
                "error": "",
                "marks_read": True,
            }
            receipt()
            return True

        try:
            read = await whatsapp.read_chat_history(
                name,
                chat_boundary,
                chat_id=chat_id,
                checkpoint=checkpoint,
                on_page=persist_page,
            )
        except BaseException as exc:
            # Preserve cancellation semantics for the coordinator/thread task;
            # ordinary reader failures become an explicit per-chat outcome.
            import asyncio

            if isinstance(exc, asyncio.CancelledError):
                raise
            outcomes[index] = _outcome(
                index + 1, "partial", f"history read failed: {type(exc).__name__}"
            )
            receipt()
            continue

        if not isinstance(read, dict):
            read = {
                "ok": False,
                "complete": False,
                "messages": [],
                "error": "invalid reader result",
            }
        result_jid = _stable_jid(read.get("chat_id"))
        if result_jid != chat_id:
            read = {
                **read,
                "ok": False,
                "complete": False,
                "messages": [],
                "error": "reader identity did not match selected chat",
            }
        messages = (
            read.get("messages") if isinstance(read.get("messages"), list) else []
        )
        controls_stopped = str(read.get("error") or "").startswith(
            "scan stopped by Always Aware controls"
        )
        if controls_stopped:
            # Earlier pages have already been durably staged by on_page; the
            # current page was intentionally not persisted after pause/off.
            messages = []
        valid_messages = []
        invalid = False
        for idx, message in enumerate(messages):
            try:
                if not isinstance(message, dict) or not str(message.get("id") or ""):
                    invalid = True
                    continue
                item = dict(message)
                item["timestamp"] = float(
                    item.get("timestamp", item.get("time", 0)) or 0
                )
                item["order"] = int(item.get("order", item.get("ordinal", idx)) or 0)
                if not math.isfinite(item["timestamp"]):
                    invalid = True
                    continue
                valid_messages.append(item)
            except (TypeError, ValueError, OverflowError):
                invalid = True
        complete = bool(read.get("ok") and read.get("complete") and not invalid)
        error = str(
            read.get("error") or ("message identity unavailable" if invalid else "")
        )[:240]
        try:
            inserted = store.ingest(
                chat_id,
                str(read.get("name") or name),
                valid_messages,
                boundary=chat_boundary,
                complete=complete,
                now=now,
                error=error,
            )
        except Exception as exc:
            outcomes[index] = _outcome(
                index + 1,
                "error",
                f"message persistence failed: {type(exc).__name__}",
                marks_read=bool(read.get("marks_read")),
            )
            receipt()
            continue
        outcome_status = "complete" if complete else "partial"
        outcomes[index] = {
            "slot": index + 1,
            "status": outcome_status,
            "retryable": not complete,
            "messages": page_inserted + len(inserted),
            "error": "" if complete else error or "history coverage incomplete",
            "marks_read": bool(read.get("marks_read")),
        }
        receipt()

    result = {
        "outcome": "ok"
        if outcomes and all(row["status"] == "complete" for row in outcomes)
        else "partial",
        "scan_id": scan_id,
        "started_at": now,
        "boundary": boundary,
        "selected": len(selected),
        "chats": outcomes,
    }
    if list_error:
        result["error"] = list_error
    return result


def _outcome(slot: int, status: str, error: str, *, marks_read=False) -> dict:
    return {
        "slot": slot,
        "status": status,
        "retryable": status not in ("complete", "off"),
        "messages": 0,
        "error": error,
        "marks_read": marks_read,
    }
