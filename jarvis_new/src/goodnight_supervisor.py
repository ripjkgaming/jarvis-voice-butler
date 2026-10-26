"""Bridge-owned ephemeral plan; authenticated private host inbox, no HTTP arm.

The owner-only HMAC capability belongs to trusted local service code, never a
model/tool argument. Same-UID malicious code is outside this physical-host trust
boundary. Bridge restart rotates capability and does not restore any arm.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import threading
import time
import uuid

from goodnight_fence import Fence, _open
from goodnight_host import _write
from goodnight_runtime import Host, SessionLock, WaylandIdle
from goodnight_supported import SupportedInventory
from goodnight_v2 import elapsed_clock


def owner_allowed():
    from screen_understanding import local_owner_authorized

    return local_owner_authorized()


def _read(path, limit=8192):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or info.st_size > limit
        ):
            raise ValueError("unsafe internal record")
        return json.loads(os.read(fd, limit + 1))
    finally:
        os.close(fd)


def _encoded(data):
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def revoke_security():
    """Durable before guest transition/publication; no blocking admission wait.

    Never auto-clear: explicit supervisor restart revokes old arm/capability.
    """
    if os.environ.get("JARVIS_GOODNIGHT_V2") == "1":
        fd = _open(Fence().directory / "detached" / "revoked")
        os.close(fd)


class Calls:
    def __init__(self, fence, clock=elapsed_clock):
        self.directory = fence.directory / "detached" / "calls"
        self.clock = clock

    def sample(self):
        try:
            if self.directory.is_symlink():
                return None, None
            paths = list(self.directory.glob("*.json"))
            if len(paths) > 4096:
                return None, None
            ages = []
            for path in paths:
                row = _read(path)
                signal = _read(path.with_suffix(".activity"))
                values = (row["at"], signal["at"])
                if any(
                    type(v) not in {float, int} or not math.isfinite(v) for v in values
                ):
                    return None, None
                now = self.clock()
                if now - row["at"] < 0 or now - signal["at"] < 0:
                    return None, None
                if row.get("closed") is True:
                    ages.append(now - signal["at"])
                    continue  # authenticated host lifecycle receipt after drain
                if now - row["at"] > 15 or row["allowed"] is not True:
                    return None, None
                if row["agent"] not in {"idle", "listening"} or row["user"] not in {
                    "away",
                    "listening",
                }:
                    return False, 0
                ages.append(now - signal["at"])
            return True, min(ages) if ages else 3600.0
        except Exception:
            return None, None


class Supervisor:
    def __init__(
        self,
        *,
        fence=None,
        idle=None,
        session_lock=None,
        authorized=owner_allowed,
        inventory=None,
        clock=elapsed_clock,
    ):
        self.fence = fence or Fence()
        self.directory = self.fence.directory / "detached"
        self.clock, self.authorized = clock, authorized
        self.epoch, self.key = uuid.uuid4().hex, secrets.token_hex(32)
        self.calls = Calls(self.fence, clock)
        self.host = Host(
            idle=idle or WaylandIdle(),
            inventory=inventory or SupportedInventory(self.fence.root, self.fence),
            session_lock=session_lock
            or SessionLock(os.environ.get("XDG_SESSION_ID", "")),
            owner_allowed=self.allowed,
            conversation_clear=lambda: self.calls.sample()[0],
            conversation_idle=lambda: self.calls.sample()[1],
            launch_fence=self.fence,
            clock=clock,
        )
        self.seen = {}
        self.cancel_before = -1.0
        self.cancel_seen = {}
        self.owner_identity = f"{os.getuid()}/{self.host.session_lock.identity()}"
        self.host.controller.finalizer = self._final
        self.fd = None
        self.stop = threading.Event()
        self.thread = None
        self.guard = threading.RLock()

    def allowed(self):
        return (
            not self.stop.is_set()
            and not (self.directory / "revoked").exists()
            and self.authorized() is True
        )

    def start(self, *, background=True):
        import fcntl

        with self.fence():
            fd = _open(self.directory / "supervisor.lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                os.close(fd)
                raise
            self.fd = fd
            try:
                # New generation invalidates every previous request and response.
                _write(
                    self.directory / "capability.json",
                    {
                        "epoch": self.epoch,
                        "key": self.key,
                        "owner": self.owner_identity,
                    },
                )
                # A security revocation cannot be cleared by merely restoring owner;
                # only explicit restart starts a new disarmed generation.
                (self.directory / "revoked").unlink(missing_ok=True)
                self._status()
            except BaseException:
                os.close(fd)
                self.fd = None
                self.stop.set()
                raise
        if background:
            self.thread = threading.Thread(
                target=self._run, name="goodnight-supervisor", daemon=True
            )
            self.thread.start()
        return self

    def _status(self):
        _write(
            self.directory / "status.json",
            {"epoch": self.epoch, "at": self.clock(), **self.host.controller.status()},
        )

    def accept(self, envelope):
        with self.guard:
            data, signature = envelope["data"], envelope["signature"]
            if set(data) != {
                "epoch",
                "id",
                "operation",
                "issued",
                "expires",
                "owner",
                "call",
                "turn",
                "generation",
            }:
                raise ValueError("invalid handoff")
            expected = hmac.new(
                bytes.fromhex(self.key), _encoded(data), hashlib.sha256
            ).hexdigest()
            if (
                not isinstance(signature, str)
                or not hmac.compare_digest(signature, expected)
                or data["epoch"] != self.epoch
            ):
                raise ValueError("unauthenticated handoff")
            for name in ("id", "call", "turn"):
                if (
                    not isinstance(data[name], str)
                    or not data[name]
                    or len(data[name]) > 128
                ):
                    raise ValueError("invalid identity")
            if data["id"] in self.seen:
                return {
                    **self.seen[data["id"]],
                    "current": self.host.controller.status(),
                    "replay": True,
                }
            if len(self.seen) >= 4096:
                self.host.controller.cancel("handoff replay ledger full")
                raise ValueError("bounded ledger full")
            now = self.clock()
            if (
                type(data["issued"]) not in {int, float}
                or not math.isfinite(data["issued"])
                or type(data["expires"]) not in {int, float}
                or not math.isfinite(data["expires"])
                or not 0 <= now - data["issued"] <= 10
                or data["expires"] != data["issued"] + 3600
                or data["owner"] != f"{os.getuid()}/{self.host.session_lock.identity()}"
                or not self.allowed()
            ):
                raise ValueError("stale owner handoff")
            operation = data["operation"]
            if not re.fullmatch(r"[a-f0-9]{32}", data["id"]):
                raise ValueError("invalid request identity")
            if operation == "arm" and (
                not re.fullmatch(r"[a-f0-9]{32}", data["call"])
                or not isinstance(data["generation"], str)
                or not re.fullmatch(r"[a-f0-9]{32}", data["generation"])
                or data["issued"] <= self.cancel_before
            ):
                raise ValueError("revoked arm generation")
            if operation == "arm":
                call = _read(self.calls.directory / (data["call"] + ".json"))
                if (
                    call.get("allowed") is not True
                    or type(call.get("at")) not in {float, int}
                    or not math.isfinite(call["at"])
                    or not 0 <= now - call["at"] <= 15
                ):
                    raise ValueError("trusted call unavailable")
                # Admission serializes accepted arm with task registration.
                with self.fence():
                    if not self.allowed():
                        raise ValueError("authority revoked")
                    c = self.host.controller
                    identity = self.host.session_lock.identity()
                    with c._mutex:
                        if data["issued"] <= self.cancel_before or not self.allowed():
                            raise ValueError("handoff cancelled before acceptance")
                        previous = c.status()["token"]
                        result = c.arm(owner=True, direct_intent=True, session=identity)
                        if (
                            result["ok"]
                            and c._token == result.get("token")
                            and c._token != previous
                        ):
                            c._token = data["generation"]
                            c._armed_at = data["issued"]
                            c._expires_at = data["expires"]
                    if result["ok"]:
                        self.host.idle.start()
            elif operation == "cancel":
                c = self.host.controller
                with c._mutex:
                    if c.status()["token"] != data["generation"]:
                        result = {"ok": False, "reason": "request generation changed"}
                    else:
                        self.cancel_before = now
                        cancelled = c.cancel()
                        result = {
                            "ok": cancelled or c.status()["state"] == "cancelled",
                            "reason": c.status()["reason"],
                        }
            elif operation == "status":
                result = {"ok": True}
            else:
                raise ValueError("unsupported internal operation")
            response = {
                **result,
                "current": self.host.controller.status(),
                "epoch": self.epoch,
            }
            self.seen[data["id"]] = response
            self._status()
            return response

    def _urgent_cancel(self):
        paths = list((self.directory / "cancels").glob("*.json"))
        if len(paths) > 4096:
            self.host.controller.cancel("cancel inbox overflow")
            return
        for path in paths:
            try:
                envelope = _read(path)
                d, signature = envelope["data"], envelope["signature"]
                expected = hmac.new(
                    bytes.fromhex(self.key), _encoded(d), hashlib.sha256
                ).hexdigest()
                if (
                    d.get("operation") != "cancel"
                    or d.get("epoch") != self.epoch
                    or d.get("owner") != self.owner_identity
                    or not hmac.compare_digest(signature, expected)
                    or type(d.get("issued")) not in {float, int}
                    or not math.isfinite(d["issued"])
                    or not 0 <= self.clock() - d["issued"] <= 10
                    or d.get("expires") != d["issued"] + 3600
                ):
                    raise ValueError("invalid cancel")
                if d["id"] in self.cancel_seen:
                    result = self.cancel_seen[d["id"]]
                else:
                    if len(self.cancel_seen) >= 4096:
                        self.host.controller.cancel("cancel replay ledger full")
                        raise ValueError("cancel ledger full")
                    c = self.host.controller
                    with c._mutex:
                        if d["generation"] != c.status()["token"]:
                            result = {
                                "ok": False,
                                "reason": "request generation changed or already committed",
                            }
                        else:
                            self.cancel_before = self.clock()
                            c.cancel()
                            result = {"ok": c.status()["state"] == "cancelled"}
                        result = {**result, "current": c.status(), "epoch": self.epoch}
                        self.cancel_seen[d["id"]] = result
            except Exception:
                result = {"ok": False, "reason": "cancel authentication refused"}
            _write(self.directory / "replies" / path.name, result)
            path.unlink()

    def _final(self, expected, commit):
        with self.fence():
            self._urgent_cancel()
            prepare = getattr(self.host.session_lock, "prepare", None)
            request = (
                prepare(expected)
                if prepare
                else lambda: self.host.session_lock.lock(expected)
            )
            if request is None:
                return commit(None, lambda: False)
            # All slow checks precede Controller.commit. It then rechecks the
            # immutable expiry/gap/continuity before consuming the generation.
            sample = self.host.probe()
            self._urgent_cancel()
            return commit(sample, request)

    def step(self):
        self._urgent_cancel()
        if not self.allowed():
            self.host.controller.cancel("guest/security/owner revoked")
        inbox = self.directory / "inbox"
        paths = list(inbox.glob("*.json"))
        if len(paths) > 4096:
            self.host.controller.cancel("internal inbox overflow")
            return
        for path in sorted(paths):
            try:
                envelope = _read(path)
                result = self.accept(envelope)
            except Exception:
                result = {"ok": False, "reason": "internal handoff refused"}
            _write(self.directory / "replies" / path.name, result)
            path.unlink()
        self.host.tick()
        with self.fence():
            self._status()

    def _run(self):
        while not self.stop.is_set():
            try:
                self.step()
            except Exception:
                self.host.controller.cancel("supervisor evidence unavailable")
            self.stop.wait(0.5)

    def control(self, operation):
        from goodnight_controls import owner_control

        if operation != "cancel":
            return owner_control(self.host, operation, owner_allowed=self.allowed())
        if not self.allowed():
            return {"ok": False, "say": "Owner goodnight controls unavailable."}
        # Never wait on handoff admission/ledger mutex to cancel a live plan.
        c = self.host.controller
        with c._mutex:
            self.cancel_before = self.clock()
            cancelled = c.cancel()
            committed = c.status()["state"] in {"locking", "locked", "lock-unverified"}
        if committed:
            return {
                "ok": False,
                "say": "The lock request already started; cancellation cannot undo it.",
            }
        self.host.idle.close()
        return {
            "ok": True,
            "say": "Goodnight lock cancelled."
            if cancelled
            else "No goodnight lock request is pending.",
        }

    def close(self):
        self.stop.set()
        self.host.controller.cancel("supervisor restart/shutdown")
        if self.thread is not None:
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                raise TimeoutError("supervisor drain incomplete")
        try:
            self.host.close()
            with self.fence():
                self._status()
        finally:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None


class Client:
    def __init__(self, fence=None, *, clock=elapsed_clock):
        self.fence = fence or Fence()
        self.directory = self.fence.directory / "detached"
        self.clock = clock
        self.capability = _read(self.directory / "capability.json")

    def packet(
        self,
        operation,
        *,
        call="control",
        turn="control",
        generation=None,
        owner=None,
        issued=None,
    ):
        now = self.clock() if issued is None else issued
        data = {
            "epoch": self.capability["epoch"],
            "id": uuid.uuid4().hex,
            "operation": operation,
            "issued": now,
            "expires": now + 3600,
            "owner": owner or self.capability["owner"],
            "call": call,
            "turn": turn,
            "generation": generation
            if operation == "cancel"
            else generation or uuid.uuid4().hex,
        }
        return {
            "data": data,
            "signature": hmac.new(
                bytes.fromhex(self.capability["key"]), _encoded(data), hashlib.sha256
            ).hexdigest(),
        }

    def cancel(self, **kwargs):
        # Snapshot is used ONLY to target an exact generation, never as proof
        # of eligibility/arm/delivery. No serialized status roundtrip may delay
        # urgent cancellation behind final probes. A stale target is refused,
        # never broadened to a newer plan.
        status = _read(self.directory / "status.json")
        if status["epoch"] != self.capability["epoch"]:
            return {"ok": False, "reason": "supervisor generation changed"}
        kwargs["generation"] = status["token"]
        return self.send("cancel", **kwargs)

    def send(self, operation, **kwargs):
        packet = self.packet(operation, **kwargs)
        request_id = packet["data"]["id"]
        directory = "cancels" if operation == "cancel" else "inbox"
        _write(self.directory / directory / (request_id + ".json"), packet)
        response = self.directory / "replies" / (request_id + ".json")
        end = time.monotonic() + 3
        while time.monotonic() < end:
            if response.exists():
                result = _read(response)
                response.unlink()
                return result
            time.sleep(0.02)
        # Unknown delivery: never retry with a fresh ID or claim accepted.
        return {"ok": False, "reason": "handoff acknowledgement unavailable"}


def client_control(operation):
    try:
        client = Client()
        if operation == "cancel":
            result = client.cancel()
            return {
                "ok": result.get("ok", False),
                "say": "Goodnight lock cancelled."
                if result.get("ok")
                else "Cancellation unconfirmed or request already committed; check status.",
            }
        status = client.send("status")
        if not status.get("ok"):
            raise ValueError()
        state = status["current"]
        return {
            "ok": True,
            **state,
            "say": f"Goodnight lock is {state['state']}: {state['reason']}.",
        }
    except Exception:
        return {"ok": False, "say": "Goodnight supervisor unavailable."}


SERVICE = None


def start_service():
    global SERVICE
    if os.environ.get("JARVIS_GOODNIGHT_V2") == "1" and SERVICE is None:
        SERVICE = Supervisor().start()
    return SERVICE


def close_service():
    global SERVICE
    if SERVICE is not None:
        SERVICE.close()
        SERVICE = None
