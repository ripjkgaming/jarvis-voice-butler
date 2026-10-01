"""Do-not-disturb rule: quiet hours only bite once Sir has really gone away.

DND is active only when BOTH hold:
- it is past the quiet window's start (default 22:30, until 07:00), and
- there has been no keyboard or mouse input for more than 30 minutes
  (JARVIS_QUIET_IDLE_MINUTES).
So while Sir is still typing at 23:00 nothing is held back, and a night with
input unknown (probe not running) also counts as "not away".

Override the window with JARVIS_QUIET_HOURS ("HH:MM-HH:MM").
"""

from __future__ import annotations

import datetime
import os
import time

from proactive import policy

IDLE_ENV = "JARVIS_QUIET_IDLE_MINUTES"
DEFAULT_IDLE_MIN = 30


def idle_minutes_required() -> int:
    try:
        return max(1, int(os.environ.get(IDLE_ENV, DEFAULT_IDLE_MIN)))
    except ValueError:
        return DEFAULT_IDLE_MIN


def idle_long_enough(now: float | None = None, idle_fn=None) -> bool:
    """True when input has been idle for the required minutes (unknown -> False)."""
    try:
        if idle_fn is None:
            import input_idle

            idle_fn = input_idle.idle_seconds
        secs = idle_fn(now=now)
    except Exception:
        return False
    return secs is not None and secs >= idle_minutes_required() * 60


def active(now: float | None = None, *, idle_fn=None, window=None) -> bool:
    """Is DND on right now? Pure apart from reading the idle state file."""
    now = time.time() if now is None else now
    win = window if window is not None else policy.quiet_hours()
    if not policy.in_quiet_hours(datetime.datetime.fromtimestamp(now), win):
        return False
    return idle_long_enough(now, idle_fn)
