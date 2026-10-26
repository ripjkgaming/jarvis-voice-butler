"""Read-only exhaustive metadata inventories. No registry constructors/threads.

All missing stores, unsupported schema, overflow, bad rows, unknown settlement
and unreported external coverage hold. Never read log/transcript/source files.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import stat
from pathlib import Path

MAX_ROWS = 4096
MAX_BYTES = 32768


def _safe(path):
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
    ):
        raise ValueError("unsafe state file")
    return info


def _decode(raw):
    if len(raw.encode()) > MAX_BYTES:
        raise ValueError("oversized metadata")
    row = json.loads(raw)
    if not isinstance(row, dict):
        raise ValueError("invalid metadata")
    return row


def json_rows(root):
    if not root.is_dir() or root.is_symlink():
        raise ValueError("missing/unsafe inventory")
    paths = list(root.glob("*.json"))
    if len(paths) > MAX_ROWS:
        raise ValueError("inventory overflow")
    result = []
    for path in paths:
        if _safe(path).st_size > MAX_BYTES:
            raise ValueError("oversized metadata")
        result.append(_decode(path.read_text()))
    return result


def sql_rows(path, *, registry=False):
    _safe(path)
    # mode=ro (not immutable): respects current WAL and committed producer writes.
    with sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1
    ) as db:
        db.execute("PRAGMA query_only=ON")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
        expected = (
            {"id", "owner", "native_id", "payload"}
            if registry
            else {"request_id", "fingerprint", "payload", "created", "reported"}
        )
        if version != (1 if registry else 0) or columns != expected:
            raise ValueError("unsupported job schema")
        if registry:
            rows = db.execute(
                "SELECT payload FROM jobs LIMIT ?", (MAX_ROWS + 1,)
            ).fetchall()
        else:
            # Project metadata inside SQLite; never return Assigner snapshots,
            # goals, model replies, source files or other payload text to Python.
            invalid = db.execute(
                "SELECT 1 FROM jobs WHERE NOT json_valid(payload) LIMIT 1"
            ).fetchone()
            if invalid:
                raise ValueError("corrupt Assigner metadata")
            rows = db.execute(
                "SELECT json_object('status', json_extract(payload, '$.status'), "
                "'remote_uncertain', json_extract(payload, '$.remote_uncertain'), "
                "'active_turn', json_extract(payload, '$.active_turn')) FROM jobs LIMIT ?",
                (MAX_ROWS + 1,),
            ).fetchall()
        if len(rows) > MAX_ROWS:
            raise ValueError("inventory overflow")
        return [_decode(row[0]) for row in rows]


def _terminal(row, allowed, *, legacy=False):
    if row.get("status") not in allowed or row.get("remote_uncertain"):
        return False
    if row.get("active_turn") or row.get("executor_started") is True:
        return False
    return not (
        legacy
        and (
            type(row.get("finished")) not in {float, int}
            or not math.isfinite(row["finished"])
        )
    )


class TaskInventory:
    """Host callbacks must query every known owned executor and external registry.

    These callbacks return strictly True only for complete, fresh, authoritative
    coverage. None/False/exception holds. Generic process absence is insufficient.
    """

    def __init__(self, home, *, owned_clear, external_clear, build_exited):
        self.home = Path(home)
        self.owned_clear = owned_clear
        self.external_clear = external_clear
        self.build_exited = build_exited

    def clear(self):
        try:
            tasks = json_rows(self.home / "tasks")
            legacy = json_rows(self.home / "jobs")
            assigner = sql_rows(self.home / "assigner" / "jobs.sqlite3")
            canonical = sql_rows(
                self.home / "job_registry" / "jobs.sqlite3", registry=True
            )
            import jobs

            # Reuse canonical typed validation without initializing its store.
            canonical = [jobs._decode(json.dumps(row)) for row in canonical]
            if not all(_terminal(r, {"done", "failed", "cancelled"}) for r in tasks):
                return False
            if not all(_terminal(r, {"done", "failed"}, legacy=True) for r in legacy):
                return False
            if not all(
                _terminal(r, {"done", "failed", "blocked", "cancelled", "timed_out"})
                for r in assigner
            ):
                return False
            for row in canonical:
                if (
                    row.get("owner") == "build-monitor"
                    and row.get("scope") == "monitor_observation"
                ):
                    # Observation settlement/detachment isn't build settlement.
                    if self.build_exited(row) is not True:
                        return False
                elif (
                    row.get("owner") == "agent-tasks"
                    and row.get("scope") == "adapter_work"
                ):
                    if row.get("lifecycle") != "settled" or row.get("outcome") not in {
                        "succeeded",
                        "failed",
                        "cancelled",
                    }:
                        return False
                else:
                    return None
            if self.owned_clear() is not True or self.external_clear() is not True:
                return None
            return True
        except Exception:
            return None


def named_build_exited(row):
    """Only registered boot/UID/PID/start identity; no process-name heuristics.

    Missing/reused/zombie identity proves this registered process ended, but
    external_clear/owned_clear must still cover surviving child/remote work.
    """
    import build_monitor

    data = build_monitor.Monitor._data(row)
    identity = build_monitor.Identity(**data["identity"])
    observed = build_monitor.read_identity(identity.pid)
    return (
        observed is None
        or observed.key() != identity.key()
        or observed.state in {"Z", "X", "x"}
    )
