"""Deadline signals: todos due today or tomorrow.

A todo has a due date when it carries a "due" field (YYYY-MM-DD) or its
text says "due <when>" / "by <when>" ("essay due friday", "form by 14
Oct"). Due tomorrow -> normal, due today -> high, overdue -> HUD only.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time

from proactive.signals import Signal

_DUE = re.compile(r"\b(?:due|by)\s+(.+?)\s*$", re.I)


def due_date(item: dict, today: dt.date) -> dt.date | None:
    """A todo's due date, from its 'due' field or its text. Pure-ish."""
    import exams

    raw = str(item.get("due") or "")
    if not raw:
        m = _DUE.search(str(item.get("text") or ""))
        raw = m.group(1) if m else ""
    iso = exams.parse_date(raw, today) if raw else ""
    return dt.date.fromisoformat(iso) if iso else None


def load_todos() -> list[dict]:
    from system.core import TODOS_PATH

    try:
        items = json.loads(TODOS_PATH.read_text())
    except (OSError, ValueError):
        return []
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def poll(now: float | None = None, todos=None) -> list[Signal]:
    now = time.time() if now is None else now
    today = dt.date.fromtimestamp(now)
    out = []
    for item in todos if todos is not None else load_todos():
        if item.get("done"):
            continue
        due = due_date(item, today)
        if due is None:
            continue
        full = " ".join(str(item.get("text") or "").split())
        text = (_DUE.sub("", full).strip() or full)[:80]
        days = (due - today).days
        if days == 1:
            say, urgency = f"Reminder, Sir: {text} is due tomorrow.", "normal"
        elif days == 0:
            say, urgency = f"Sir, {text} is due today.", "high"
        elif days < 0:
            say, urgency = f"Overdue: {text}", "low"
        else:
            continue
        out.append(
            Signal(
                kind="deadline",
                key=f"due:{text.lower()}:{due.isoformat()}:{days}",
                title=say,
                detail=f"{text} (due {due.isoformat()})",
                urgency=urgency,
                ts=now,
            )
        )
    return out
