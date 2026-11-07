"""One compact voice tool for Always Aware (docs/always-aware/PLAN.md §6).

``awareness(action, arg)`` keeps the agent's tool list small (AGENTS.md).
All logic lives in ``aware.control``; the fast regex route in agent.py
calls the same handler without the model.
"""

from __future__ import annotations

import asyncio

from jarvis_rt import RunContext, ToolError, function_tool
from system import LocalSystemError, log_action, require_local


class AwareTools:
    @property
    def tools(self) -> list:
        return [self.awareness]

    @function_tool()
    async def awareness(
        self, context: RunContext, action: str, arg: str = ""
    ) -> dict[str, str]:
        """Always Aware: Jarvis's low-rate glance at Sir's non-HUD monitors.

        action is one of: on, off, pause (arg "for an hour" / "until 5"),
        resume, status, seen ("what did you see"), at (arg a time: "what was
        I doing at 3pm"), shadow_on, shadow_off, mute / unmute (arg the kind
        or subject: "stop offering code fixes"), spend_limit (arg "£50"),
        spend_limit_off, paid / sent (arg "this week" / "today": "what have
        you paid for"), cancel ("stop", "I've got it" during a takeover),
        undo (arg a count), show ("show me" the last change), tried ("what
        did you try"), answer (arg yes / no / never: Sir's reply to an offer
        Jarvis just made; for a worksheet offer arg is complete / hint /
        leave / never), carry_on, leave_there ("leave it there" after a
        web takeover). Say the returned line as is.

        Args:
            action: One of the actions above.
            arg: Duration, time, kind, amount or period when the action needs one.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        import phone_session

        if phone_session.guest_required():
            raise ToolError("Always Aware is owner-only, Sir.")
        from aware import control

        if action.strip().lower() not in control.ACTIONS:
            raise ToolError(
                "Unknown awareness action. Use one of: " + ", ".join(control.ACTIONS)
            )
        say = await asyncio.to_thread(control.handle, action, arg)
        log_action("aware", f"{action} {arg}".strip()[:80])
        return {"say": say or "I have nothing for that time, Sir."}
