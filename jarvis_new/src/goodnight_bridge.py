"""Reserved bridge wiring: authenticated status/cancel, no HTTP arming route.

Coordinator binds HOST only after authoritative host adapters are supplied.
Direct goodnight intent must travel via the trusted owner ingress adapter,
separate from generic tool/chat/body dispatch. No default host or background job.
"""

import ipaddress

HOST = None


def handle(handler, method, parsed):
    import phone_session
    import watch_mode

    try:
        allowed = (
            bool(handler.token)
            and handler._authorized()
            and ipaddress.ip_address(handler.client_address[0]).is_loopback
            and not watch_mode.guest_required()
            and not phone_session.guest_required()
        )
    except Exception:
        allowed = False
    if not allowed:
        handler._send(403, {"ok": False, "error": "owner authorization required"})
        return
    operation = {"/goodnight/status": "status", "/goodnight/cancel": "cancel"}.get(
        parsed.path
    )
    if (method, operation) not in {("GET", "status"), ("POST", "cancel")}:
        handler._send(404, {"ok": False, "error": "no arming endpoint"})
        return
    from goodnight_host import control

    result = HOST.control(operation) if HOST is not None else control(operation)
    handler._send(200 if result["ok"] else 503, result)
