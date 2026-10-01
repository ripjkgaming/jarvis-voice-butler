"""School mode voice tools: switch the mode, confirm a loud action."""

from __future__ import annotations

import asyncio

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

import school
from system.projects_tools import _bridge_call


def loud_guard(tool: str, args: dict | None = None) -> None:
    """Raise the confirm-first ToolError for a loud action in school mode."""
    if school.is_school() and school.is_loud(tool, args) and not school.loud_allowed():
        school.mark_asked()  # keeps the call open for Sir's answer
        raise ToolError(school.LOUD_REFUSAL)


class SchoolTools:
    """Main-agent tools for school mode."""

    @property
    def tools(self) -> list:
        return [self.set_school_mode, self.confirm_loud_action]

    @function_tool()
    async def set_school_mode(self, context: RunContext, on: bool) -> dict[str, str]:
        """Enter or exit school mode ("open/enter school mode" / "exit school mode").

        "School mode" is always this Jarvis setting — never search the web
        or open a site for it. Call it ONCE per request; never follow an
        enter with an exit in the same turn.

        School mode shrinks the HUD to a quiet taskbar strip, answers
        quietly, needs a clear "hey Jarvis", and ends each call shortly
        after the answer.

        Args:
            on: True to enter school mode, False to go back to normal.
        """
        mode = school.SCHOOL if on else school.NORMAL
        refusal = school.flip_refusal(mode)
        if refusal == "already":
            # Usually the instant path already switched it: just confirm.
            return {"say": "School mode, Sir." if on else "Back to normal, Sir."}
        if refusal:
            raise ToolError(refusal)
        got = await asyncio.to_thread(_bridge_call, "POST", "/mode", {"mode": mode})
        if not (got or {}).get("ok"):
            raise ToolError("The mode switch didn't respond.")
        return {"say": "School mode, Sir." if on else "Back to normal, Sir."}

    @function_tool()
    async def confirm_loud_action(self, context: RunContext) -> dict[str, str]:
        """Sir confirmed a loud action in school mode (music, games, volume up).

        Call ONLY after Sir clearly says yes to your "are you sure" question,
        then retry the action once.
        """
        school.confirm_loud()
        return {"say": "", "next": "retry the action now"}
