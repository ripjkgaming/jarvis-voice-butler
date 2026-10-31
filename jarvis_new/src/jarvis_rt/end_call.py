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
        self._task = asyncio.get_running_loop().create_task(
            self._shutdown(session, created)
        )
        return self._end_instructions

    async def _shutdown(self, session: Any, created: asyncio.Future) -> None:
        try:
            handle = await asyncio.wait_for(created, FAREWELL_WAIT_S)
            await handle.wait_for_playout()
        except asyncio.TimeoutError:
            log.warning("farewell reply timed out, closing session")
        except Exception:
            log.exception("waiting for farewell failed")
        finally:
            session.off("speech_created", lambda _: None)
            with contextlib.suppress(Exception):
                await session.aclose(CloseReason.USER_INITIATED)
