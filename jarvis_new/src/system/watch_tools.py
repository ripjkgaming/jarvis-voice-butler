"""Voice controls for productive computer observation and the security guard."""

from __future__ import annotations

import asyncio

from jarvis_rt import RunContext, function_tool
from jarvis_rt import ToolError

import computer_watch_client
import watch_mode
from screen_understanding import local_owner_authorized
from system import LocalSystemError, log_action, require_local
from system.core import _guarded_thread_action, _source


def _require_owner() -> None:
    try:
        require_local()
    except LocalSystemError as exc:
        raise ToolError(str(exc)) from exc
    if not local_owner_authorized():
        raise ToolError("Computer watch requires the local owner session.")


class WatchTools:
    @property
    def tools(self) -> list:
        return [
            self.start_watch_mode,
            self.stop_watch_mode,
            self.watch_mode_status,
            self.watch_mode_briefing,
        ]

    @function_tool()
    async def start_watch_mode(self, context: RunContext) -> dict[str, str]:
        """Start Watch Mode for 'watch the computer', 'watch the laptop',
        'start watch mode' or 'I'm leaving': the fullscreen ASCII eye card and
        camera guard (src/watch_mode.py). Sir stops it with Ctrl+Alt+Shift+W.
        Never use for negated requests, stop requests or window questions.
        """
        _require_owner()

        def work() -> tuple[bool, str]:
            result = watch_mode.launch()
            if not result.get("ok"):
                return False, str(result.get("error") or "Watch mode did not start.")
            log_action("watch_mode", "launched")
            return True, "Watching the laptop, Sir."

        return await _guarded_thread_action(
            "watch_mode",
            {"action": "start"},
            _source(context),
            work,
            "Watching the laptop, Sir.",
        )

    @function_tool()
    async def stop_watch_mode(self, context: RunContext) -> dict[str, str]:
        """Stop productive Watch Mode for 'stop watching' or 'stop watch mode'.
        This does not stop the separate security guard or restore owner mode.
        """
        require_local()
        result = await asyncio.to_thread(computer_watch_client.request, "stop")
        if not result.get("ok", True):
            raise ToolError(str(result.get("error") or "Watch mode did not stop."))
        return {"say": "Stopped watching the computer, Sir."}

    @function_tool()
    async def watch_mode_status(self, context: RunContext) -> dict:
        """Report whether productive Watch Mode is active and what it observes."""
        _require_owner()
        result = await asyncio.to_thread(computer_watch_client.request, "status")
        if not result.get("ok"):
            raise ToolError(str(result.get("error") or "Watch status unavailable."))
        return result

    @function_tool()
    async def watch_mode_briefing(self, context: RunContext) -> dict[str, str]:
        """Explain meaningful changes and prepared next steps from Watch Mode."""
        _require_owner()
        result = await asyncio.to_thread(computer_watch_client.request, "briefing")
        if not result.get("ok"):
            raise ToolError(str(result.get("error") or "Watch briefing unavailable."))
        return {"say": str(result.get("say", ""))}

    @function_tool()
    async def start_guard_mode(self, context: RunContext) -> dict[str, str]:
        """Refuse legacy assistant requests to start the fullscreen guard.

        Compatibility stub only; not exposed as an assistant tool. Activation
        remains unavailable until the desktop exit controls are repaired.
        """
        raise ToolError(
            "Fullscreen security guard cannot be started by the assistant. "
            "Its desktop exit controls require repair before voice activation."
        )
