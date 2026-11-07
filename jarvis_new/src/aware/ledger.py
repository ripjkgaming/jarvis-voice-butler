"""Always Aware daily budget ledger (PLAN.md §1.4).

``~/.jarvis/aware/budget.json`` = ``{"YYYY-MM-DD": {"calls": n, "tokens": n}}``
with tokens from Gemini ``usageMetadata``. Over 70 % of either cap the tick
sends the active monitor only; at 100 % ticks stop for the day. Fail-soft.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time

from aware import state as st

CALLS_DEFAULT = 240
TOKENS_DEFAULT = 1_000_000
SIDE_DROP_FRAC = 0.70
KEEP_DAYS = 14
_LOCK = threading.Lock()


def _int_env(env: dict, name: str, default: int) -> int:
    try:
        value = int(str(env.get(name, "")).strip())
    except ValueError:
        return default
    return value if value > 0 else default


def caps(env: dict | None = None) -> tuple[int, int]:
    env = os.environ if env is None else env
    return (
        _int_env(env, "JARVIS_AWARE_DAILY_CALLS", CALLS_DEFAULT),
        _int_env(env, "JARVIS_AWARE_DAILY_TOKENS", TOKENS_DEFAULT),
    )


def day_key(now: float) -> str:
    return dt.date.fromtimestamp(now).isoformat()


def path():
    return st.aware_dir() / "budget.json"


def load() -> dict:
    try:
        data = json.loads(path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict) -> bool:
    keep = {k: data[k] for k in sorted(data)[-KEEP_DAYS:]}
    return st.write_private(path(), json.dumps(keep, sort_keys=True))


def _totals(row) -> dict:
    row = row if isinstance(row, dict) else {}
    out = {}
    for key in ("calls", "tokens"):
        try:
            out[key] = max(0, int(row.get(key) or 0))
        except (TypeError, ValueError):
            out[key] = 0
    return out


def today(now: float | None = None) -> dict:
    now = time.time() if now is None else now
    return _totals(load().get(day_key(now)))


def record(tokens: int, now: float | None = None, calls: int = 1) -> dict:
    now = time.time() if now is None else now
    try:
        tokens = max(0, int(tokens or 0))
    except (TypeError, ValueError):
        tokens = 0
    with _LOCK:
        data = load()
        key = day_key(now)
        row = _totals(data.get(key))
        row["calls"] += max(0, int(calls))
        row["tokens"] += tokens
        data[key] = row
        save(data)
    return row


def level_for(totals: dict, call_cap: int, token_cap: int) -> str:
    """ok | active_only | stop. Pure."""
    calls = int(totals.get("calls") or 0)
    toks = int(totals.get("tokens") or 0)
    if calls >= call_cap or toks >= token_cap:
        return "stop"
    if calls >= SIDE_DROP_FRAC * call_cap or toks >= SIDE_DROP_FRAC * token_cap:
        return "active_only"
    return "ok"


def level(now: float | None = None, env: dict | None = None) -> str:
    return level_for(today(now), *caps(env))


def summary(now: float | None = None, env: dict | None = None) -> dict:
    totals = today(now)
    call_cap, token_cap = caps(env)
    return {
        **totals,
        "call_cap": call_cap,
        "token_cap": token_cap,
        "level": level_for(totals, call_cap, token_cap),
    }
