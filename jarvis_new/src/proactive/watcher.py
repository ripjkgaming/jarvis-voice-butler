"""Background proactive watcher: polls telemetry, speaks only on escalation.

Runs as an asyncio task inside my_agent(); failures are swallowed so a
sensor hiccup can never break a voice session.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable

from context.telemetry import TelemetrySnapshot
from proactive.monitors import (
    drain_rate_per_min,
    predict_depletion_mins,
    should_alert,
    urgency_for,
    warning_line,
)

SnapshotFn = Callable[[], TelemetrySnapshot]
SpeakFn = Callable[[str], Awaitable[None]]


class ProactiveWatcher:
    """Edge-triggered battery/climb monitor with cooldowns."""

    def __init__(
        self,
        snapshot_fn: SnapshotFn,
        speak_fn: SpeakFn,
        *,
        poll_s: float = 15.0,
        cooldown_s: float = 120.0,
    ) -> None:
        self._snapshot_fn = snapshot_fn
        self._speak_fn = speak_fn
        self._poll_s = poll_s
        self._cooldown_s = cooldown_s
        self._history: list[tuple[float, float]] = []
        self._prev_urgency = "ok"
        self._last_alert: float | None = None
        self._task: asyncio.Task | None = None

    async def _tick(self, now: float) -> None:
        snap = self._snapshot_fn()
        if snap.battery_pct is not None:
            self._history.append((now, snap.battery_pct))
            self._history = self._history[-12:]
        rate = drain_rate_per_min(self._history)
        pct = snap.battery_pct if snap.battery_pct is not None else 100.0
        mins = predict_depletion_mins(pct, rate)
        urgency = urgency_for(mins, pct)
        if should_alert(
            self._prev_urgency,
            urgency,
            cooldown_s=self._cooldown_s,
            last_alert_ts=self._last_alert,
            now=now,
        ):
            self._last_alert = now
            await self._speak_fn(warning_line(urgency, mins, pct))
        self._prev_urgency = urgency

    async def _loop(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                await self._tick(time.time())
            await asyncio.sleep(self._poll_s)

    def start(self) -> asyncio.Task:
        """Begin background polling. Idempotent."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
        return self._task

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
