"""Voice tools: look at the screen / through the camera (src/look.py)."""

from __future__ import annotations

import asyncio

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local


def _guard() -> None:
    try:
        require_local()
    except LocalSystemError as exc:
        raise ToolError(str(exc)) from exc


class LookTools:
    @property
    def tools(self) -> list:
        return [self.look_at_screen, self.look_through_camera]

    @function_tool()
    async def look_at_screen(
        self, context: RunContext, question: str = ""
    ) -> dict[str, str]:
        """Look at the window Sir is working in and answer about it: "what's
        wrong with this error", "what does this say", "is this right".

        Args:
            question: What Sir wants to know about his screen.
        """
        _guard()
        import look

        image = await asyncio.to_thread(look.capture_window)
        if image is None:
            raise ToolError("I couldn't capture the screen, Sir (spectacle missing?).")
        answer, warning = await asyncio.to_thread(
            look.ask, image, "image/png", question, "screen"
        )
        log_action("look", "screen")
        if not answer:
            raise ToolError(f"I couldn't make it out: {warning}.")
        return {"say": answer[:900]}

    @function_tool()
    async def look_through_camera(
        self, context: RunContext, question: str = ""
    ) -> dict[str, str]:
        """Look through the webcam at something Sir holds up: "what's this
        part", "what does this label say".

        Args:
            question: What Sir wants to know.
        """
        _guard()
        import look

        image = await asyncio.to_thread(look.camera_frame)
        if image is None:
            raise ToolError("The camera gave me nothing, Sir.")
        answer, warning = await asyncio.to_thread(
            look.ask, image, "image/jpeg", question, "camera"
        )
        log_action("look", "camera")
        if not answer:
            raise ToolError(f"I couldn't make it out: {warning}.")
        return {"say": answer[:900]}
