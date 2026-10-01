"""Draft watcher: announce a ready email draft when Sir is at the laptop.

Runs as an asyncio task inside my_agent(), modeled on ProactiveWatcher. It
polls the drafts directory every ~20s and does nothing else while no draft
is pending.

With a draft waiting, input_idle.status() decides whether Sir is around: any
keyboard or mouse input in the last JARVIS_IDLE_MINUTES (5) counts as
"present". No camera is used. Delivery needs two consecutive "present"
readings about 2s apart. "absent" (no input for 5 minutes, so nothing is
spoken aloud) and "unknown" (probe not running) both hold the draft and retry
with backoff, 20s growing to a 5-minute cap; the draft stays in the Drafts
window meanwhile. The spoken line goes through ProactivePolicy (quiet hours,
dedupe, daily cap) and session.say, then the draft becomes "announced".
Nothing is ever sent from here.

Every failure is swallowed and logged so it can never break a voice session.
JARVIS_DRAFT_ANNOUNCE=0 turns announcing off.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import draft_engine
from proactive.policy import REASON_CAPPED, REASON_DEDUPED, ProactivePolicy
from proactive.watcher import backoff_sleep

ANNOUNCE_ENV = "JARVIS_DRAFT_ANNOUNCE"

SpeakFn = Callable[[str], Awaitable[None]]
PresenceFn = Callable[[], dict]
SleepFn = Callable[[float], Awaitable[None]]
LogFn = Callable[[str, str], None]


def announce_enabled() -> bool:
    """Kill switch: JARVIS_DRAFT_ANNOUNCE=0 disables spoken draft announcements."""
    return os.environ.get(ANNOUNCE_ENV, "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def _speakable(text: str, limit: int) -> str:
    """Untrusted header text -> one clean short spoken fragment. Pure."""
    text = re.sub(r"<[^>]*>|[\x00-\x1f]", " ", str(text or ""))
    return " ".join(text.split())[:limit].strip()


def announce_line(record: dict) -> str:
    """The short spoken notice for one draft, in Jarvis's voice. Pure."""
    sender = _speakable(record.get("sender"), 60) or "someone"
    subject = re.sub(
        r"^(re:\s*)+", "", _speakable(record.get("subject"), 90), flags=re.I
    )
    about = f" about {subject}" if subject else ""
    return (
        f"Sir, I've drafted a reply to {sender}{about}. "
        "What would you like me to change, or shall I send it as is?"
    )


def _dnd_idle() -> bool:
    """Quiet hours apply only after 30+ minutes without input (dnd.py)."""
    import dnd

    return dnd.idle_long_enough()


def _default_presence() -> dict:
    """Input-based, no camera: "present" = keyboard/mouse input in the last
    JARVIS_IDLE_MINUTES (5), "absent" = idle, "unknown" = probe not running."""
    import input_idle

    return input_idle.status()


class DraftWatcher:
    """Polls pending drafts; announces them when Sir is at the desk."""

    def __init__(
        self,
        speak_fn: SpeakFn,
        *,
        presence_fn: PresenceFn | None = None,
        policy: ProactivePolicy | None = None,
        drafts_base: Path | None = None,
        poll_s: float = 20.0,
        backoff_cap_s: float = 300.0,
        confirm_gap_s: float = 2.0,
        now_fn: Callable[[], float] | None = None,
        sleep_fn: SleepFn | None = None,
        log_fn: LogFn | None = None,
    ) -> None:
        self._speak_fn = speak_fn
        self._presence_fn = presence_fn or _default_presence
        self._now = now_fn or time.time
        if log_fn is not None:
            self._log = log_fn
        else:
            from system import log_action

            self._log = log_action
        self._policy = policy or ProactivePolicy(
            now_fn=self._now, log_fn=self._log, idle_gate=_dnd_idle
        )
        self._base = drafts_base
        self._poll_s = poll_s
        self._backoff_cap_s = backoff_cap_s
        self._confirm_gap_s = confirm_gap_s
        self._sleep = sleep_fn or asyncio.sleep
        self._failures = 0
        self._next_at = 0.0
        self._in_tick = False
        self._task: asyncio.Task | None = None

    def _note(self, reason: str, detail: str = "") -> None:
        with contextlib.suppress(Exception):
            self._log("drafts", f"{reason} {detail}".strip())

    async def _status(self) -> str:
        """One presence reading in a thread. Any failure is "unknown"."""
        try:
            result = await asyncio.to_thread(self._presence_fn)
            status = (
                str(result.get("status", "unknown"))
                if isinstance(result, dict)
                else "unknown"
            )
        except Exception:
            return "unknown"
        return status if status in ("present", "absent") else "unknown"

    async def _confirmed_status(self) -> str:
        """ "present" only after two present readings ~2s apart."""
        first = await self._status()
        if first != "present":
            return first
        await self._sleep(self._confirm_gap_s)
        return await self._status()

    def _hold(self, now: float, why: str, *, grow: bool = True) -> None:
        """Retry later: 20s, doubling to the 5-minute cap."""
        delay = backoff_sleep(self._poll_s, self._failures, self._backoff_cap_s)
        if grow:
            self._failures += 1
        self._next_at = now + delay
        self._note("held", f"{why} retry_in={delay:.0f}s")

    async def _deliver(self, record: dict, now: float) -> str:
        """Gate + speak one draft. Returns speak | deduped | capped | held."""
        decision = self._policy.consider(
            announce_line(record),
            urgency="urgent" if record.get("priority") == "high" else "info",
            fingerprint_fp=f"draft:{record['id']}",
            now=now,
            batch=False,
        )
        if decision.action == "speak":
            for line in decision.texts:
                await self._speak_fn(line)
            draft_engine.set_status(str(record["id"]), "announced", self._base)
            self._note("announced", f"id={str(record['id'])[:20]}")
            return "speak"
        if decision.reason == REASON_DEDUPED:
            # Already voiced within the hour: settle it, never repeat it.
            draft_engine.set_status(str(record["id"]), "announced", self._base)
            return "deduped"
        return "capped" if decision.reason == REASON_CAPPED else "held"

    async def _tick(self, now: float) -> None:
        if self._in_tick or not announce_enabled():
            return
        self._in_tick = True
        try:
            pending = draft_engine.list_drafts("pending", self._base)
            if not pending:
                self._failures = 0
                self._next_at = 0.0
                return
            if now < self._next_at:
                return
            if self._policy.in_quiet(now):
                # Quiet hours: no speech. Hold until they end.
                self._hold(now, "quiet-hours", grow=False)
                return
            status = await self._confirmed_status()
            if status != "present":
                self._hold(now, status)
                return
            for record in pending:
                try:
                    outcome = await self._deliver(record, now)
                except Exception as exc:
                    self._note("speak-error", f"{type(exc).__name__}")
                    self._hold(now, "speak-error")
                    return
                if outcome in ("capped", "held"):
                    self._hold(now, outcome)
                    return
            self._failures = 0
            self._next_at = 0.0
        finally:
            self._in_tick = False

    async def _loop(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                await self._tick(self._now())
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
