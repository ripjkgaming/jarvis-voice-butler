"""Instant acknowledgements for slow tools (IRONMAN_SPEC §4).

When a tool is expected to take more than ACK_AFTER_S, Jarvis says a short
cached line ("On it, Sir.") before the work starts, so Sir is not left in
silence. Lines are fixed strings (no model call). "Expected" comes from a
running average of each tool's real durations (~/.jarvis/tool_durations.json,
fed by the agent's tool timer) plus a few tools known to be slow.

Rate limited to one ack per ACK_GAP_S. JARVIS_ACKS=0 turns them off.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import threading
import time
from pathlib import Path

ACK_AFTER_S = 2.0
ACK_GAP_S = 20.0
EMA_ALPHA = 0.3
LINES = (
    "On it, Sir.",
    "One moment, Sir.",
    "Right away.",
    "Working on it, Sir.",
    "Give me a second.",
)
#: Slow before any history exists (web, research, backend jobs, mail lists).
KNOWN_SLOW = frozenset(
    {
        "search_the_web",
        "open_url",
        "read_page",
        "start_research",
        "ask_backend",
        "gmail_inbox",
        "gmail_thread",
        "import_schedule_to_calendar",
        "morning_briefing",
        "daily_briefing",
        "news_digest",
        "catch_me_up",
        "run_tests",
        "explain_failure",
        "look_at_screen",
    }
)
#: Durations are persisted at most this often; record() runs on the event
#: loop after every tool call, so a disk write each time was pure latency.
SAVE_EVERY_S = 30.0
_state = {"last": 0.0, "i": 0, "ema": None, "saved": 0.0, "dirty": False}
_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("JARVIS_ACKS", "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def _path() -> Path:
    h = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(h) if h else Path.home() / ".jarvis") / "tool_durations.json"


def _load() -> dict:
    if _state["ema"] is None:
        try:
            data = json.loads(_path().read_text())
            _state["ema"] = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _state["ema"] = {}
    return _state["ema"]


def record(tool: str, seconds: float) -> None:
    """Feed one real duration into the tool's running average."""
    with _lock:
        ema = _load()
        old = ema.get(tool)
        ema[tool] = (
            seconds
            if old is None
            else (1 - EMA_ALPHA) * float(old) + EMA_ALPHA * seconds
        )
        _state["dirty"] = True
        if time.monotonic() - _state["saved"] >= SAVE_EVERY_S:
            _flush_locked()


def _flush_locked() -> None:
    _state["saved"] = time.monotonic()
    _state["dirty"] = False
    with contextlib.suppress(OSError):
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps(_state["ema"]))


def flush() -> None:
    """Persist any unsaved durations now (also runs at interpreter exit)."""
    with _lock:
        if _state["dirty"] and _state["ema"] is not None:
            _flush_locked()


atexit.register(flush)


def expected_slow(tool: str) -> bool:
    with _lock:
        avg = _load().get(tool)
    if avg is not None:
        return float(avg) > ACK_AFTER_S
    return tool in KNOWN_SLOW


def next_line(tool: str, now: float | None = None) -> str | None:
    """The line to say before `tool` runs, or None (fast tool / too soon)."""
    if not enabled() or not expected_slow(tool):
        return None
    now = time.monotonic() if now is None else now
    with _lock:
        if now - _state["last"] < ACK_GAP_S:
            return None
        _state["last"] = now
        line = LINES[_state["i"] % len(LINES)]
        _state["i"] += 1
    return line


def reset() -> None:
    """Forget in-memory state (tests)."""
    with _lock:
        _state.update(last=0.0, i=0, ema=None, saved=0.0, dirty=False)
