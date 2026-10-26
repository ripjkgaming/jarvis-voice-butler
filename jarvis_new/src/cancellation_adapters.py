"""Explicit agent-tasks adapter for the shared jobs.Registry v1 contract.

This module never stops a PID, observes a process, or revokes a phone ticket.
Canonical monitor IDs cannot grant executor authority.
"""

from __future__ import annotations

import re

import agent_tasks
from cancellation_control import task_receipt

OWNER = "agent-tasks"
SCOPE = "adapter_work"
TASK_ID = re.compile(r"task-[A-Za-z0-9_-]{1,70}\Z")
JOB_ID = re.compile(r"job-[0-9a-f]{32}\Z")


def available_adapter():
    """Legacy jobs.py remains supported; integration adds Registry explicitly."""
    import jobs

    registry = getattr(jobs, "Registry", None)
    if registry is None:
        return TaskCancellationAdapter()
    return TaskCancellationAdapter(
        registry(agent_tasks.tasks_dir().parent / "job_registry")
    )


def sync_registered_task(task: dict):
    """Settlement hook only for tasks already registered before dispatch."""
    adapter = available_adapter()
    if adapter.registry is not None and adapter.registry.by_native(OWNER, task["id"]):
        return adapter.sync(task)
    return None


class TaskCancellationAdapter:
    def __init__(self, registry=None):
        self.registry = registry

    def register(self, task: dict):
        if self.registry is None:
            return None
        existing = self.registry.by_native(OWNER, task["id"])
        return existing or self.registry.register(
            OWNER, task["id"], SCOPE, "Background task", {}, lifecycle="running"
        )

    def native_id(self, identifier: str) -> str | None:
        if not isinstance(identifier, str):
            return None
        if TASK_ID.fullmatch(identifier):
            return identifier
        if self.registry is None or not JOB_ID.fullmatch(identifier):
            return None
        # Scope/owner are read before request_cancel, which itself records no
        # authority. A monitor registration must never reach agent_tasks.cancel.
        record = self.registry.get(identifier)
        if not record or record["owner"] != OWNER or record["scope"] != SCOPE:
            return None
        native = record["native_id"]
        return native if TASK_ID.fullmatch(native) else None

    def sync(self, task: dict):
        """Project native preparation settlement; this verifies no external act."""
        row = self.register(task)
        if row is None or row["lifecycle"] == "settled":
            return row
        if task["status"] == "cancelling":
            self.registry.request_cancel(row["id"])
            return self.registry.get(row["id"], OWNER)
        outcome = {
            "done": "succeeded",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(task["status"])
        if outcome is None:
            return row
        return self.registry.settle(row["id"], OWNER, outcome, {}, row["revision"])

    def cancel(self, identifier: str):
        native = self.native_id(identifier)
        task = agent_tasks.get(native) if native else None
        if task is None:
            return None
        row = self.register(task)
        if task["status"] in agent_tasks.TERMINAL:
            return self.receipt(task)
        if row is not None:
            self.registry.request_cancel(row["id"])
        task = agent_tasks.cancel(native)
        task = task or agent_tasks.get(native)  # completion may have won the race
        return self.receipt(task) if task else None

    def cancel_tool(self, identifier: str, service):
        """Explicit owned computer-use service; never a registry/PID fallback."""
        current = service.status()
        if not identifier or current.get("id") != identifier:
            return None
        if current.get("status") not in {"running", "cancelling"}:
            settled_check = getattr(service, "cancellation_settled", None)
            settled = bool(settled_check()) if callable(settled_check) else False
            return {
                "accepted": False,
                "settled": settled,
                "state": current.get("status") if settled else "cancelling",
                "say": "That computer operation already ended; earlier inputs have not been undone."
                if settled
                else "The operation is ending; input cleanup has not yet settled.",
            }
        result = service.cancel()
        return {
            "accepted": True,
            "settled": False,
            "state": "cancelling",
            "operation_id": identifier,
            "effect_state": "unknown",
            "say": result.get("say")
            or "Cancellation requested; the current input may still finish.",
        }

    def receipt(self, task: dict):
        result = task_receipt(task)
        row = self.sync(task)
        if row is not None:
            result.update(
                job_id=row["id"], lifecycle=row["lifecycle"], job_outcome=row["outcome"]
            )
        return result
