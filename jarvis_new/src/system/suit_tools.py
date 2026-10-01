"""Voice control of the simulated suit diagnostics UI; no hardware operations."""

from __future__ import annotations

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

import suit_diagnostics
from system import LocalSystemError, log_action, require_local
from system.core import _guarded_thread_action, _source


class SuitTools:
    @property
    def tools(self) -> list:
        return [self.set_suit_diagnostics]

    @function_tool()
    async def set_suit_diagnostics(
        self, context: RunContext, on: bool
    ) -> dict[str, str]:
        """Show or dismiss the SIMULATED suit diagnostics panel on the desktop.

        Use only for an explicit request such as "bring up the suit diagnostics"
        or "dismiss/close/hide suit diagnostics". This is a visual simulation,
        not real suit telemetry or hardware control. Never launch an app or site
        for this panel, and do not call this for negated requests or questions.

        Args:
            on: True to show the simulated panel, False to dismiss it.
        """
        try:
            require_local()
            if type(on) is not bool:
                raise ValueError("on must be a boolean")
        except (LocalSystemError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

        def work() -> tuple[bool, str]:
            result = suit_diagnostics.request_visibility(on)
            if not (result or {}).get("ok"):
                return False, "The suit diagnostics panel didn't respond."
            if result.get("superseded"):
                return True, result["say"]
            log_action("suit", "show simulation" if on else "dismiss simulation")
            return True, suit_diagnostics.reply_for(on)

        return await _guarded_thread_action(
            "set_suit_diagnostics",
            {"open": on},
            _source(context),
            work,
            suit_diagnostics.reply_for(on),
        )
