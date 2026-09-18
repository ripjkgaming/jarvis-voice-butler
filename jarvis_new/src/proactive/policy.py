"""Proactive delivery policy: quiet hours, daily cap, batching, dedupe.

The background watcher (watcher.py) may only speak through this gate.
Rules, in order:

1. QUIET HOURS (``JARVIS_QUIET_HOURS``, default ``22:00-07:00`` with
   overnight wrap): nothing speaks — every urgency is queued, never
   wakes the user. The queue flushes once quiet ends.
2. DEDUPE (1 h): an identical fingerprint delivered within the hour is
   dropped, not repeated.
3. SAFETY BYPASS: ``critical`` urgency (landing/charge-now battery)
   speaks immediately and bypasses the daily cap — but the bypass is
   logged, and quiet hours still hold it.
4. DAILY CAP (``JARVIS_PROACTIVE_MAX_PER_DAY``, default 3): routine and
   urgent notes past the cap are dropped until tomorrow.
5. BATCHING (30 min): routine ``info`` notes collect into one combined
   utterance per window instead of interrupting one by one. ``urgent``
   skips the batch and speaks at once.

Every decision carries a reason code, appended to ``~/.jarvis/actions.log``
(category ``proactive``) via ``system.log_action`` — audit the gate by
reading that log. All time flows through an injectable clock so tests
freeze it; production defaults to wall time. There are deliberately NO
destructive actions here: the policy only ever permits or delays speech.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

QUIET_ENV = "JARVIS_QUIET_HOURS"
MAX_ENV = "JARVIS_PROACTIVE_MAX_PER_DAY"
DEFAULT_QUIET = "22:00-07:00"
DEFAULT_MAX_PER_DAY = 3
BATCH_WINDOW_S = 30 * 60.0
DEDUPE_WINDOW_S = 60 * 60.0

# Reason codes (stable strings — grep actions.log for these).
REASON_DELIVERED = "delivered"
REASON_URGENT_BYPASS = "urgent-bypass"
REASON_BATCHED_FLUSH = "batched-flush"
REASON_BATCHED_WAITING = "batched-waiting"
REASON_QUEUED_QUIET = "queued-quiet-hours"
REASON_QUIET_FLUSH = "quiet-flush"
REASON_CAPPED = "capped"
REASON_DEDUPED = "deduped"


def parse_quiet_hours(raw: str | None) -> tuple[int, int]:
    """'HH:MM-HH:MM' -> (start_min, end_min) since midnight. Pure.

    Garbage falls back to the default window (fail-quiet, literally:
    a malformed value must never accidentally open the night).
    """
    try:
        start_s, end_s = (raw or "").split("-", 1)
        sh, sm = (int(p) for p in start_s.split(":"))
        eh, em = (int(p) for p in end_s.split(":"))
        if not (0 <= sh < 24 and 0 <= sm < 60 and 0 <= eh < 24 and 0 <= em < 60):
            raise ValueError("out of range")
        return sh * 60 + sm, eh * 60 + em
    except (ValueError, AttributeError):
        return parse_quiet_hours(DEFAULT_QUIET)


def quiet_hours() -> tuple[int, int]:
    """Configured window from the environment. Pure (env only)."""
    return parse_quiet_hours(os.environ.get(QUIET_ENV, DEFAULT_QUIET))


def max_per_day() -> int:
    """Configured daily cap from the environment. Pure (env only)."""
    try:
        return max(0, int(os.environ.get(MAX_ENV, DEFAULT_MAX_PER_DAY)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_PER_DAY


def in_quiet_hours(dt: datetime.datetime, window: tuple[int, int]) -> bool:
    """True when dt falls inside window (overnight wrap aware). Pure."""
    start, end = window
    mins = dt.hour * 60 + dt.minute
    if start <= end:
        return start <= mins < end
    return mins >= start or mins < end


def fingerprint(text: str) -> str:
    """Stable id for a speakable line (whitespace-normalized). Pure."""
    return hashlib.sha256(" ".join((text or "").split()).encode()).hexdigest()[:16]


@dataclass
class Decision:
    """One gate outcome: speak these texts now, or hold/drop with a reason."""

    action: str  # "speak" | "defer" | "drop"
    reason: str
    texts: list[str] = field(default_factory=list)


class ProactivePolicy:
    """Delivery gate with per-day cap, batching, dedupe, quiet queue."""

    def __init__(
        self,
        *,
        window: tuple[int, int] | None = None,
        cap: int | None = None,
        now_fn: Callable[[], float] | None = None,
        log_fn: Callable[[str, str], None] | None = None,
    ) -> None:
        self._window = window if window is not None else quiet_hours()
        self._cap = cap if cap is not None else max_per_day()
        self._now = now_fn or time.time
        if log_fn is not None:
            self._log = log_fn
        else:
            from system import log_action

            self._log = log_action
        self._day = ""
        self._delivered_today = 0
        self._recent: dict[str, float] = {}
        self._batch: list[tuple[float, str, str]] = []
        self._batch_start: float | None = None
        self._queue: list[tuple[float, str, str, str]] = []

    def _note(self, reason: str, detail: str = "") -> None:
        with contextlib.suppress(Exception):
            self._log("proactive", f"{reason} {detail}".strip())

    def _roll_day(self, day: str) -> None:
        if day != self._day:
            self._day = day
            self._delivered_today = 0

    def _remember(self, fp: str, now: float) -> None:
        self._recent[fp] = now
        cutoff = now - DEDUPE_WINDOW_S
        self._recent = {k: v for k, v in self._recent.items() if v >= cutoff}

    def _record(self, fp: str, now: float) -> None:
        self._remember(fp, now)
        self._delivered_today += 1

    def consider(
        self,
        text: str,
        *,
        urgency: str = "info",
        fingerprint_fp: str | None = None,
        now: float | None = None,
    ) -> Decision:
        """Gate one speakable line. Returns what to speak now (if anything)."""
        at = now if now is not None else self._now()
        dt = datetime.datetime.fromtimestamp(at)
        self._roll_day(dt.date().isoformat())
        fp = fingerprint_fp or fingerprint(text)
        tag = f"urgency={urgency} fp={fp}"
        if (
            (fp in self._recent and at - self._recent[fp] < DEDUPE_WINDOW_S)
            or any(bfp == fp for _, _, bfp in self._batch)
            or any(qfp == fp for _, _, _, qfp in self._queue)
        ):
            # Repeats collapse even before delivery: holding the same line
            # twice (in the batch, in the quiet queue) would speak it twice.
            self._note(REASON_DEDUPED, tag)
            return Decision("drop", REASON_DEDUPED)
        if in_quiet_hours(dt, self._window):
            self._queue.append((at, text, urgency, fp))
            self._note(REASON_QUEUED_QUIET, tag)
            return Decision("defer", REASON_QUEUED_QUIET)
        if urgency == "critical":
            # Safety bypasses the cap (logged); quiet hours already held it.
            self._record(fp, at)
            self._note(REASON_URGENT_BYPASS, tag)
            return Decision("speak", REASON_URGENT_BYPASS, [text])
        if self._delivered_today >= self._cap:
            self._note(REASON_CAPPED, tag)
            return Decision("drop", REASON_CAPPED)
        if urgency == "urgent":
            self._record(fp, at)
            self._note(REASON_DELIVERED, tag)
            return Decision("speak", REASON_DELIVERED, [text])
        # Routine info: collect into the 30-minute batch window.
        if self._batch_start is None:
            self._batch_start = at
        self._batch.append((at, text, fp))
        if at - self._batch_start >= BATCH_WINDOW_S:
            texts = [t for _, t, _ in self._batch]
            for _, _, bfp in self._batch:
                self._remember(bfp, at)
            self._delivered_today += 1  # one combined utterance, one delivery
            self._batch = []
            self._batch_start = None
            self._note(REASON_BATCHED_FLUSH, f"n={len(texts)}")
            return Decision("speak", REASON_BATCHED_FLUSH, texts)
        self._note(REASON_BATCHED_WAITING, tag)
        return Decision("defer", REASON_BATCHED_WAITING)

    def drain_queue(self, now: float | None = None) -> Decision:
        """Flush quiet-held lines once quiet ends (cap still applies)."""
        at = now if now is not None else self._now()
        dt = datetime.datetime.fromtimestamp(at)
        self._roll_day(dt.date().isoformat())
        if not self._queue or in_quiet_hours(dt, self._window):
            return Decision("drop", REASON_QUEUED_QUIET)
        if self._delivered_today >= self._cap:
            self._note(REASON_CAPPED, "quiet-flush-held")
            return Decision("drop", REASON_CAPPED)
        texts = [t for _, t, _, _ in self._queue]
        for _, _, _, fp in self._queue:
            self._remember(fp, at)
        self._delivered_today += 1  # one combined utterance, one delivery
        n = len(texts)
        self._queue = []
        self._note(REASON_QUIET_FLUSH, f"n={n}")
        return Decision("speak", REASON_QUIET_FLUSH, texts)

    @property
    def queued_count(self) -> int:
        return len(self._queue)
