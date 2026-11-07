"""Owner status/cancel voice tools; host control client is trusted injection."""

from jarvis_rt import RunContext, function_tool


class GoodnightTools:
    def __init__(self, client=None):
        self.client = client

    @property
    def tools(self):
        return [self.goodnight_status, self.goodnight_cancel]

    def _control(self, operation):
        import phone_session
        import watch_mode
        from system import require_local

        try:
            require_local()
            if (
                watch_mode.guest_required()
                or phone_session.guest_required()
                or self.client is None
            ):
                raise ValueError("owner host unavailable")
            return self.client(operation)
        except Exception:
            return {
                "ok": False,
                "say": "Goodnight controls are unavailable pending host coverage.",
            }

    @function_tool()
    async def goodnight_status(self, context: RunContext) -> dict:
        """Report current goodnight lock status. Does not arm or extend it."""
        return self._control("status")

    @function_tool()
    async def goodnight_cancel(self, context: RunContext) -> dict:
        """Cancel the owner's current goodnight lock request."""
        return self._control("cancel")
