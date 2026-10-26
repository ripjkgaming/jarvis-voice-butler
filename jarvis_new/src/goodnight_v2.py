"""Explicit owner goodnight: ephemeral, ten-minute, fail-closed lock policy.

No startup hooks, subprocesses, persistence, input contents or device access.
The host owns authoritative probes, authorization and verified session lock.
"""

from __future__ import annotations

import math
import re
import threading
import time
import uuid
from dataclasses import dataclass

IDLE_S = 600.0
EXPIRY_S = 3600.0
MAX_POLL_GAP_S = 30.0


def elapsed_clock():
    """Linux boottime includes suspend, so expiry cannot stop while asleep."""
    return time.clock_gettime(time.CLOCK_BOOTTIME)


@dataclass(frozen=True)
class Evidence:
    idle_seconds: float | None
    tasks_clear: bool | None
    conversation_clear: bool | None
    owner_allowed: bool
    session: str | None


class Controller:
    def __init__(self, *, probe, lock, clock=elapsed_clock, finalizer=None):
        self.probe, self.lock, self.clock = probe, lock, clock
        self.finalizer = finalizer
        self._mutex = threading.RLock()
        self._token = None
        self._state = "disarmed"
        self._reason = "not requested"
        self._session = None
        self._armed_at = self._quiet_since = self._last_poll = 0.0
        self._expires_at = 0.0

    def status(self):
        with self._mutex:
            return {
                "state": self._state,
                "reason": self._reason,
                "token": self._token,
                "armed_at": self._armed_at,
                "expires_at": self._expires_at,
                "minimum_idle_s": IDLE_S,
            }

    def arm(self, *, owner, direct_intent, session):
        with self._mutex:
            if self._state == "locking":
                return {"ok": False, "reason": "A lock request has already started."}
            if owner is not True or direct_intent is not True or not session:
                return {
                    "ok": False,
                    "reason": "explicit authenticated owner intent required",
                }
            now = self.clock()
            if self._token and now >= self._expires_at:
                self.cancel("expired")
            if self._token and self._session == session:
                self.activity()
                return dict(ok=True, **self.status())
            self._token = uuid.uuid4().hex
            self._session = session
            self._armed_at = self._quiet_since = self._last_poll = now
            self._expires_at = now + EXPIRY_S
            self._state, self._reason = (
                "armed",
                "waiting for ten continuous idle minutes",
            )
            return dict(ok=True, **self.status())

    def activity(self):
        with self._mutex:
            self._quiet_since = self.clock()

    def cancel(self, reason="cancelled"):
        with self._mutex:
            if self._state == "locking":
                return False  # request already committed; cannot undo it
            had = self._token is not None
            self._token = None
            self._state = (
                reason
                if reason in {"expired", "locked", "lock-unverified"}
                else "cancelled"
            )
            self._reason = reason
            return had

    def _sample(self):
        try:
            value = self.probe()
            return value if isinstance(value, Evidence) else None
        except Exception:
            return None

    def _ready(self, sample, now):
        if sample is None:
            self._quiet_since = now
            self._reason = "authoritative evidence unavailable"
            return False
        if not sample.session or sample.session != self._session:
            self.cancel("session changed or unavailable")
            return False
        idle = sample.idle_seconds
        if (
            type(idle) not in {float, int}
            or not math.isfinite(idle)
            or idle < 0
            or sample.conversation_clear is not True
            or sample.owner_allowed is not True
        ):
            self._quiet_since = now
            self._reason = "input, call, conversation or owner state unknown/active"
            return False
        if idle < IDLE_S:
            self._quiet_since = max(self._quiet_since, now - idle)
            self._reason = "keyboard/mouse activity; waiting for confirmed idle"
            return False
        if now - self._armed_at < IDLE_S or now - self._quiet_since < IDLE_S:
            self._reason = "waiting for ten continuous idle minutes"
            return False
        if sample.tasks_clear is not True:
            self._reason = "coding/build/owned work active or unknown"
            return False
        return True

    def tick(self, token=None):
        with self._mutex:
            current = self._token
            if current is None or (token is not None and token != current):
                return self.status()
            now = self.clock()
            if now >= self._expires_at:
                self.cancel("expired")
                return self.status()
            if now < self._last_poll or now - self._last_poll > MAX_POLL_GAP_S:
                self._quiet_since = now  # wake, scheduling gap, clock discontinuity
            self._last_poll = now
        first = self._sample()  # cancellation must remain responsive to slow probes
        with self._mutex:
            if self._token != current:
                return self.status()
            if not self._ready(first, self.clock()):
                return self.status()
        # Fresh comprehensive second snapshot, not a cached first verdict.
        final = self._sample()

        def commit(sample, request):
            with self._mutex:
                now = self.clock()
                if self._token != current:
                    return None
                if now >= self._expires_at:
                    self.cancel("expired")
                    return None
                if now - self._last_poll > MAX_POLL_GAP_S:
                    self._quiet_since = now
                if not self._ready(sample, now):
                    return None
                # Cancellation linearizes before this commit or observes that
                # the fixed request already started; no replay after uncertainty.
                self._token = None
                self._state, self._reason = "locking", "lock request committed"
            try:
                receipt = request()
            except Exception:
                receipt = False
            with self._mutex:
                self._state = "locked" if receipt is True else "lock-unverified"
                self._reason = self._state
            return receipt

        if self.finalizer is not None:
            try:
                self.finalizer(self._session, commit)
            except Exception:
                with self._mutex:
                    self._reason = "final admission fence unavailable; postponed"
        else:
            commit(final, lambda: self.lock(self._session))
        return self.status()


class IntentGate:
    """Host-ingress gate; NEVER pass model/tool/chat-history text as direct=True.

    Exact utterances preserve quotes/newlines, avoiding punctuation stripping
    that converts quoted or embedded text into a command. No transcript storage.
    """

    def __init__(self, controller):
        self.controller = controller

    def input(self, text, *, owner, direct, session):
        if owner is not True or direct is not True or not isinstance(text, str):
            return None
        self.controller.activity()
        normalized = text.lower()
        if re.fullmatch(
            r"(?:hey jarvis[, ]+)?good ?night(?:[, ]+jarvis)?[.!]?", normalized
        ):
            result = self.controller.arm(
                owner=True, direct_intent=True, session=session
            )
            return (
                "Goodnight, Sir. I'll lock after ten idle minutes once registered work finishes. The request expires in one hour."
                if result["ok"]
                else result["reason"]
            )
        if normalized in {"cancel goodnight", "cancel goodnight schedule"}:
            cancelled = self.controller.cancel()
            if cancelled:
                return "Goodnight lock cancelled."
            if self.controller.status()["state"] in {
                "locking",
                "locked",
                "lock-unverified",
            }:
                return "The lock request already started; cancellation cannot undo it."
            return "No goodnight lock request is pending."
        if normalized in {"goodnight status", "goodnight schedule status"}:
            status = self.controller.status()
            return f"Goodnight lock is {status['state']}: {status['reason']}."
        return None
