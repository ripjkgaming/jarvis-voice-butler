"""Background proactive watcher: polls telemetry, speaks only on escalation.

Runs as an asyncio task inside my_agent(); failures are swallowed so a
sensor hiccup can never break a voice session. Every utterance passes
through ProactivePolicy (quiet hours, daily cap, batching, dedupe) —
the escalation math below only decides *what* is worth saying.
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
from proactive.policy import ProactivePolicy

SnapshotFn = Callable[[], TelemetrySnapshot]
SpeakFn = Callable[[str], Awaitable[None]]
LogFn = Callable[[str, str], None]


def backoff_sleep(poll_s: float, failures: int, cap_s: float) -> float:
    """Poll interval with exponential backoff on sensor failures. Pure."""
    if failures <= 0:
        return poll_s
    return min(cap_s, poll_s * (2.0 ** min(failures, 8)))


class ProactiveWatcher:
    """Edge-triggered battery/climb monitor with cooldowns + delivery gate."""

    def __init__(
        self,
        snapshot_fn: SnapshotFn,
        speak_fn: SpeakFn,
        *,
        poll_s: float = 15.0,
        cooldown_s: float = 120.0,
        policy: ProactivePolicy | None = None,
        now_fn: Callable[[], float] | None = None,
        log_fn: LogFn | None = None,
        stale_after_s: float = 300.0,
        backoff_cap_s: float = 300.0,
    ) -> None:
        self._snapshot_fn = snapshot_fn
        self._speak_fn = speak_fn
        self._poll_s = poll_s
        self._cooldown_s = cooldown_s
        self._policy = policy or ProactivePolicy(now_fn=now_fn, log_fn=log_fn)
        self._now = now_fn or time.time
        if log_fn is not None:
            self._log = log_fn
        else:
            from system import log_action

            self._log = log_action
        self._stale_after_s = stale_after_s
        self._backoff_cap_s = backoff_cap_s
        self._history: list[tuple[float, float]] = []
        self._prev_urgency = "ok"
        self._last_alert: float | None = None
        self._last_ok: float | None = None
        self._failures = 0
        self._in_tick = False
        self._task: asyncio.Task | None = None

    def _note(self, reason: str, detail: str = "") -> None:
        with contextlib.suppress(Exception):
            self._log("proactive", f"{reason} {detail}".strip())

    def sleep_for(self) -> float:
        """Current loop interval (backoff grows it on failures). Pure read."""
        return backoff_sleep(self._poll_s, self._failures, self._backoff_cap_s)

    async def _speak_decision_texts(self, texts: list[str]) -> None:
        for line in texts:
            with contextlib.suppress(Exception):
                await self._speak_fn(line)

    async def _tick(self, now: float) -> None:
        if self._in_tick:
            self._note("overlap-skip", "previous tick still speaking")
            return
        self._in_tick = True
        try:
            try:
                snap = self._snapshot_fn()
            except Exception:
                self._failures += 1
                self._note("snapshot-error", f"failures={self._failures}")
                return
            self._failures = 0
            if self._last_ok is not None and (
                now - self._last_ok > self._stale_after_s
            ):
                # Sensors were dark for minutes: take over with a fresh
                # baseline instead of alerting on ancient drain math.
                self._history = []
                self._prev_urgency = "ok"
                self._note("stale-takeover", f"dark={now - self._last_ok:.0f}s")
            self._last_ok = now
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
                decision = self._policy.consider(
                    warning_line(urgency, mins, pct),
                    urgency=urgency,
                    fingerprint_fp=f"power:{urgency}",
                    now=now,
                )
                await self._speak_decision_texts(decision.texts)
            else:
                # No new escalation — but quiet hours may just have ended.
                await self._speak_decision_texts(self._policy.drain_queue(now).texts)
            self._prev_urgency = urgency
        finally:
            self._in_tick = False

    async def _loop(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                await self._tick(self._now())
            await asyncio.sleep(self.sleep_for())

    def start(self) -> asyncio.Task:
        """Begin background polling. Idempotent."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
        return self._task

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
