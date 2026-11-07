"""When the next Always Aware tick runs. Pure logic plus tiny /proc readers.

PLAN.md §1.1: base 180 s clamped to [120, 300] with ±20 % jitter; -30 s
after real change (or importance >= 2 or an opportunity), +60 s after an
unchanged/importance-0 tick; battery floor 240 s, ticks stop under 25 %;
load and memory guards; 429 -> 30 min backoff, outage -> 10 min, three
capture failures in a row -> 1 h.

Activity-aware pacing (on top of the above):
- Idle (no input and no media playing): the loop parks. It makes no ticks and
  wakes within PARK_POLL_S of Sir returning, then ticks RESUME_S later.
- Unchanged or low-value screens while active (same page, a film playing)
  climb UNCHANGED_LADDER, 5 -> 10 -> 20 -> 30 min. A real change resets it.
- Study: when an opportunity is first seen, the next look comes STUDY_S
  later so Jarvis watches for a bit before offering anything.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path

BASE_S = 180
MIN_S = 120
MAX_S = 300
BATTERY_FLOOR_S = 240
BATTERY_STOP_PCT = 25.0
SHORTEN_S = 30
LENGTHEN_S = 60
LOAD_MAX = 0.85
MEM_MIN_GIB = 1.5
BACKOFF_S = {"quota": 1800, "outage": 600, "capture": 3600}
CAPTURE_FAILS_BEFORE_PAUSE = 3
UNCHANGED_LADDER = (300, 600, 1200, 1800)
LOW_VALUE_ACTIVITIES = ("media", "gaming", "idle")
PARK_POLL_S = 15.0
RESUME_S = 30.0
STUDY_S = 10.0


@dataclass
class Signals:
    on_ac: bool = True
    battery_pct: float = 100.0
    load_per_cpu: float = 0.0
    mem_available_gib: float = 99.0


def guard(sig: Signals) -> str | None:
    """Reason to skip this tick for power/load, or None. Pure."""
    if not sig.on_ac and sig.battery_pct < BATTERY_STOP_PCT:
        return "battery_low"
    if sig.load_per_cpu > LOAD_MAX:
        return "load_high"
    if sig.mem_available_gib < MEM_MIN_GIB:
        return "memory_low"
    return None


class Scheduler:
    def __init__(self, rng: random.Random | None = None, jitter: float = 0.2):
        self.rng = rng or random.Random()
        self.jitter = jitter
        self.base = BASE_S
        self.last_tick = 0.0
        self.blocked_until = 0.0
        self.block_reason = ""
        self.capture_fails = 0
        self.quiet_streak = 0  # unchanged / low-value ticks in a row
        self.study = False  # next look is a short follow-up

    def next_delay(self, sig: Signals) -> float:
        floor = MIN_S if sig.on_ac else BATTERY_FLOOR_S
        if self.study:
            return float(STUDY_S)
        if self.quiet_streak and self.base >= MAX_S:
            ceil = float(
                UNCHANGED_LADDER[min(self.quiet_streak, len(UNCHANGED_LADDER)) - 1]
            )
            base = ceil
        else:
            ceil = float(MAX_S)
            base = max(floor, min(ceil, self.base))
        if self.jitter:
            base *= 1 + self.rng.uniform(-self.jitter, self.jitter)
        return float(max(floor, min(ceil, base)))

    def after_tick(
        self,
        *,
        changed: bool,
        importance: int,
        opportunity: bool,
        activity: str = "",
        study: bool = False,
    ) -> None:
        self.capture_fails = 0
        self.study = study
        low_value = activity in LOW_VALUE_ACTIVITIES and importance <= 1
        if opportunity or importance >= 2 or (changed and not low_value):
            self.base = max(MIN_S, self.base - SHORTEN_S)
            self.quiet_streak = 0
        else:
            if self.base >= MAX_S:
                self.quiet_streak += 1
            self.base = min(MAX_S, self.base + LENGTHEN_S)

    def resume(self) -> None:
        """Sir is back after an idle park: start fast again."""
        self.base = BASE_S
        self.quiet_streak = 0
        self.study = False

    def note_error(self, kind: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if kind == "capture":
            self.capture_fails += 1
            if self.capture_fails < CAPTURE_FAILS_BEFORE_PAUSE:
                return
            self.capture_fails = 0
        secs = BACKOFF_S.get(kind)
        if secs:
            self.blocked_until = max(self.blocked_until, now + secs)
            self.block_reason = f"backoff_{kind}"

    def backoff_reason(self, now: float | None = None) -> str | None:
        now = time.time() if now is None else now
        return self.block_reason if now < self.blocked_until else None

    def may_tick(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        gap = STUDY_S / 2 if self.study else MIN_S
        return now - self.last_tick >= gap


@contextlib.contextmanager
def tick_lock(path: Path):
    """Non-blocking exclusive lock; yields False when another tick holds it."""
    fd = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            fd = None
            yield False
            return
        yield True
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)


def read_signals(
    loadavg: Path = Path("/proc/loadavg"),
    meminfo: Path = Path("/proc/meminfo"),
    power: Path = Path("/sys/class/power_supply"),
    nproc: int | None = None,
) -> Signals:
    """Live power/load/memory. Missing data = AC power, no load. Never raises."""
    sig = Signals()
    n = nproc or os.cpu_count() or 1
    with contextlib.suppress(OSError, ValueError, IndexError):
        sig.load_per_cpu = float(loadavg.read_text().split()[0]) / n
    with contextlib.suppress(OSError, ValueError):
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                sig.mem_available_gib = int(line.split()[1]) / (1024 * 1024)
    with contextlib.suppress(OSError):
        for supply in sorted(power.iterdir()):
            online = supply / "online"
            cap = supply / "capacity"
            with contextlib.suppress(OSError, ValueError):
                if online.exists():
                    sig.on_ac = online.read_text().strip() != "0"
                if cap.exists():
                    sig.battery_pct = float(cap.read_text().strip())
    return sig
