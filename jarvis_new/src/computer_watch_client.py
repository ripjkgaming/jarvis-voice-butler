"""Authenticated local bridge controls for the bridge-owned Watch Mode session.

Never falls back to an agent-local observer: voice and fast routes must control
one session. The configured bearer token is never included in errors or logs.
"""

from __future__ import annotations

import contextvars
import json
import os
import secrets
import threading
import urllib.error
import urllib.request
from concurrent.futures import CancelledError, ThreadPoolExecutor


def _request(
    action: str,
    *,
    context: str = "",
    user_text: str = "",
    session_id: str = "",
    operation_generation: int | None = None,
) -> dict:
    from screen_understanding import local_owner_authorized
    from system import require_local
    from wizard.gate import BRIDGE_DEFAULT_PORT, bridge_host, bridge_token

    try:
        require_local()
        if action != "stop" and not local_owner_authorized():
            return {
                "ok": False,
                "error": "Computer watch requires the local owner session.",
            }
        token = bridge_token()
        if not token:
            raise ValueError("missing authentication")
        port = int(os.environ.get("JARVIS_BRIDGE_PORT", str(BRIDGE_DEFAULT_PORT)))
        if not 1 <= port <= 65535:
            raise ValueError("invalid port")
        args = {"action": action}
        if context:
            args["context"] = str(context)[-1000:]
        endpoint = "/watch-control" if user_text else "/tool"
        payload = (
            {"text": user_text[:500], "session_id": session_id[:500]}
            if user_text
            else {"tool": "computer_watch", "args": args}
        )
        if operation_generation is not None:
            metadata = {
                "transport_id": _transport_id,
                "transport_epoch": operation_generation,
            }
            if user_text:
                payload.update(metadata)
            else:
                args.update(metadata)
        req = urllib.request.Request(
            f"http://{bridge_host()}:{port}{endpoint}",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        try:
            response = urllib.request.urlopen(req, timeout=5 if user_text else 3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            data = json.loads(response.read(262144))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"ok": False, "error": "The local Watch Mode bridge is unavailable."}


# Context/start/status share a bounded ordered transport. Stop uses its own
# request, cancels queued work and advances the server-checked client epoch.
_transport = ThreadPoolExecutor(max_workers=1, thread_name_prefix="watch-control")
_context_slots = threading.BoundedSemaphore(4)
_control_lock = threading.RLock()
_pending_context = set()
_control_slots = threading.BoundedSemaphore(4)
_pending_controls = set()
_generation = 0
_transport_id = secrets.token_hex(16)


def _fenced_request(generation, action, **kwargs):
    with _control_lock:
        if generation != _generation:
            return {"ok": False, "error": "Watch control was cancelled by Stop."}
    return _request(action, operation_generation=generation, **kwargs)


def _cancel_pending():
    global _generation
    with _control_lock:
        _generation += 1
        for pending in list(_pending_context | _pending_controls):
            pending.cancel()


def _control_request(action, **kwargs):
    caller = contextvars.copy_context()
    if action == "stop":
        _cancel_pending()
        return caller.run(_request, action, operation_generation=_generation, **kwargs)
    with _control_lock:
        if not _control_slots.acquire(blocking=False):
            return {"ok": False, "error": "Watch controls are busy; try again shortly."}
        future = _transport.submit(
            caller.run, _fenced_request, _generation, action, **kwargs
        )
        _pending_controls.add(future)

        def finished(done):
            with _control_lock:
                _pending_controls.discard(done)
            _control_slots.release()

        future.add_done_callback(finished)
    try:
        return future.result(timeout=15)
    except (TimeoutError, CancelledError):
        future.cancel()
        return {"ok": False, "error": "Watch control timed out or was cancelled."}


def request(action: str, *, context: str = "") -> dict:
    return _control_request(action, context=context)


def note_context(text: str) -> None:
    from screen_understanding import local_owner_authorized

    if not local_owner_authorized() or not _context_slots.acquire(blocking=False):
        return
    caller = contextvars.copy_context()
    with _control_lock:
        future = _transport.submit(
            caller.run,
            _fenced_request,
            _generation,
            "context",
            context=str(text)[-1000:],
        )
        _pending_context.add(future)

        def finished(done):
            with _control_lock:
                _pending_context.discard(done)
            _context_slots.release()

        future.add_done_callback(finished)


def stop() -> dict:
    return request("stop")


def trusted_user_control(text: str, session_id: str) -> dict:
    """Only the fresh SDK user-message/native adapter calls this; no model tool."""
    import computer_watch

    action = "stop" if computer_watch.parse_command(text) == "stop" else "user_control"
    return _control_request(action, user_text=text, session_id=session_id)
