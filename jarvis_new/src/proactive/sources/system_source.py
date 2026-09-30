"""System signals: the home disk is nearly full.

Battery stays with the in-call ProactiveWatcher (proactive/watcher.py) so
it is never announced twice.
"""

from __future__ import annotations

import datetime as dt
import shutil
import time
from pathlib import Path

from proactive.signals import Signal

LOW_PCT = 5.0
WARN_PCT = 10.0


def poll(now: float | None = None, usage=None) -> list[Signal]:
    now = time.time() if now is None else now
    total, _used, free = (usage or shutil.disk_usage)(str(Path.home()))
    if not total:
        return []
    pct = free / total * 100
    if pct >= WARN_PCT:
        return []
    day = dt.date.fromtimestamp(now).isoformat()
    gb = free / 1e9
    urgency = "high" if pct < LOW_PCT else "normal"
    return [
        Signal(
            kind="system",
            key=f"disk:{urgency}:{day}",
            title=f"Disk space is low, Sir: {gb:.1f} gigabytes left ({pct:.0f} percent).",
            detail=f"Home disk {pct:.1f}% free",
            urgency=urgency,
            ts=now,
        )
    ]
