"""Opt-in native task crash reconciliation, without automatic action replay.

Native JSON snapshots remain authoritative. A free OS execution lease only
establishes that no executor currently owns the task, not that its last
external effect was rolled back or verified.
"""

from __future__ import annotations

import time
import uuid


def _interruption_reason(task: dict) -> str | None:
    if task.get("executor_started"):
        return "executor_interrupted"
    for step in task.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("status") == "running":
            return "step_interrupted"
        if step.get("approved") and step.get("status") != "done":
            return "pending_approval_interrupted"
    return None


def reconcile() -> list[dict]:
    """Inspect native tasks under existing locks; never invoke a task runner.

    May be called repeatedly or in different processes. A currently-held
    execution lease protects live work from being misclassified as crashed.
    """
    import agent_tasks as native

    changes = []
    for snapshot in native.all_tasks():
        task_id = snapshot.get("id")
        if not isinstance(task_id, str) or not task_id.startswith("task-"):
            continue
        if snapshot.get("status") not in {"planning", "running", "cancelling"}:
            continue
        with native._state_lock():
            current = native._load(task_id)
            if current is None or current.get("status") not in {
                "planning",
                "running",
                "cancelling",
            }:
                continue
            with native._execution_lock(task_id) as idle:
                if not idle:  # Executor may still be working in another process.
                    continue
                reason = _interruption_reason(current)
                if current["status"] == "cancelling":
                    current["status"] = "cancelled"
                    current["settled_at"] = time.time()
                    reason = "orphaned_cancellation_settled"
                elif reason is None:
                    continue  # A clean queued plan/step remains resumable.
                else:
                    current["status"] = "recovery_required"
                    current["recovery"] = {
                        "reason": reason,
                        "review_required": True,
                        "review_id": uuid.uuid4().hex,
                        "detected_at": time.time(),
                    }
                current["executor_started"] = False
                # An interrupted approval must never authorize later work.
                for step in current.get("steps") or []:
                    if isinstance(step, dict) and step.get("status") != "done":
                        step.pop("approved", None)
                native._write_locked(current)
                changes.append(
                    {"id": task_id, "status": current["status"], "reason": reason}
                )
        # Best-effort diagnostic: no raw goal, outputs or tool arguments.
        try:
            from .integration import recovery_event

            recovery_event(task_id, reason)
        except Exception:
            pass
    return changes


def cancel_after_review(task_id: str, revision: int, review_id: str) -> dict | None:
    """Human-selected cancellation; never retries or compensates an external act.

    The caller MUST authenticate the human and obtain a fresh explicit cancel
    decision. review_id only comes from a trusted local inspection; it must
    not be included in public status or model prompts. This function is not
    exposed to a remote agent tool.
    """
    import agent_tasks as native

    if not isinstance(task_id, str) or not task_id.startswith("task-"):
        return None
    with native._state_lock():
        current = native._load(task_id)
        if (
            current is None
            or current.get("status") != "recovery_required"
            or current.get("revision") != revision
            or current.get("recovery", {}).get("review_id") != review_id
            or not isinstance(review_id, str)
            or len(review_id) != 32
        ):
            return None
        with native._execution_lock(task_id) as idle:
            if not idle:
                return None
            current["status"] = "cancelled"
            current["settled_at"] = time.time()
            current["recovery"]["review_required"] = False
            current["recovery"]["resolution"] = "cancelled_after_review"
            for step in current.get("steps") or []:
                if isinstance(step, dict):
                    step.pop("approved", None)
                    step.pop("approval_request_id", None)
                    step.pop("approval_fingerprint", None)
                    step.pop("approval_expires_at", None)
            native._write_locked(current)
            return {
                "id": task_id,
                "status": "cancelled",
                "revision": current["revision"],
                "resolution": "cancelled_after_review",
            }
