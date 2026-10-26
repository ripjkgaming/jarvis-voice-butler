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
        """Read the explicitly selected foreground window once using local OCR.
        Requires a fresh explicit screen request and approved text disclosure.
        Never captures the full desktop, uploads images, or performs fixes.

        Args:
            question: What Sir wants to know about his screen.
        """
        _guard()
        from screen_understanding import ScreenError, local_owner_authorized

        if not local_owner_authorized():
            raise ToolError("Screen reading requires the local owner session.")
        session = getattr(context, "session", None)
        service = getattr(session, "jarvis_screen_service", None)
        # Trusted adapter sets the correlated turn; tool arguments cannot do so.
        turn_id = getattr(session, "jarvis_screen_turn_id", None)
        if service is None or not turn_id:
            raise ToolError(
                "Ask explicitly to read the selected window after screen privacy setup."
            )
        try:
            return await service.inspect(turn_id, question)
        except ScreenError as exc:
            raise ToolError(str(exc)) from exc

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
