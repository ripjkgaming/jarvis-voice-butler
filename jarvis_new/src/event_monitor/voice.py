"""Basic voice tools. Registration is staged for the release coordinator.

SDK decorator contract: https://docs.livekit.io/agents/build/tools/
Creation requires an exact current owner command with a JSON definition, so
model guesses cannot authorize outputs, scope or endless watching.
"""

import json

from livekit.agents import RunContext, function_tool
from system.build_monitor_tools import BuildMonitorTools

from .core import canonical


class MonitorTools:
    def __init__(self, api):
        self.api = api

    @property
    def tools(self):
        return [
            self.monitor_create,
            self.monitor_list,
            self.monitor_status,
            self.monitor_stop,
        ]

    @function_tool()
    async def monitor_create(self, context: RunContext, definition_json: str) -> dict:
        """Create a finite public monitor with explicit outputs. Ask for a current
        'monitor {JSON definition}' command specifying source, event, numeric
        filters, expires UTC epoch, session_scope, outputs, request_id, budget,
        ttl. Outputs status/session/activate/desktop-alert only; no calls or external sends.
        Never infer omitted choices or create an endless watch.
        """
        source = BuildMonitorTools._input(context)
        if not source or not source[1].strip().lower().startswith("monitor "):
            return {"status": "needs_explicit_definition"}
        try:
            current = json.loads(source[1].strip()[8:])
            definition = json.loads(definition_json)
            if canonical(current) != canonical(definition):
                raise ValueError("Current command differs")
            result = self.api.execute("create", definition)
            return {"status": "created", **result}
        except (ValueError, PermissionError):
            return {"status": "denied_or_incomplete_definition"}

    @function_tool()
    async def monitor_list(self, context: RunContext) -> dict:
        """List monitors belonging to the authenticated local owner."""
        return {"monitors": self.api.execute("list")}

    @function_tool()
    async def monitor_status(self, context: RunContext, monitor_id: str) -> dict:
        """Get state, recent completed event summaries, model/fallback status and alert receipts of one exact owner monitor ID."""
        return self.api.execute("status", {"id": monitor_id})

    @function_tool()
    async def monitor_stop(self, context: RunContext, monitor_id: str) -> dict:
        """Stop one exact owner monitor; cancels pending events and activation."""
        source = BuildMonitorTools._input(context)
        if not source or source[1].strip() != f"stop monitor {monitor_id}":
            return {"status": "needs_explicit_stop"}
        return self.api.execute("stop", {"id": monitor_id})
