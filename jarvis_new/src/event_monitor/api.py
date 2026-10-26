"""Owner-scoped basic API. Host derives identity/auth; never use body authority."""


class MonitorAPI:
    def __init__(self, store, *, owner="owner", authorize=None, clock=None):
        import time

        self.store, self.owner = store, owner
        self.authorize = authorize or local_owner_guard
        self.clock = clock or time.time

    def execute(self, operation, body=None):
        body = body or {}
        ticket = self.authorize()
        if operation == "create":
            required = {
                "source",
                "event",
                "filters",
                "expires",
                "outputs",
                "session_scope",
                "request_id",
                "budget",
                "ttl",
            }
            if set(body) != required:
                raise ValueError(
                    "Explicit definition, output, expiry, request ID, budget and TTL required"
                )
            self.authorize(ticket)
            result = {
                "id": self.store.create(owner=self.owner, now=self.clock(), **body)
            }
        elif operation == "list" and not body:
            result = self.store.list(self.owner)
        elif operation == "status" and set(body) == {"id"}:
            result = self.store.status(body["id"], self.owner)
        elif operation == "stop" and set(body) == {"id"}:
            self.authorize(ticket)
            self.store.stop(body["id"], self.owner, self.clock())
            result = {"id": body["id"], "state": "cancelled"}
        else:
            raise ValueError("Supported operations: create/list/status/stop")
        self.authorize(ticket)
        return result


def local_owner_guard(ticket=None):
    import build_monitor
    import phone_session
    from system import require_local

    require_local()
    if not build_monitor.owner_allowed("127.0.0.1"):
        raise PermissionError("Local owner unavailable; guest/private boundary applies")
    if ticket is None:
        ticket = phone_session.approval_ticket()
    phone_session.check_approval_effect(ticket, current=True)
    return ticket
