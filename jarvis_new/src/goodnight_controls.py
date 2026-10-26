"""Status/cancel controls only; deliberately no model-callable arm function."""


def owner_control(host, operation, *, owner_allowed):
    if owner_allowed is not True:
        return {"ok": False, "say": "Owner goodnight controls unavailable."}
    if host is None:
        return {
            "ok": False,
            "say": "Goodnight lock is unavailable pending host coverage.",
        }
    if operation == "cancel":
        cancelled = host.controller.cancel()
        if not cancelled and host.controller.status()["state"] in {
            "locking",
            "locked",
            "lock-unverified",
        }:
            return {
                "ok": False,
                "say": "The lock request already started; cancellation cannot undo it.",
            }
        host.idle.close()
        return {
            "ok": True,
            "say": "Goodnight lock cancelled."
            if cancelled
            else "No goodnight lock request is pending.",
        }
    if operation == "status":
        state = host.controller.status()
        return {
            "ok": True,
            "state": state["state"],
            "reason": state["reason"],
            "say": f"Goodnight lock is {state['state']}: {state['reason']}.",
        }
    return {"ok": False, "say": "Unsupported goodnight control."}
