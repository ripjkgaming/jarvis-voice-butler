"""Talk-time budget ledger: keep the month inside the free allowances.

Every voice session records its connected seconds into
~/.jarvis/minutes.json under the current month key. The entrypoint
refuses new sessions at the cap and warns at 70/90%.

The cap defaults to 800 session-minutes: inside the 1,000-minute
allowance with margin, and far above what the $2.50 inference credits
cover on the cheap pipeline (~800 min at ~$0.003/min).
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

LEDGER_PATH = Path.home() / ".jarvis" / "minutes.json"


def budget_limit_minutes() -> int:
    """Monthly cap in session-minutes (env JARVIS_MINUTE_BUDGET)."""
    try:
        return max(1, int(os.environ.get("JARVIS_MINUTE_BUDGET", "800")))
    except ValueError:
        return 800


def month_key(today: datetime.date | None = None) -> str:
    """Ledger bucket, e.g. '2026-09'. Pure."""
    day = today or datetime.date.today()
    return day.strftime("%Y-%m")


def load_ledger() -> dict:
    """Raw ledger mapping month -> used seconds. Never raises."""
    try:
        data = json.loads(LEDGER_PATH.read_text())
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def record_session(seconds: float) -> dict:
    """Add connected seconds to the current month. Returns the ledger."""
    ledger = load_ledger()
    key = month_key()
    try:
        prior = float(ledger.get(key, 0))
    except (TypeError, ValueError):
        prior = 0.0
    ledger[key] = round(prior + max(0.0, float(seconds)), 1)
    try:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        LEDGER_PATH.write_text(json.dumps(ledger))
    except Exception:
        pass
    return ledger


def status(ledger: dict | None = None) -> dict[str, float]:
    """Used/limit/pct for the current month. Pure."""
    ledger = ledger if ledger is not None else load_ledger()
    try:
        used_seconds = float(ledger.get(month_key(), 0))
    except (TypeError, ValueError):
        used_seconds = 0.0
    limit_minutes = budget_limit_minutes()
    used_minutes = used_seconds / 60.0
    pct = used_minutes / limit_minutes if limit_minutes else 1.0
    return {
        "used_minutes": round(used_minutes, 1),
        "limit_minutes": float(limit_minutes),
        "pct": round(pct, 3),
    }
