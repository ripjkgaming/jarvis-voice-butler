"""Optional read-only native Jarvis projection; never starts or resumes a task."""

from __future__ import annotations

import os
from pathlib import Path


def enabled():
    return os.environ.get("JARVIS_POWER_FOUNDATION") == "1"


def home():
    return Path(os.environ.get("JARVIS_HOME") or Path.home() / ".jarvis")


def checkpoint(task):
    """Best-effort sanitized audit; native task persistence remains authoritative."""
    if not (enabled() and os.environ.get("JARVIS_POWER_TASK_AUDIT") == "1"):
        return
    from .engine import FoundationStore

    store = FoundationStore(home() / "foundation" / "state.sqlite3")
    task_id = str(task.get("id") or "")
    state = str(task.get("status") or "")
    if task_id.startswith("task-") and state in {
        "planning",
        "running",
        "waiting",
        "done",
        "failed",
        "cancelled",
        "cancelling",
    }:
        store.event("native_task_checkpoint", task_id[:70] + ":" + state)
    # No untrusted task output or claimed success enters audit evidence.


def recovery_event(task_id, reason):
    """Sanitized recovery diagnostics, never the task payload."""
    if not (enabled() and os.environ.get("JARVIS_POWER_TASK_AUDIT") == "1"):
        return
    from .engine import FoundationStore

    if task_id.startswith("task-") and reason in {
        "executor_interrupted",
        "step_interrupted",
        "pending_approval_interrupted",
        "orphaned_cancellation_settled",
    }:
        FoundationStore(home() / "foundation" / "state.sqlite3").event(
            "native_task_recovery", task_id[:70] + ":" + reason
        )


def native_projection(rows):
    items = []
    for task in rows[-20:]:
        steps = task.get("steps") or []
        items.append(
            {
                "id": str(task.get("id", ""))[:80],
                "status": str(task.get("status", "unknown"))[:30],
                "revision": task.get("revision"),
                "step_count": len(steps),
                "completed_steps": sum(
                    s.get("status") == "done" for s in steps if isinstance(s, dict)
                ),
                "requires_approval": task.get("status") == "waiting",
                "recovery_required": task.get("status") == "recovery_required",
                "checkpoints": len(task.get("checkpoint_history") or []),
                "verification": "not_checked",
            }
        )
    return {"count": len(rows), "items": items}


def status():
    if not enabled():
        return {"ok": False, "enabled": False, "status": "disabled"}
    import agent_tasks

    from .connectors import basic_connectors
    from .engine import FoundationStore
    from .workspace import snapshot

    store = FoundationStore(home() / "foundation" / "state.sqlite3")
    state = snapshot(store, basic_connectors(store, live=True))
    # Foundation-only tasks are not the native task runner.
    state["tasks"] = [{"id": t["id"], "status": t["status"]} for t in state["tasks"]]
    state["events"] = [{"at": e["at"], "kind": e["kind"]} for e in state["events"]]
    state["native_tasks"] = native_projection(agent_tasks.all_tasks())
    items = state["native_tasks"]["items"]
    state["safety"] = {
        "recovery_required": sum(
            row["status"] == "recovery_required" for row in agent_tasks.all_tasks()
        ),
        "approvals_waiting": sum(
            row["status"] == "waiting" for row in agent_tasks.all_tasks()
        ),
        "task_checkpoints_enabled": os.environ.get("JARVIS_POWER_TASK_CHECKPOINTS")
        == "1",
        "approval_tickets_enabled": os.environ.get("JARVIS_POWER_APPROVAL_TICKETS")
        == "1",
        "memory_evidence_enabled": os.environ.get("JARVIS_POWER_MEMORY_EVIDENCE")
        == "1",
        "native_task_items_shown": len(items),
    }
    ledger_path = home() / "foundation" / "outbound.sqlite3"
    state["outbound"] = {"status": "not_initialized", "uncertain": 0, "total": 0}
    if ledger_path.is_file():
        try:
            from .operations import OutboundLedger

            state["outbound"] = {
                "status": "historical_ledger",
                **OutboundLedger(ledger_path).summary(),
            }
        except Exception:
            state["outbound"] = {
                "status": "unavailable",
                "uncertain": None,
                "total": None,
            }
    state["mcp"] = {"status": "not_inspected", "approved_servers": 0}
    allowlist_path = os.environ.get("JARVIS_POWER_MCP_ALLOWLIST")
    if allowlist_path:
        from .mcp_discovery import load_allowlist

        state["mcp"] = {
            "status": "allowlist_configured_not_connected",
            "approved_servers": len(load_allowlist(allowlist_path)),
        }
    state["observability"] = {
        "voice_latency": "existing_instrumentation_not_live_verified",
        "coding_workers": "existing_proposal_only_not_live_verified",
        "mobile": "not_live_verified",
    }
    state["enabled"] = True
    state["ok"] = True
    state["status"] = "read_only"
    return state


def claim_evidence(store, claim):
    """Attribution is NOT independent verification of factual truth."""
    if not isinstance(claim, str) or not claim.strip():
        return {"status": "unsupported"}
    evidence = store.search_evidence(claim.strip(), limit=1)
    if not evidence or evidence[0]["content"] != claim.strip():
        return {"status": "unsupported"}
    return {
        "status": "attributed_unverified",
        "source": evidence[0]["source"],
        "observed_at": evidence[0]["observed_at"],
        "evidence_id": evidence[0]["id"],
    }
