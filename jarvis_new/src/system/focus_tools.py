"""Voice tools for Focus Mode: lock on, status, end, review my work.

Sir says "lock on to this" / "focus on this tab for 45 minutes": Jarvis
locks the current window or Brave tab, nudges Sir when he drifts, and
summarizes at the end. Register via .tools on the SystemAgent.
"""

from __future__ import annotations

import asyncio

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local


def _focus():
    import focus

    return focus


class FocusTools:
    """Focus mode + drift tracking + work review. Main-agent tools."""

    @property
    def tools(self) -> list:
        return [
            self.lock_on,
            self.focus_status,
            self.end_focus,
            self.review_my_work,
        ]

    @function_tool()
    async def lock_on(
        self,
        context: RunContext,
        kind: str = "auto",
        minutes: float = 0,
        label: str = "",
    ) -> dict[str, str]:
        """Lock onto Sir's current work and watch it ("lock on", "lock on
        to this", "focus mode for 30 minutes", "watch this tab", "focus
        on this for an hour").

        Captures the active window — or the active Brave tab when Sir is
        in Brave — runs a focus timer, and speaks up when Sir drifts to
        something else. Say "lock on" with no time for an open-ended
        watch.

        Args:
            kind: "window", "tab", or "auto" (tab when Brave is active).
            minutes: Timer length; 0 = open-ended, no timer.
            label: What Sir calls it ("thesis", "spanish homework").
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        kind = (kind or "auto").strip().lower()
        if kind not in ("auto", "window", "tab"):
            raise ToolError("Lock onto a window or a tab, Sir?")
        try:
            mins = float(minutes or 0)
        except (TypeError, ValueError):
            raise ToolError("How many minutes should I watch it for, Sir?") from None
        if mins < 0 or mins > 480:
            raise ToolError("Between one minute and eight hours, Sir.")
        got = await asyncio.to_thread(
            _focus().get_tracker().lock, kind, mins, (label or "").strip()
        )
        if not got.get("ok"):
            raise ToolError(got.get("say") or "I couldn't lock on, Sir.")
        log_action("focus", f"lock kind={kind} mins={mins}")
        return {"say": got["say"]}

    @function_tool()
    async def focus_status(self, context: RunContext) -> dict[str, str]:
        """How is focus going ("how am I doing", "focus status", "am I
        still locked on").

        Reports time on the locked work, drift count, and on-task
        percentage. Idle when no session runs.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        say = await asyncio.to_thread(_focus().spoken_status)
        return {"say": say}

    @function_tool()
    async def end_focus(self, context: RunContext) -> dict[str, str]:
        """End focus mode ("stop focusing", "end focus", "I'm done
        focusing", "cancel the timer").

        Speaks the session summary (time, drifts, on-task %) and files
        it in the focus history.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        got = await asyncio.to_thread(_focus().get_tracker().end, "stopped")
        log_action("focus", "end")
        return {"say": got.get("say") or "Focus over, Sir."}

    @function_tool()
    async def review_my_work(
        self, context: RunContext, question: str = ""
    ) -> dict[str, str]:
        """Look at the locked work and comment ("what do you see", "check
        my work", "does this look right", "review this").

        Screenshots the screen when the locked target is showing and
        gives brief, useful feedback. Needs the locked work in front;
        says so when it isn't.

        Args:
            question: Sir's question about the work, verbatim (empty =
                general feedback).
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        got = await asyncio.to_thread(
            _focus().get_tracker().look, (question or "").strip()
        )
        if not got.get("ok"):
            raise ToolError(got.get("say") or "I couldn't look just now, Sir.")
        log_action("focus", f"review q={(question or '')[:60]}")
        return {"say": got["say"]}
