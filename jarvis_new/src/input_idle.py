"""Has Sir touched the keyboard or mouse recently? (replaces camera presence)

A tiny helper process (input_idle_helper.py, system python + pywayland) asks
the compositor for "no input for N minutes" and keeps a state file current.
This module only reads that file, starting the helper when it is missing.

Tri-state on purpose, like the old camera gate:
- "active":  input within the last N minutes, so speaking aloud is fine,
- "idle":    no input for N minutes, so stay quiet,
- "unknown": helper not running or state stale, never treated as active.

N defaults to 5 (JARVIS_IDLE_MINUTES). Everything is fail-soft.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HELPER = Path(__file__).resolve().parent / "input_idle_helper.py"
#: Heartbeat is written every 15s; older than this means the helper is gone.
STALE_S = 60.0

_proc: subprocess.Popen | None = None


def idle_minutes() -> int:
    try:
        return max(1, int(os.environ.get("JARVIS_IDLE_MINUTES", "5")))
    except ValueError:
        return 5


def state_path() -> Path:
    home = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(home) if home else Path.home() / ".jarvis") / "input_idle.json"


def read_state(path: Path | None = None, now: float | None = None) -> str:
    """State file -> "active" | "idle" | "unknown". Pure apart from the read."""
    now = time.time() if now is None else now
    try:
        data = json.loads((path or state_path()).read_text())
    except Exception:
        return "unknown"
    if not isinstance(data, dict):
        return "unknown"
    if now - float(data.get("updated") or 0) > STALE_S:
        return "unknown"
    if int(data.get("timeout_s") or 0) != idle_minutes() * 60:
        return "unknown"
    return "idle" if data.get("idle") else "active"


def idle_seconds(path: Path | None = None, now: float | None = None) -> float | None:
    """How long there has been no input: None if unknown, 0 while active.

    The compositor reports "idle" after `timeout_s` without input, so the
    real idle time is that plus how long ago the report fired.
    """
    now = time.time() if now is None else now
    try:
        data = json.loads((path or state_path()).read_text())
    except Exception:
        return None
    if not isinstance(data, dict) or now - float(data.get("updated") or 0) > STALE_S:
        return None
    if int(data.get("timeout_s") or 0) != idle_minutes() * 60:
        return None
    if not data.get("idle"):
        return 0.0
    return max(0.0, now - float(data.get("changed") or now)) + float(data["timeout_s"])


def read_recent(path: Path | None = None, now: float | None = None) -> bool:
    """True when the helper saw keyboard/mouse input in the last ~10 seconds."""
    now = time.time() if now is None else now
    try:
        data = json.loads((path or state_path()).read_text())
        return (
            isinstance(data, dict)
            and now - float(data.get("updated") or 0) <= STALE_S
            and bool(data.get("recent"))
            and not data.get("idle")
        )
    except Exception:
        return False


def _system_python() -> str | None:
    """The interpreter that has pywayland (the venv one does not)."""
    for cand in ("/usr/bin/python3", shutil.which("python3")):
        if cand and Path(cand) != Path(sys.executable):
            return cand
    return None


def ensure_helper(spawn=subprocess.Popen, path: Path | None = None) -> bool:
    """Start the helper when no live one is reporting. True if running."""
    global _proc
    path = path or state_path()
    if os.environ.get("PYTEST_CURRENT_TEST") and spawn is subprocess.Popen:
        return False  # tests must never start the real probe
    if read_state(path) != "unknown":
        return True
    if _proc is not None and _proc.poll() is None:
        return True
    py = _system_python()
    if py is None or not HELPER.is_file():
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _proc = spawn(
            [py, str(HELPER), str(path), str(idle_minutes() * 60)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        return False
    return True


def status() -> dict:
    """{"status": "present"|"absent"|"unknown"} for DraftWatcher (same shape as
    the old camera check: present = input recently, absent = idle)."""
    with contextlib.suppress(Exception):
        ensure_helper()
    return {
        "status": {"active": "present", "idle": "absent"}.get(read_state(), "unknown")
    }
