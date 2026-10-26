"""Durable explicit one-shot reminders and calls, hosted by the bridge timer.

Transport admission is claimed before dispatch. A crash without a receipt is
uncertain and never replayed automatically; invitation is not answered speech.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from goodnight_fence import admission

GRACE_S = 60
HORIZON_S = 366 * 86400


class ScheduleError(ValueError):
    """Safe speakable scheduling failure."""


def enabled():
    return os.environ.get("JARVIS_SCHEDULED_TASKS", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def timezone():
    try:
        return ZoneInfo(
            os.environ.get("JARVIS_TZ", "Asia/Singapore").strip() or "Asia/Singapore"
        )
    except ZoneInfoNotFoundError as exc:
        raise ScheduleError("The configured scheduling timezone is invalid.") from exc


def parse_when(when="", *, delay_minutes=0, now=None):
    now = time.time() if now is None else now
    if not math.isfinite(float(now)):
        raise ScheduleError("The scheduling clock is unavailable.")
    text = " ".join(str(when or "").strip().lower().split())
    if delay_minutes:
        if text:
            raise ScheduleError("Give either a time or a delay, not both.")
        due = now + float(delay_minutes) * 60
    elif match := re.fullmatch(
        r"in (\d+(?:\.\d+)?) (seconds?|minutes?|hours?|days?)", text
    ):
        multiplier = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
        due = now + float(match[1]) * multiplier[match[2].rstrip("s")]
    else:
        current = dt.datetime.fromtimestamp(now, timezone())
        explicit_day = text.startswith(("today ", "tomorrow "))
        tomorrow = text.startswith("tomorrow ")
        clock = re.sub(r"^(?:today |tomorrow )?(?:at )?", "", text).replace(" ", "")
        match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)?", clock)
        try:
            if match:
                hour, minute = int(match[1]), int(match[2] or 0)
                if match[3]:
                    if not 1 <= hour <= 12:
                        raise ValueError()
                    hour = hour % 12 + (12 if match[3] == "pm" else 0)
                elif match[2] is None:
                    raise ValueError()
                day = current.date() + dt.timedelta(days=int(tomorrow))
                value = dt.datetime.combine(day, dt.time(hour, minute), timezone())
                if value.timestamp() <= now and not explicit_day:
                    value += dt.timedelta(days=1)
                if value.utcoffset() != value.replace(
                    fold=1
                ).utcoffset() or value.astimezone(dt.timezone.utc).astimezone(
                    timezone()
                ).replace(tzinfo=None) != value.replace(tzinfo=None):
                    raise ValueError()
            else:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}.*", text):
                    raise ValueError()
                value = dt.datetime.fromisoformat(text.replace("z", "+00:00"))
                if value.tzinfo is None:
                    first = value.replace(tzinfo=timezone(), fold=0)
                    second = value.replace(tzinfo=timezone(), fold=1)
                    if (
                        first.utcoffset() != second.utcoffset()
                        or first.astimezone(dt.timezone.utc)
                        .astimezone(timezone())
                        .replace(tzinfo=None)
                        != value
                    ):
                        raise ValueError()
                    value = first
            due = value.timestamp()
        except (ValueError, OverflowError) as exc:
            raise ScheduleError(
                "Give a time such as 7pm, tomorrow at 09:00, an ISO date and time, or in 10 minutes."
            ) from exc
    if not math.isfinite(due) or not now < due <= now + HORIZON_S:
        raise ScheduleError("Choose a future time within the next year.")
    return due


def store_path():
    home = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(home) if home else Path.home() / ".jarvis") / "scheduled_tasks.sqlite"


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else store_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ScheduleError("The scheduling store is unavailable.")
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS scheduled_tasks (id TEXT PRIMARY KEY, request_key TEXT UNIQUE, kind TEXT, text TEXT, due REAL, timezone TEXT, created REAL, status TEXT, reason TEXT, claimed REAL)"
            )
        self.path.chmod(0o600)

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @admission
    def create(self, kind, text, due, *, now=None, request_id=None):
        now = time.time() if now is None else now
        if not enabled():
            raise ScheduleError(
                "Scheduled reminders and calls are disabled. Enable the scheduling worker first."
            )
        if kind not in {"reminder", "call"}:
            raise ScheduleError("Choose reminder or call.")
        text = " ".join(str(text or "").split())
        if not text or len(text) > 300:
            raise ScheduleError("Give a reminder or call message up to 300 characters.")
        if not math.isfinite(due) or not now < due <= now + HORIZON_S:
            raise ScheduleError("Choose a future time within the next year.")
        if kind == "call":
            from phone_calls import quiet

            if quiet(due):
                raise ScheduleError(
                    "Calls are unavailable during school quiet hours, Monday to Friday 08:00-15:30 Asia/Singapore. Choose another time."
                )
        # One identical effect per SDK turn; multiple distinct tasks remain valid.
        request_key = f"{request_id}|{kind}|{text}" if request_id else None
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if request_key:
                row = db.execute(
                    "SELECT * FROM scheduled_tasks WHERE request_key=?", (request_key,)
                ).fetchone()
                if row:
                    return dict(row)
            task_id = "sched-" + secrets.token_hex(8)
            db.execute(
                "INSERT INTO scheduled_tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    request_key,
                    kind,
                    text,
                    due,
                    str(timezone()),
                    now,
                    "pending",
                    "",
                    None,
                ),
            )
            return dict(
                db.execute(
                    "SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)
                ).fetchone()
            )

    def get(self, task_id):
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)
            ).fetchone()
            return dict(row) if row else None

    def list(self, limit=20):
        with self.db() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM scheduled_tasks ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END,due DESC LIMIT ?",
                    (max(1, min(100, limit)),),
                )
            ]

    def cancel(self, task_id):
        with self.db() as db:
            return bool(
                db.execute(
                    "UPDATE scheduled_tasks SET status='cancelled',reason='' WHERE id=? AND status IN ('pending','dispatching')",
                    (task_id,),
                ).rowcount
            )

    def tick(self, *, now=None, reminder=None, call=None, clock=None):
        if not enabled():
            return 0
        clock = clock or (time.time if now is None else lambda: now)
        now = clock() if now is None else now
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE scheduled_tasks SET status='uncertain',reason='Dispatch ended without a receipt; not replayed' WHERE status='dispatching' AND claimed<?",
                (now - GRACE_S,),
            )
            db.execute(
                "UPDATE scheduled_tasks SET status='missed',reason='Scheduled time passed while unavailable' WHERE status='pending' AND due<?",
                (now - GRACE_S,),
            )
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM scheduled_tasks WHERE status='pending' AND due<=? ORDER BY due LIMIT 20",
                    (now,),
                )
            ]
            for row in rows:
                db.execute(
                    "UPDATE scheduled_tasks SET status='dispatching',claimed=? WHERE id=?",
                    (now, row["id"]),
                )
        count = 0
        for row in rows:
            # Serialize cancellation against the short local transport handoff.
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                if (
                    db.execute(
                        "SELECT status FROM scheduled_tasks WHERE id=?", (row["id"],)
                    ).fetchone()[0]
                    != "dispatching"
                ):
                    continue
                dispatch_now = clock()
                if dispatch_now > row["due"] + GRACE_S:
                    db.execute(
                        "UPDATE scheduled_tasks SET status='missed',reason='Scheduled time passed before transport handoff' WHERE id=?",
                        (row["id"],),
                    )
                    continue
                status, reason = "failed", "Transport unavailable"
                try:
                    if row["kind"] == "call":
                        receipt = call(row) if call else _call(row, dispatch_now)
                    else:
                        receipt = (
                            reminder(row) if reminder else _reminder(row, dispatch_now)
                        )
                    if receipt is True:
                        receipt = {"status": "queued", "reason": ""}
                    if isinstance(receipt, dict) and receipt.get("status") in {
                        "queued",
                        "notified",
                        "invited",
                        "blocked",
                        "failed",
                    }:
                        status, reason = (
                            receipt["status"],
                            str(receipt.get("reason", ""))[:160],
                        )
                except Exception:
                    pass  # Never retain exception bodies or retry an uncertain effect.
                db.execute(
                    "UPDATE scheduled_tasks SET status=?,reason=? WHERE id=?",
                    (status, reason, row["id"]),
                )
                count += 1
        return count


def _reminder(row, now):
    import notify
    import phone_session

    if phone_session.guest_required():
        return {"status": "blocked", "reason": "guest_mode"}

    receipt = notify.send(
        row["text"],
        title="Jarvis reminder",
        kind="reminder",
        source="scheduled",
        urgency="urgent",
        fingerprint=row["id"],
        queue=True,
        now=now,
    )
    return {
        "status": "queued"
        if receipt.get("queued")
        else "notified"
        if receipt.get("ok")
        else "failed",
        "reason": ""
        if receipt.get("queued") or receipt.get("ok")
        else "Notification unavailable",
    }


def _call(row, now):
    import phone_calls

    return phone_calls.queue().offer_scheduled(
        row["id"], row["text"], now, row["due"] + GRACE_S
    )


def start_thread(interval=1.0):
    stop = threading.Event()

    def loop():
        store = None
        while not stop.is_set():
            with contextlib.suppress(Exception):
                if enabled():
                    store = store or Store()
                    store.tick()
            stop.wait(max(0.1, interval))

    thread = threading.Thread(target=loop, name="scheduled-tasks", daemon=True)
    thread.start()
    return thread, stop
