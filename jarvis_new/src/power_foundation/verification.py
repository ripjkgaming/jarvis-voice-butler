"""Result receipts based on observations."""

from __future__ import annotations

OBSERVATIONS = frozenset({"observed", "attempted", "failed", "not_checked"})


def result_receipt(action_id: str, observation: str, evidence: str | None):
    if not action_id or observation not in OBSERVATIONS:
        raise ValueError("Invalid result observation")
    verified = observation == "observed" and bool(evidence and evidence.strip())
    return {
        "action_id": action_id,
        "status": "verified" if verified else "unverified",
        "verified": verified,
        "evidence": evidence if verified else None,
        "observation": observation,
    }
