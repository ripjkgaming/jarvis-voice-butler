"""Voice tools: look at the screen / through the camera (src/look.py)."""

from __future__ import annotations

import asyncio
import os
import uuid

from jarvis_rt import RunContext, ToolError, function_tool
from system import LocalSystemError, log_action, require_local


def _guard() -> None:
    try:
        require_local()
    except LocalSystemError as exc:
        raise ToolError(str(exc)) from exc


def _window_jpeg() -> bytes | None:
    """Selected window as a bounded JPEG for Gemini's video input."""
    import io

    from PIL import Image

    import look

    png = look.capture_window()
    if not png:
        return None
    try:
        with Image.open(io.BytesIO(png)) as image:
            image = image.convert("RGB")
            image.thumbnail((1280, 1280))
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=85)
            return out.getvalue()
    except Exception as exc:  # truncated/corrupt capture: never leak PIL errors
        raise ToolError(
            "The selected window capture was unreadable. Ask again."
        ) from exc


async def _send_window_frames(session) -> dict[str, str]:
    """Owner pre-approved: stream the window into the live Gemini session."""
    import time

    backend = getattr(session, "backend", None)
    push = getattr(backend, "push_video", None)
    if push is None:
        raise ToolError("Screen video input is unavailable in this session.")
    # Sessions that turn video input off (event ledger, screen holder) must not get frames.
    if not getattr(getattr(session, "input", None), "video_enabled", True):
        raise ToolError("Screen video input is disabled for this session.")
    frame = await asyncio.to_thread(_window_jpeg)
    if frame is None:
        raise ToolError("I couldn't capture the selected window.")
    # Two frames so the model has the picture before it answers.
    push(frame, int(time.time() * 1000))
    await asyncio.sleep(1.0)
    push(frame, int(time.time() * 1000))
    await asyncio.sleep(0.5)
    return {
        "status": "complete",
        "note": "The window frame is now in your video input. Look at it and answer Sir's question in your own words. Do not read text out verbatim.",
    }


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
        if os.environ.get("JARVIS_AUTO_APPROVE", "1") != "0":
            return await _send_window_frames(session)
        service = getattr(session, "jarvis_screen_service", None)
        # Trusted adapter sets the correlated turn; tool arguments cannot do so.
        turn_id = getattr(session, "jarvis_screen_turn_id", None)
        if (
            service is not None
            and not turn_id
            and os.environ.get("JARVIS_AUTO_APPROVE", "1") != "0"
        ):
            # Owner pre-approved: self-enroll a fresh turn instead of demanding a phrase.
            turn_id = uuid.uuid4().hex
            try:
                await service.enroll(turn_id, "read my screen")
            except ScreenError as exc:
                raise ToolError(str(exc)) from exc
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
