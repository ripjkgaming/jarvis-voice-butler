"""Voice tool: let the agent notify Sir outside the call.

A single `notify` tool so the model can send Sir something worth
interrupting for (or just worth seeing): it is spoken aloud when Sir is
at the laptop and a desktop notification otherwise. Routing, speech
gating, quiet hours and dedupe all live in notify.send; this is a thin
voice-shaped wrapper around it.
"""

from __future__ import annotations

from livekit.agents import RunContext, function_tool


class NotifyTools:
    """One voice tool: notify Sir (spoken if present, toast otherwise)."""

    @property
    def tools(self) -> list:
        return [self.notify]

    @function_tool()
    async def notify(
        self, context: RunContext, message: str, urgency: str = "info"
    ) -> dict[str, str]:
        """Send Sir a notification: spoken aloud if he is at the laptop,
        a desktop notification otherwise.

        Use this when Sir should see or hear something outside the call
        (a result is ready, something needs his attention). Do not use it
        for ordinary in-conversation replies.

        Args:
            message: The short text Sir should see or hear.
            urgency: "info" or "urgent" (anything else behaves as "info").
        """
        try:
            import notify

            level = urgency if urgency in ("info", "urgent") else "info"
            result = notify.send(
                message, kind="assistant", source="agent", urgency=level
            )
            route = result.get("route") if isinstance(result, dict) else None
            if route in ("announce", "notification", "deduped"):
                return {"say": "Notified, Sir."}
            if route == "empty":
                return {"say": "There was nothing to notify, Sir."}
            return {"say": f"Notify {route}, Sir."}
        except Exception:
            return {"say": "I could not send that notification, Sir."}
