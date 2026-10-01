"""Tool-alternative finder: "is there anything cheaper than X?"

Starts a background research project (existing engine, Claude Sonnet —
models stay as they are) comparing current pricing with cheaper or free
alternatives, so Sir gets a spoken summary plus a full report.
"""

from __future__ import annotations

import asyncio

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local


def build_alternatives_topic(tool: str, use_case: str = "", budget: str = "") -> str:
    """Full research brief for a cheaper-alternative hunt. Pure."""
    tool = " ".join((tool or "").split())[:120]
    use_case = " ".join((use_case or "").split())[:300]
    budget = " ".join((budget or "").split())[:120]
    lines = [
        f"Find cheaper or free alternatives to {tool}"
        + (f" for {use_case}" if use_case else "")
        + ".",
        f"First establish the current pricing of {tool}"
        + (f" for this use case ({use_case})" if use_case else "")
        + (f", keeping in mind a budget of {budget}" if budget else "")
        + ".",
        "Then list 4-8 cheaper or free alternatives (including lesser-known "
        "ones, e.g. budget API providers in the same space).",
        "Compare them in a table: price, free tier, quality, limits, and API "
        "availability.",
        "End with one clear recommendation: the best pick and why, plus "
        "which to avoid and why.",
    ]
    return " ".join(lines)


class AlternativesTools:
    """Cheaper-alternative research. Register via .tools."""

    @property
    def tools(self) -> list:
        return [self.find_cheaper_alternative]

    @function_tool()
    async def find_cheaper_alternative(
        self,
        context: RunContext,
        tool: str,
        use_case: str = "",
        budget: str = "",
    ) -> dict[str, str]:
        """Research cheaper or free alternatives to a paid tool.

        For "is there anything cheaper than Higgsfield", "find a free
        alternative to X for AI video generation". Runs in the background;
        the report lands in the Project Archive and you'll be told.

        Args:
            tool: The paid tool Sir wants to replace (e.g. "Higgsfield").
            use_case: What Sir uses it for (e.g. "AI video generation").
            budget: Max spend, if Sir named one (e.g. "under $20/month").
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        tool = " ".join((tool or "").split())[:120]
        if len(tool) < 2:
            raise ToolError("Which tool should I find alternatives to, Sir?")
        topic = build_alternatives_topic(tool, use_case, budget)

        def _start() -> None:
            import projects

            projects.start_research(topic)

        try:
            await asyncio.to_thread(_start)
        except Exception as exc:
            raise ToolError("The research engine isn't reachable right now.") from exc
        log_action("alternatives", f"{tool} use={use_case[:60]}")
        return {
            "say": f"I'm researching cheaper alternatives to {tool}, Sir. "
            "I'll let you know what I find."
        }
