"""Listen for "hey Jarvis" without ever competing with it.

Subscribes to ``wake-events.sock`` (wake / call_ended). Falls back to polling
``wake.sock`` with ``{"status": true}`` so chess never stays deaf. This module
only ever sends *status* queries; it never sends a mute (a test asserts it).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

GRACE_S = 1.5
POLL_S = 2.0
DEAF_LIMIT_S = 600.0


def _home() -> Path:
    h = os.environ.get("JARVIS_HOME", "").strip()
    return Path(h) if h else Path.home() / ".jarvis"


def events_path() -> Path:
    return _home() / "wake-events.sock"


def wake_sock_path() -> Path:
    return _home() / "wake.sock"


async def query_status(path: Path | None = None, timeout: float = 0.8) -> dict | None:
    """Ask the wake client for ``{"status": true}``. None if unreachable."""
    p = path or wake_sock_path()
    try:
        r, w = await asyncio.wait_for(asyncio.open_unix_connection(str(p)), timeout)
    except (OSError, asyncio.TimeoutError):
        return None
    try:
        w.write(json.dumps({"status": True}).encode())
        await w.drain()
        raw = await asyncio.wait_for(r.read(4096), timeout)
        return json.loads(raw.decode())
    except (OSError, asyncio.TimeoutError, ValueError):
        return None
    finally:
        w.close()
        with contextlib.suppress(Exception):
            await w.wait_closed()


class WakeWatcher:
    def __init__(
        self,
        on_wake: Callable[[], None],
        on_resume: Callable[[], None],
        *,
        events: Path | None = None,
        status_fn: Callable[[], Awaitable[dict | None]] | None = None,
        grace: float = GRACE_S,
        poll_s: float = POLL_S,
        clock: Callable[[], float] = time.monotonic,
        on_deaf_limit: Callable[[], None] | None = None,
        deaf_limit: float = DEAF_LIMIT_S,
        settle_s: float = 3.0,
    ):
        self.on_wake = on_wake
        self.on_resume = on_resume
        self.on_deaf_limit = on_deaf_limit
        self._events = events
        self._status = status_fn or query_status
        self.grace = grace
        self.poll_s = poll_s
        self.deaf_limit = deaf_limit
        self.settle_s = settle_s
        self._clock = clock
        self.paused = False
        self._paused_at = 0.0
        self._resume_task: asyncio.Task | None = None
        self._tasks: list[asyncio.Task] = []
        self.connected = False
        self._deaf_fired = False

    def start(self) -> None:
        self._tasks = [
            asyncio.ensure_future(self._subscribe()),
            asyncio.ensure_future(self._poll()),
        ]

    async def stop(self) -> None:
        for t in self._tasks + ([self._resume_task] if self._resume_task else []):
            if t:
                t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ---------------------------------------------------------- transitions
    def wake(self) -> None:
        """Wake fired (event socket or transcript backstop)."""
        if self._resume_task and not self._resume_task.done():
            self._resume_task.cancel()
        self._deaf_fired = False
        if not self.paused:
            self.paused = True
            self._paused_at = self._clock()
            self.on_wake()
        else:
            self._paused_at = self._clock()

    def call_ended(self) -> None:
        if not self.paused:
            return
        if self._resume_task and not self._resume_task.done():
            return
        self._resume_task = asyncio.ensure_future(self._resume_after_grace())

    async def _resume_after_grace(self) -> None:
        await asyncio.sleep(self.grace)
        self.resume_now()

    def resume_now(self) -> None:
        if self.paused:
            self.paused = False
            self.on_resume()

    # -------------------------------------------------------------- sources
    async def _subscribe(self) -> None:
        backoff = 0.5
        while True:
            path = self._events or events_path()
            try:
                r, w = await asyncio.open_unix_connection(str(path))
            except OSError:
                self.connected = False
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 5.0)
                continue
            self.connected = True
            backoff = 0.5
            try:
                while True:
                    line = await r.readline()
                    if not line:
                        break
                    try:
                        ev = json.loads(line).get("event")
                    except ValueError:
                        continue
                    if ev == "wake":
                        self.wake()
                    elif ev == "call_ended":
                        self.call_ended()
            except (OSError, asyncio.IncompleteReadError):
                pass
            finally:
                self.connected = False
                w.close()

    async def _poll(self) -> None:
        idle_polls = 0
        while True:
            await asyncio.sleep(self.poll_s)
            if not self.paused:
                idle_polls = 0
                continue
            waited = self._clock() - self._paused_at
            if waited >= self.deaf_limit and not self._deaf_fired:
                self._deaf_fired = True
                if self.on_deaf_limit:
                    self.on_deaf_limit()
            st = await self._status()
            in_call = bool(st and st.get("in_call"))
            if not in_call and waited >= self.settle_s:
                idle_polls += 1
                if idle_polls >= 2:
                    idle_polls = 0
                    self.call_ended()
            else:
                idle_polls = 0
