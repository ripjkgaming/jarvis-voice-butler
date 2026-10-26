"""Ephemeral owner authority for authenticated watch-phone jobs only.

Uses the existing LiveKit signing secret, never a new credential. Dispatch
metadata and the immutable participant attributes must agree with this room.
LiveKit job/RoomOptions API: https://docs.livekit.io/agents/server/job/
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import hashlib
import hmac
import ipaddress
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

_PREFIX = "jarvis-private-phone-"
_ROOM = re.compile(_PREFIX + r"[0-9a-f]{32}\Z")


@dataclass
class _Owner:
    identity: str
    active: bool = False
    revoked: bool = False
    approval_gate: object | None = None
    approval_epoch: int = 0
    phone_call_id: str = ""

    def revoke(self, *_):
        self.active = False
        self.revoked = True


_OWNER = contextvars.ContextVar("jarvis_private_phone_owner", default=None)
TTL = 900


def new_room() -> str:
    return _PREFIX + uuid.uuid4().hex


def private_room(room: object) -> bool:
    return isinstance(room, str) and room.startswith(_PREFIX)


def identity_for(room: str) -> str:
    if not _ROOM.fullmatch(room):
        raise ValueError("Invalid private phone room")
    return "jarvis-owner-phone-" + room.removeprefix(_PREFIX)


def _signature(data: dict) -> str:
    from mint_token import load_creds

    secret = load_creds().get("LIVEKIT_API_SECRET", "")
    if not secret:
        raise RuntimeError("Laptop voice service is unavailable")
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(secret.encode(), encoded, hashlib.sha256).hexdigest()


def sign(room: str, now: float | None = None) -> str:
    issued = int(time.time() if now is None else now)
    data = {
        "room": room,
        "identity": identity_for(room),
        "issued": issued,
        "expires": issued + TTL,
    }
    return json.dumps({**data, "signature": _signature(data)})


def verify(metadata: str, room: str, now: float | None = None) -> str:
    now = time.time() if now is None else now
    try:
        data = json.loads(metadata)
        if not isinstance(data, dict) or set(data) != {
            "room",
            "identity",
            "issued",
            "expires",
            "signature",
        }:
            raise ValueError
        signature = data.pop("signature")
        if (
            data["room"] != room
            or data["identity"] != identity_for(room)
            or type(data["issued"]) is not int
            or type(data["expires"]) is not int
            or data["expires"] - data["issued"] != TTL
            or data["issued"] > now + 5
            or data["expires"] <= now
            or not isinstance(signature, str)
            or not hmac.compare_digest(signature, _signature(data))
        ):
            raise ValueError
        return data["identity"]
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("Invalid or expired private phone dispatch") from exc


def private() -> bool:
    return _OWNER.get() is not None


def identity() -> str:
    owner = _OWNER.get()
    return owner.identity if owner is not None and owner.active else ""


def invitation_id() -> str:
    owner = _OWNER.get()
    return owner.phone_call_id if owner is not None and owner.active else ""


def guest_required() -> bool:
    import watch_mode

    owner = _OWNER.get()
    return not owner.active if owner is not None else watch_mode.guest_required()


PRIVATE_UNSAFE_TOOLS = frozenset(
    {
        "notify",
        "start_task",
        "approve_task",
        "start_workshop",
        "research_project",
        "find_cheaper_alternative",
        "code_project",
        "run_tests",
        "show_panel",
        "hide_panel",
        "open_insights",
        "close_insights",
        "open_projects",
        "navigate_archive",
        "projects_ui",
        "confirm_muse_send",
    }
)


def private_tool_error(tool: str) -> str | None:
    if private() and tool == "confirm_muse_send":
        return "During a private phone call, outbound WhatsApp work needs an exact reviewed spoken draft approval."
    if private() and tool in PRIVATE_UNSAFE_TOOLS:
        return (
            "During a private phone call, desktop notifications, shared panels and "
            "detached jobs are unavailable. Ask me to reply in this call instead."
        )
    return None


_APPROVAL_EFFECT = contextvars.ContextVar("jarvis_private_approval_effect", default=None)


def bind_approval_gate(gate):
    scope = _OWNER.get()
    if scope is not None and scope.active:
        scope.approval_gate = gate


def advance_approval_epoch():
    scope = _OWNER.get()
    if scope is not None:
        scope.approval_epoch += 1


def approval_ticket():
    scope = _OWNER.get()
    return (scope, scope.approval_epoch) if scope is not None else None


def check_approval_effect(ticket=None, *, current=False):
    """Immediately before generic input/exec, using its invocation epoch."""
    ticket = _APPROVAL_EFFECT.get() if ticket is None else ticket
    if ticket is None and current:
        ticket = approval_ticket()
    if ticket is None:
        return
    scope, epoch = ticket
    gate = scope.approval_gate
    if (not scope.active or scope.approval_epoch != epoch
            or (gate is not None and gate.transaction_active)):
        from system import LocalSystemError
        raise LocalSystemError(
            "Generic desktop/command actions cannot bypass an active WhatsApp review. "
            "Cancel that review, then make a fresh action request."
        )


def approval_action(fn):
    """Track generic input from invocation through copied worker contexts."""
    @functools.wraps(fn)
    async def run(*args, **kwargs):
        ticket = approval_ticket()
        token = _APPROVAL_EFFECT.set(ticket)
        try:
            check_approval_effect(ticket)
            return await fn(*args, **kwargs)
        except __import__("system").LocalSystemError as exc:
            from livekit.agents.llm import ToolError
            raise ToolError(str(exc)) from exc
        finally:
            _APPROVAL_EFFECT.reset(token)
    return run


def bind_session(session) -> None:
    """Revoke before the installed SDK schedules/drains closing activities.

    The SDK emits close *after* draining tools. These per-private-session
    adapters cover its current _close_soon and _aclose_impl entrypoints;
    ordinary sessions are untouched. Installed SDK contract is regression-tested.
    """
    scope = _OWNER.get()
    if scope is None:
        return
    close_soon = session._close_soon
    aclose_impl = session._aclose_impl

    def closing(*args, **kwargs):
        scope.revoke()
        return close_soon(*args, **kwargs)

    async def aclosing(*args, **kwargs):
        scope.revoke()
        return await aclose_impl(*args, **kwargs)

    session._close_soon = closing
    session._aclose_impl = aclosing
    session.on("close", scope.revoke)


def _state_dir() -> Path:
    import watch_mode

    return watch_mode.jarvis_home() / "phone_sessions"


def _pid_start(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return ""


def has_private_calls() -> bool:
    """Active job markers survive entrypoint return, but not worker death."""
    for path in _state_dir().glob("*.active"):
        try:
            data = json.loads(path.read_text())
            pid = data["pid"]
            if type(pid) is int and data["start"] and _pid_start(pid) == data["start"]:
                return True
            path.unlink(missing_ok=True)  # A dead worker cannot own a call.
        except (OSError, ValueError, TypeError, KeyError):
            # Malformed active state fails closed for shared captions.
            return True
    return False


def hide_shared_captions(peer: str) -> bool:
    """Old APKs have no room scope. Keep their shared read empty during watch."""
    import watch_mode

    try:
        local = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        local = False
    return not local and (watch_mode.guest_required() or has_private_calls())


def isolated_job(fn):
    """Claim one dispatch, verify the phone, and scope owner access to its job.

    Reconnection stays in this job. A new dispatch with the same nonce is
    rejected even after hangup; a fresh user call gets a new room/token.
    """

    @functools.wraps(fn)
    async def run(ctx):
        room = ctx.room.name
        if not private_room(room):
            token = _OWNER.set(None)
            try:
                return await fn(ctx)
            finally:
                _OWNER.reset(token)
        owner = verify(ctx.job.metadata, room)
        directory = _state_dir()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Old nonces cannot be accepted after expiry, so bound the consumed ledger.
        for old in directory.glob("*.claimed"):
            try:
                if time.time() - old.stat().st_mtime > TTL + 60:
                    old.unlink()
            except OSError:
                pass
        claimed = directory / (room + ".claimed")
        try:
            fd = os.open(claimed, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise RuntimeError("Private phone dispatch already claimed") from exc
        os.close(fd)
        active = directory / (room + ".active")

        scope = _Owner(owner)

        from livekit import rtc

        terminal_reasons = {
            rtc.DisconnectReason.CLIENT_INITIATED,
            rtc.DisconnectReason.ROOM_DELETED,
            rtc.DisconnectReason.USER_REJECTED,
            rtc.DisconnectReason.PARTICIPANT_REMOVED,
            rtc.DisconnectReason.ROOM_CLOSED,
            rtc.DisconnectReason.DUPLICATE_IDENTITY,
        }

        def participant_left(participant):
            if (
                participant.identity == owner
                and participant.disconnect_reason in terminal_reasons
            ):
                scope.revoke()

        listeners = [
            ("participant_disconnected", participant_left),
            ("disconnected", scope.revoke),
        ]
        for event, callback in listeners:
            ctx.room.on(event, callback)

        async def cleanup():
            scope.revoke()
            while listeners:
                event, callback = listeners.pop()
                ctx.room.off(event, callback)
            active.unlink(missing_ok=True)

        token = _OWNER.set(scope)
        try:
            # Bind before connect so RTC event tasks inherit the same room scope.
            await ctx.connect()
            participant = await asyncio.wait_for(
                ctx.wait_for_participant(identity=owner), timeout=15
            )
            if (
                ctx.room.name != room
                or participant.identity != owner
                or participant.attributes.get("jarvis.phone_owner") != room
            ):
                raise RuntimeError("Private phone participant does not match dispatch")
            call_id = participant.attributes.get("jarvis.phone_call", "")
            if call_id:
                import phone_calls
                if not re.fullmatch(r"[0-9a-f]{32}", call_id) or not phone_calls.queue().accepted(call_id, time.time()):
                    raise RuntimeError("Private incoming invitation is unavailable")
                scope.phone_call_id = call_id
            # Recheck expiry after waiting; no privilege from expired startup.
            verify(ctx.job.metadata, room)
            active.write_text(
                json.dumps({"pid": os.getpid(), "start": _pid_start(os.getpid())})
            )
            active.chmod(0o600)
            ctx.add_shutdown_callback(cleanup)
            if scope.revoked:
                raise RuntimeError("Private phone call ended during startup")
            scope.active = True
            return await fn(ctx)
        except BaseException:
            await cleanup()
            raise
        finally:
            if token is not None:
                _OWNER.reset(token)

    return run
