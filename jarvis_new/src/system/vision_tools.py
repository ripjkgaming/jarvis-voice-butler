"""Voice tools for Eyes (posture + appearance) and Hollow Hands (gestures).

Local-only like the rest of the system tools: the webcam lives on Sir's
own machine, so every tool refuses unless JARVIS_LOCAL=1.
"""

from __future__ import annotations

import asyncio

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local


def _need_local() -> None:
    try:
        require_local()
    except LocalSystemError as exc:
        raise ToolError(str(exc)) from exc


class VisionTools:
    """Webcam eyes + hand gestures. Register via .tools on the SystemAgent."""

    @property
    def tools(self) -> list:
        return [
            self.look_at_me,
            self.start_posture_watch,
            self.stop_posture_watch,
            self.posture_report,
            self.set_hand_gestures,
        ]

    @function_tool()
    async def look_at_me(
        self, context: RunContext, question: str = ""
    ) -> dict[str, str]:
        """Look at Sir through the webcam and comment on appearance.

        For "how do I look", "check my outfit", "is my hair alright",
        "how's the lighting". Honest, kind, specific: outfit, grooming,
        lighting, framing — two to four sentences.

        Args:
            question: Sir's exact question, if he asked one (e.g. "does
                this tie match"). Empty for a general look-over.
        """
        _need_local()
        import eyes

        text = await asyncio.to_thread(eyes.describe_appearance, question or "")
        if text.startswith("I couldn't") or text.startswith("My eyes"):
            raise ToolError(text)
        log_action("vision", f"look q={question[:60]}")
        return {"say": text}

    @function_tool()
    async def start_posture_watch(self, context: RunContext) -> dict[str, str]:
        """Start watching Sir's posture (slouch + phone alerts).

        Calibrates first: tell Sir to sit up straight, then call this.
        While running, Jarvis calls out sustained slouching and
        phone-in-hand out of call, at most every few minutes.
        """
        _need_local()
        import eyes

        if eyes.is_running():
            return {"say": "Already watching your posture, Sir."}
        baseline = await asyncio.to_thread(eyes.calibrate)
        if baseline is None:
            raise ToolError(
                "I couldn't see you clearly, Sir. Sit up straight in "
                "front of the camera and try again."
            )
        if not eyes.start():
            raise ToolError("The posture watch wouldn't start.")
        log_action("vision", "posture_watch start")
        return {"say": "Posture watch on, Sir. I'll keep an eye out."}

    @function_tool()
    async def stop_posture_watch(self, context: RunContext) -> dict[str, str]:
        """Stop the posture watch (no more slouch or phone call-outs)."""
        _need_local()
        import eyes

        if not eyes.is_running():
            return {"say": "The posture watch wasn't running, Sir."}
        await asyncio.to_thread(eyes.stop)
        log_action("vision", "posture_watch stop")
        return {"say": "Posture watch off, Sir."}

    @function_tool()
    async def posture_report(self, context: RunContext) -> dict[str, str]:
        """How the posture watch is doing: alerts given, time monitored."""
        _need_local()
        import eyes

        st = await asyncio.to_thread(eyes.status)
        bits = (
            f"{st['slouch_alerts']} slouch call-outs, "
            f"{st['phone_alerts']} phone call-outs, "
            f"{st['minutes_monitored']} minutes watched"
        )
        state = "running" if st["running"] else "off"
        if not st["calibrated"]:
            return {
                "say": f"Posture watch is {state}, with no calibration yet. {bits}."
            }
        return {"say": f"Posture watch is {state}. So far: {bits}."}

    @function_tool()
    async def set_hand_gestures(self, context: RunContext, on: bool) -> dict[str, str]:
        """Turn experimental hand-gesture control on or off ("Hollow Hands").

        Palm toggles the mic, thumbs-up confirms, victory summons a call,
        a long fist stops gesture mode. Off unless Sir asks.

        Args:
            on: True to enable gestures, False to disable.
        """
        _need_local()
        import gestures

        if on:
            if gestures.is_running():
                return {"say": "Hand gestures are already on, Sir."}
            if not gestures.start():
                raise ToolError("Hand gestures wouldn't start.")
            log_action("vision", "gestures on")
            return {
                "say": "Hollow Hands on, Sir. Palm for the mic, thumbs-up "
                "to confirm, victory to summon me, a long fist to stop."
            }
        if not gestures.is_running():
            return {"say": "Hand gestures are already off, Sir."}
        await asyncio.to_thread(gestures.stop)
        log_action("vision", "gestures off")
        return {"say": "Hollow Hands off, Sir."}
