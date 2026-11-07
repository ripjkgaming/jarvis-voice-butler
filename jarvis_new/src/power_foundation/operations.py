"""Durable one-shot outbound ledger; claiming is never proof of delivery.

No action executor lives here. A consumer MUST keep its existing confirmation
and authorization guard. An unknown post-dispatch result is never replay-safe.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

STATES = frozenset(
    {"attempted", "completed_unverified", "verified", "failed_unverified"}
)


def fingerprint(arguments: dict) -> str:
    """Hash canonical action arguments; never store message bodies or addresses."""
    return hashlib.sha256(
        json.dumps(
            arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


class OutboundLedger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS outbound_attempts (
                request_id TEXT PRIMARY KEY, action TEXT NOT NULL,
                argument_sha256 TEXT NOT NULL, state TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL,
                evidence_ref TEXT)""")

    def _db(self):
        return sqlite3.connect(self.path, timeout=3)

    def claim(self, request_id: str, action: str, argument_digest: str) -> bool:
        """Atomic durable claim BEFORE any external dispatch; duplicate is refused."""
        if (
            not isinstance(request_id, str)
            or not 8 <= len(request_id) <= 128
            or not isinstance(action, str)
            or not 1 <= len(action) <= 64
            or len(argument_digest) != 64
            or any(c not in "0123456789abcdef" for c in argument_digest)
        ):
            raise ValueError("Invalid outbound claim")
        stamp = time.time()
        with self._db() as db:
            row = db.execute(
                "INSERT OR IGNORE INTO outbound_attempts VALUES(?,?,?,?,?,?,?)",
                (request_id, action, argument_digest, "attempted", stamp, stamp, None),
            )
            return row.rowcount == 1

    def settle(
        self, request_id: str, state: str, evidence_ref: str | None = None
    ) -> bool:
        """No externally verified state unless a trusted observer supplies proof.

        This method is accounting only; callers must ensure evidence is genuine.
        A dispatcher's API 200/id is *completed_unverified*, not verified.
        """
        if state not in STATES:
            raise ValueError("Invalid outcome state")
        if evidence_ref is not None and (
            len(evidence_ref) > 128 or not evidence_ref.isascii()
        ):
            raise ValueError("Invalid redacted evidence reference")
        with self._db() as db:
            result = db.execute(
                "UPDATE outbound_attempts SET state=?,updated=?,evidence_ref=? "
                "WHERE request_id=? AND state='attempted'",
                (state, time.time(), evidence_ref, request_id),
            )
            return result.rowcount == 1

    def summary(self):
        with self._db() as db:
            rows = db.execute(
                "SELECT state,COUNT(*) FROM outbound_attempts GROUP BY state"
            )
            counts = dict(rows)
        return {
            "total": sum(counts.values()),
            "states": counts,
            "uncertain": counts.get("attempted", 0)
            + counts.get("failed_unverified", 0),
        }
