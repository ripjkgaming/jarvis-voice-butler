# ruff: noqa: RUF001
# Portions derived from livekit-agents (Copyright 2023 LiveKit, Inc.),
# licensed under the Apache License, Version 2.0.
"""End-call tool: the model says goodbye, then the session closes."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from .events import CloseReason
from .tools import RunContext, function_tool

log = logging.getLogger("jarvis_rt.end_call")

END_CALL_DESCRIPTION = """
Ends the current call and disconnects immediately.

Call when:
- The user clearly indicates they are done (e.g., “that’s all, bye”).

Do not call when:
- The user asks to pause, hold, or transfer.
- Intent is unclear.

This is the final action the agent can take.
Once called, no further interaction is possible with the user.
Don't generate any other text or response when the tool is called.
"""

FAREWELL_WAIT_S = 5.0
#: Hard cap on how long a goodbye may hold the session open.
FAREWELL_MAX_S = 25.0
#: Extra time after the last audio sample is due, for client-side buffering.
PLAYOUT_TAIL_S = 0.6
_POLL_S = 0.1


class _PlayoutClock:
    """Wraps ``session.output.audio`` to learn when the queued audio ends.

    Realtime backends stream audio faster than it plays, so a speech handle is
    "done" (turn complete) well before the listener has heard the end of it.
    Closing then cuts the farewell off mid-sentence. This tracks the wall-clock
    moment the last pushed sample finishes playing.
    """

    def __init__(self, session: Any) -> None:
        self._output = getattr(session, "output", None)
        self._inner = getattr(self._output, "audio", None)
        self.seen = False
        self.until = 0.0
        if self._output is not None and self._inner is not None:
            self._output.audio = self._push

    def _push(self, pcm24k: bytes) -> None:
        now = asyncio.get_running_loop().time()
        self.seen = True
        self.until = max(self.until, now) + len(pcm24k) / 2 / 24000
        self._inner(pcm24k)

    def restore(self) -> None:
        if self._output is not None and self._inner is not None:
            if getattr(self._output, "audio", None) == self._push:
                self._output.audio = self._inner


class EndCallTool:
    def __init__(
        self,
        *,
        extra_description: str = "",
        end_instructions: str | None = "say goodbye to the user",
        delete_room: bool = True,
        **_: Any,
    ) -> None:
        self._end_instructions = end_instructions
        self._delete_room = delete_room
        self._task: asyncio.Task | None = None
        self.tools = [
            function_tool(
                self._end_call,
                name="end_call",
                description=f"{END_CALL_DESCRIPTION}\n{extra_description}",
            )
        ]

    async def _end_call(self, ctx: RunContext) -> str | None:
        session = ctx.session
        created: asyncio.Future = asyncio.get_running_loop().create_future()

        def _on_created(ev: Any) -> None:
            if not created.done():
                created.set_result(ev.speech_handle)

        session.once("speech_created", _on_created)
        clock = _PlayoutClock(session)
        self._task = asyncio.get_running_loop().create_task(
            self._shutdown(session, created, clock, _on_created)
        )
        return self._end_instructions

    async def _shutdown(
        self,
        session: Any,
        created: asyncio.Future,
        clock: _PlayoutClock,
        on_created: Any,
    ) -> None:
        """Close once the farewell has been generated AND heard.

        The farewell may arrive as a new speech (speech_created) or as more
        audio on a speech that was still open when the tool ran; either counts
        as started. Then wait for the turn to complete and for the queued audio
        to finish playing.
        """
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        try:
            while True:
                await asyncio.sleep(_POLL_S)
                now = loop.time()
                if now - t0 > FAREWELL_MAX_S:
                    log.warning(
                        "farewell still playing after %.0fs, closing", FAREWELL_MAX_S
                    )
                    break
                started = created.done() or clock.seen
                if not started:
                    if now - t0 > FAREWELL_WAIT_S:
                        log.warning("farewell reply timed out, closing session")
                        break
                    continue
                speech = getattr(session, "_speech", None)
                if speech is not None and not speech.done():
                    continue
                if now >= clock.until + PLAYOUT_TAIL_S:
                    break
        except Exception:
            log.exception("waiting for farewell failed")
        finally:
            clock.restore()
            with contextlib.suppress(Exception):
                session.off("speech_created", on_created)
            with contextlib.suppress(Exception):
                await session.aclose(CloseReason.USER_INITIATED)
