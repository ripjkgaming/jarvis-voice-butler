"""Learn which tools Sir actually uses, so the router carries only those.

Every function tool call is counted (agent._wrap_tools_with_timing). Counts
decay with a HALF_LIFE_DAYS half-life, so what Sir used last month fades if he
stops. At session start the Assistant asks plan_cold() which of its tools have
gone unused: those leave the router's per-turn tool list (fewer schemas in
every model request = lower latency) and are served behind one handoff
(transfer_to_more_tools) instead. Rare tools Sir keeps using are promoted back
(promote()). Nothing is demoted until MIN_TOTAL_CALLS of history exist, so a
fresh install behaves exactly as before.

State: ~/.jarvis/tool_usage.json (JARVIS_HOME overrides). JARVIS_ADAPTIVE_TOOLS=0
turns the whole thing off. Never raises into the voice path.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import threading
import time
from collections.abc import Iterable
from pathlib import Path

HALF_LIFE_DAYS = 14.0
MIN_TOTAL_CALLS = 60  # decayed calls of history required before any demotion
COLD_BELOW = 0.75  # decayed call-count under which a router tool is demoted
HOT_AT = 3.0  # decayed call-count at which a rare tool is promoted
SAVE_EVERY_S = 30.0

_state: dict = {"data": None, "saved": 0.0, "dirty": False}
_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("JARVIS_ADAPTIVE_TOOLS", "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def _path() -> Path:
    h = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(h) if h else Path.home() / ".jarvis") / "tool_usage.json"


def _load() -> dict:
    if _state["data"] is None:
        try:
            raw = json.loads(_path().read_text())
            _state["data"] = raw if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            _state["data"] = {}
    return _state["data"]


def _decayed(entry: object, now: float) -> float:
    try:
        score, ts = float(entry[0]), float(entry[1])  # type: ignore[index]
    except (TypeError, ValueError, IndexError, KeyError):
        return 0.0
    age_days = max(0.0, now - ts) / 86400.0
    return score * 0.5 ** (age_days / HALF_LIFE_DAYS)


def record(tool: str, now: float | None = None) -> None:
    """Count one call of `tool`. Cheap: in-memory, disk write debounced."""
    now = time.time() if now is None else now
    with _lock:
        data = _load()
        data[tool] = [_decayed(data.get(tool), now) + 1.0, now]
        _state["dirty"] = True
        if time.monotonic() - _state["saved"] >= SAVE_EVERY_S:
            _flush_locked()


def _flush_locked() -> None:
    _state["saved"] = time.monotonic()
    _state["dirty"] = False
    with contextlib.suppress(OSError, TypeError):
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps(_state["data"]))


def flush() -> None:
    with _lock:
        if _state["dirty"] and _state["data"] is not None:
            _flush_locked()


atexit.register(flush)


def score(tool: str, now: float | None = None) -> float:
    now = time.time() if now is None else now
    with _lock:
        return _decayed(_load().get(tool), now)


def total(now: float | None = None) -> float:
    now = time.time() if now is None else now
    with _lock:
        return sum(_decayed(v, now) for v in _load().values())


def plan_cold(
    ids: Iterable[str], protected: Iterable[str] = (), now: float | None = None
) -> set[str]:
    """Router tool ids to move behind the handoff. Empty until enough history."""
    if not enabled():
        return set()
    now = time.time() if now is None else now
    if total(now) < MIN_TOTAL_CALLS:
        return set()
    keep = set(protected)
    return {i for i in ids if i not in keep and score(i, now) < COLD_BELOW}


def promote(candidates: Iterable[str], now: float | None = None) -> set[str]:
    """Rare-handoff tool ids Sir uses often enough to live on the router."""
    if not enabled():
        return set()
    now = time.time() if now is None else now
    return {i for i in candidates if score(i, now) >= HOT_AT}


def reset() -> None:
    """Forget in-memory state (tests)."""
    with _lock:
        _state.update(data=None, saved=0.0, dirty=False)
