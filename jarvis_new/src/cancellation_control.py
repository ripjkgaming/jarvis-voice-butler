"""Pure cancellation policy. Speech interruption conveys no effect rollback.

Native task IDs remain native. Build-monitor registrations grant observation
only and must never be forwarded to an executor cancellation function.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ACTIVE_TASK_STATES = frozenset({"planning", "running", "waiting", "cancelling"})


@dataclass(frozen=True)
class Control:
    action: str
    target: str = ""


def classify_control(text: str) -> Control | None:
    """Classify exact owner commands; callers must establish trusted final input."""
    clean = " ".join(str(text or "").casefold().strip(" .!?\t\n").split())
    if clean in {"stop", "stop talking", "be quiet"}:
        return Control("speech")
    if clean in {"cancel this tool", "cancel that tool"}:
        return Control("tool")
    if clean in {"cancel this task", "cancel that task", "cancel task"}:
        return Control("task")
    match = re.fullmatch(r"cancel task (task-[a-z0-9_-]{1,70})", clean)
    return Control("task", match[1]) if match else None


def resolve_task(rows: list[dict], task_id: str) -> dict | None:
    """Only one unambiguous active task, from an already-authorized collection."""
    eligible = [row for row in rows if row.get("status") in ACTIVE_TASK_STATES]
    if task_id:
        return next((row for row in eligible if row.get("id") == task_id), None)
    return eligible[0] if len(eligible) == 1 else None


def task_receipt(task: dict) -> dict:
    """A task receipt is separate from verified-action postcondition evidence."""
    state = task.get("status", "unknown")
    settled = state in {"cancelled", "done", "failed"}
    title = str(task.get("title") or "task")[:80]
    say = (
        f"Stopped '{title}'. Earlier completed actions have not been undone."
        if state == "cancelled"
        else f"Cancellation requested for '{title}'; the current preparation may still finish."
    )
    if state in {"done", "failed"}:
        say = (
            f"'{title}' already ended. Earlier completed actions have not been undone."
        )
    elif state not in {"cancelled", "cancelling"}:
        say = f"'{title}' is {state}; no cancellation is confirmed."
    return {
        "say": say,
        "task_id": task.get("id"),
        "state": state,
        "accepted": state in {"cancelling", "cancelled"},
        "settled": settled,
        "effect_state": "unknown",  # legacy task ledger is not an effect ledger
    }
