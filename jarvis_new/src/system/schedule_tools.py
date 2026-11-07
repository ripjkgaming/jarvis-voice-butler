"""Authenticated-owner voice interface to durable reminders and call requests."""

from __future__ import annotations

import asyncio
import datetime as dt
import sqlite3

from jarvis_rt import RunContext, function_tool
from jarvis_rt import ToolError

import action_guard
import phone_session
import scheduled_tasks as scheduler
from system import LocalSystemError, require_local


def _guard():
    if phone_session.guest_required():
        raise ToolError("Scheduling is available only to the authenticated owner.")
    if phone_session.private():
        if not phone_session.identity():
            raise ToolError("The private owner session has ended.")
    else:
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc


def _describe(row):
    when = dt.datetime.fromtimestamp(row["due"], scheduler.ZoneInfo(row["timezone"]))
    return (
        f"{row['id']}: {row['kind']} {row['text']}, {when:%d %b %Y %H:%M:%S} {row['timezone']}, {row['status']}"
        + (f" ({row['reason']})" if row.get("reason") else "")
    )


def _read_tasks():
    _guard()
    path = scheduler.store_path()
    if not path.exists():
        return []
    # Listing must not create a DB or mutate state when the worker is disabled.
    if path.is_symlink():
        raise scheduler.ScheduleError("The scheduling store is unavailable.")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in db.execute(
                "SELECT * FROM scheduled_tasks ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END,due LIMIT 20"
            )
        ]


def _create(kind, text, due, request_id):
    _guard()
    return scheduler.Store().create(kind, text, due, request_id=request_id)


def _cancel(task_id):
    _guard()
    return scheduler.Store().cancel(task_id)


class ScheduleTools:
    @property
    def tools(self):
        return [self.schedule_task, self.scheduled_tasks]

    @function_tool()
    async def schedule_task(
        self,
        context: RunContext,
        kind: str = "reminder",
        when: str = "",
        delay_minutes: float = 0,
        text: str = "",
    ) -> dict[str, str]:
        """Schedule one explicit reminder or incoming phone call for the owner.

        Use for 'remind me at 7pm' or 'call me in ten minutes'. This stores one
        occurrence; it does not create a Calendar event. Never invent the time
        or reminder message. Read back the exact date, time and timezone.

        Args:
            kind: reminder or call.
            when: 7pm, tomorrow at 09:00, YYYY-MM-DD HH:MM, RFC3339, or in 10 minutes.
            delay_minutes: Relative minutes; use either this or when.
            text: What to remind the owner about, or the scheduled call's opener.
        """
        _guard()
        if not scheduler.enabled():
            raise ToolError(
                "Scheduled reminders and calls are disabled. Enable the scheduling worker first."
            )
        try:
            due = scheduler.parse_when(when, delay_minutes=delay_minutes)
            request_id = action_guard.request_id(context)
            if request_id:
                request_id = f"{request_id}|{when}|{delay_minutes}"
            row = await asyncio.to_thread(_create, kind, text, due, request_id)
        except (
            scheduler.ScheduleError,
            ValueError,
            TypeError,
            OSError,
            sqlite3.Error,
        ) as exc:
            if isinstance(exc, scheduler.ScheduleError):
                raise ToolError(str(exc)) from exc
            raise ToolError(
                "The schedule could not be saved. Check its time and try again."
            ) from exc
        _guard()
        message = "Scheduled " + _describe(row) + "."
        if kind == "reminder":
            message += " This is a desktop reminder: the computer and Jarvis bridge must be running. It is not a mobile push alarm."
        if kind == "call":
            message += " The phone must have incoming calls enabled and be online at that time; blocked or missed calls are not replayed."
        return {"say": message, "task_id": row["id"], "status": row["status"]}

    @function_tool()
    async def scheduled_tasks(
        self, context: RunContext, action: str = "list", task_id: str = ""
    ) -> dict[str, str]:
        """List saved reminders/calls and their actual status, or cancel by exact ID.

        Args:
            action: list or cancel.
            task_id: Exact sched- ID from list, required for cancel.
        """
        _guard()
        try:
            if action == "list":
                rows = await asyncio.to_thread(_read_tasks)
                _guard()
                return {
                    "say": "; ".join(_describe(row) for row in rows)[:3500]
                    if rows
                    else "No scheduled reminders or calls."
                }
            if action == "cancel":
                if not task_id or not scheduler.store_path().exists():
                    raise scheduler.ScheduleError(
                        "Give the exact scheduled task ID to cancel."
                    )
                cancelled = await asyncio.to_thread(_cancel, task_id)
                _guard()
                return {
                    "say": f"Cancelled {task_id}."
                    if cancelled
                    else "That schedule is already dispatched, finished, cancelled or unknown; no new cancellation was made."
                }
            raise scheduler.ScheduleError("Choose list or cancel.")
        except (scheduler.ScheduleError, OSError, sqlite3.Error) as exc:
            raise ToolError(
                str(exc)
                if isinstance(exc, scheduler.ScheduleError)
                else "Scheduled tasks are unavailable."
            ) from exc
