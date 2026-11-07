"""Learned Stage 1 thresholds per task kind (PLAN.md §3.3).

threshold(kind) = 0.75 + 0.2 * (1 - acceptance) + 0.05 * stuck_misses, where
acceptance is the Beta(2,2) posterior mean over that kind's offer answers in
``offers.jsonl``. ``yes`` counts as accept; ``no``/``silent``/``never`` as
decline; shadow offers do not count. Stuck takeovers ("gate misses") are
kept in ``~/.jarvis/aware/feasibility.json`` and also logged as fixtures in
``gate_misses.jsonl``.
"""

from __future__ import annotations

import json
import time

from aware import state as st

BASE = 0.75
SPAN = 0.2
STUCK_STEP = 0.05
CEILING = 0.99
PRIOR_A = 2.0
PRIOR_B = 2.0


def path():
    return st.aware_dir() / "feasibility.json"


def misses_path():
    return st.aware_dir() / "gate_misses.jsonl"


def load() -> dict:
    try:
        data = json.loads(path().read_text())
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def acceptance(history: list[dict], task_kind: str) -> float:
    """Beta(2,2) posterior mean of yes over answered offers of this kind. Pure."""
    yes = no = 0
    for row in history or []:
        if row.get("kind") != task_kind or row.get("shadow"):
            continue
        ans = row.get("answer")
        if ans == "yes":
            yes += 1
        elif ans in ("no", "silent", "never"):
            no += 1
    return (PRIOR_A + yes) / (PRIOR_A + PRIOR_B + yes + no)


def threshold_from(history: list[dict], task_kind: str, stuck: int = 0) -> float:
    """Pure threshold for given history and stuck count."""
    acc = acceptance(history, task_kind)
    value = BASE + SPAN * (1.0 - acc) + STUCK_STEP * max(0, int(stuck))
    return round(min(CEILING, value), 4)


def threshold(task_kind: str, history: list[dict] | None = None) -> float:
    if history is None:
        try:
            from aware import offers

            history = offers.history()
        except Exception:
            history = []
    stuck = int((load().get("stuck") or {}).get(task_kind, 0) or 0)
    return threshold_from(history, task_kind, stuck)


def record_stuck(
    task_kind: str, fixture: dict | None = None, now: float | None = None
) -> None:
    """A takeover of this kind ended stuck: raise its threshold, log a gate miss."""
    now = time.time() if now is None else now
    data = load()
    stuck = data.get("stuck") if isinstance(data.get("stuck"), dict) else {}
    stuck[task_kind] = int(stuck.get(task_kind, 0) or 0) + 1
    data["stuck"] = stuck
    st.write_private(path(), json.dumps(data, sort_keys=True))
    row = {"ts": now, "kind": task_kind, "fixture": fixture or {}}
    st.append_private(misses_path(), json.dumps(row, default=str))
