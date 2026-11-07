"""LOCKE//LINK owner controls and the /phone/v1/state aggregator.

The phone is always admin: ``/phone/v1/control`` is served before the
bridge's guest gate and calls the power/guest primitives directly (never
``taskbar.dispatch_power``, which refuses guests). Every probe is cached and
refreshed on a background thread, so a slow ``kscreen-doctor`` or a hung
D-Bus call never delays a state poll; the poll returns the cached value or
null. All side effects go through ``Deps`` so tests inject fakes.
"""

from __future__ import annotations

import contextvars
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import power_actions

API_VERSION = 1
STEPUP = frozenset({"guest_off", "unlock", "restart", "shutdown"})
ACTIONS = (
    "guest_on",
    "guest_off",
    "lock",
    "unlock",
    "screens_off",
    "screens_on",
    "sleep",
    "restart",
    "shutdown",
    "mic_mute",
    "mic_unmute",
)
READBACK_TRIES = 3
READBACK_GAP_S = 0.3


@dataclass
class Deps:
    run: Callable[..., Any] = subprocess.run
    run_phone_tool: Callable[[str, dict], dict] = lambda tool, args: {"ok": False}
    wake_rpc: Callable[[dict], dict | None] = lambda payload: None
    laptop_power: Callable[[], dict | None] = lambda: None
    voice: Callable[[], dict] = lambda: {}
    guest_required: Callable[[], bool] = lambda: False
    enter_guest: Callable[[], None] = lambda: None
    request_stop: Callable[[], Any] = lambda: None
    restore_owner: Callable[[], None] = lambda: None
    presence: Callable[[], dict] = lambda: {"status": "unknown"}
    log: Callable[[str, str], None] = lambda category, detail: None
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    host: str = ""
    dbus_ok: Callable[[], bool] = field(
        default=lambda: bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS"))
    )


def _isolated(fn, *args):
    """Run outside any private phone-call context (fresh contextvars)."""
    return contextvars.Context().run(fn, *args)


class Probe:
    """One cached reading. ``get`` never blocks: stale -> background refresh."""

    def __init__(self, read: Callable[[], Any], ttl: float, clock):
        self.read, self.ttl, self.clock = read, ttl, clock
        self.value: Any = None
        self.at: float | None = None
        self._busy = False
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        try:
            value = self.read()
        except Exception:
            value = None
        with self._lock:
            self.value, self.at, self._busy = value, self.clock(), False

    def get(self) -> tuple[Any, float | None]:
        with self._lock:
            stale = self.at is None or self.clock() - self.at >= self.ttl
            if stale and not self._busy:
                self._busy = True
                threading.Thread(target=self._refresh, daemon=True).start()
            age = None if self.at is None else round(self.clock() - self.at, 1)
            return self.value, age

    def put(self, value: Any) -> None:
        with self._lock:
            self.value, self.at = value, self.clock()


def screens_of(result: dict | None) -> str | None:
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    outputs = result.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        return None
    return "off" if all(not o.get("enabled") for o in outputs) else "on"


def power_of(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("battery"), int):
        return None
    ac = raw.get("ac")
    charging = ac if isinstance(ac, bool) else raw.get("status") == "Charging"
    return {"percent": raw["battery"], "charging": bool(charging)}


class PhoneControl:
    def __init__(self, deps: Deps, presence_enabled: bool | None = None):
        self.deps = deps
        if presence_enabled is None:
            presence_enabled = os.environ.get("JARVIS_PHONE_PRESENCE", "1") != "0"
        self.presence_enabled = presence_enabled
        clock = deps.clock
        self._dbus_warned = False
        self.probes = {
            "locked": Probe(self._read_lock, 2.0, clock),
            "screens": Probe(self._read_screens, 10.0, clock),
            "mic_muted": Probe(self._read_mic, 2.0, clock),
            "presence": Probe(self._read_presence, 60.0, clock),
        }

    # probes
    def _read_lock(self) -> bool | None:
        if not self.deps.dbus_ok():
            if not self._dbus_warned:
                self._dbus_warned = True
                self.deps.log(
                    "phone_state", "no DBUS_SESSION_BUS_ADDRESS; lock unknown"
                )
            return None
        return power_actions.lock_state(self.deps.run)

    def _read_screens(self) -> str | None:
        return screens_of(_isolated(self.deps.run_phone_tool, "screens_state", {}))

    def _read_mic(self) -> bool | None:
        reply = self.deps.wake_rpc({"status": True})
        muted = reply.get("muted") if isinstance(reply, dict) else None
        return muted if isinstance(muted, bool) else None

    def _read_presence(self) -> str:
        status = (self.deps.presence() or {}).get("status")
        return status if status in ("present", "absent") else "unknown"

    # state
    def laptop(self) -> tuple[dict, dict]:
        values, ages = {}, {}
        for key, probe in self.probes.items():
            if key == "presence" and not self.presence_enabled:
                values[key], ages[key] = "unknown", None
                continue
            values[key], ages[key] = probe.get()
        try:
            guest = bool(self.deps.guest_required())
        except Exception:
            guest = None
        laptop = {
            "host": self.deps.host,
            "guest": guest,
            "locked": values["locked"],
            "screens": values["screens"],
            "mic_muted": values["mic_muted"],
            "presence": values["presence"] or "unknown",
            "power": power_of(self._safe(self.deps.laptop_power)),
        }
        return laptop, ages

    @staticmethod
    def _safe(fn):
        try:
            return fn()
        except Exception:
            return None

    def _voice(self) -> dict:
        raw = self._safe(self.deps.voice) or {}
        return {
            "call_live": bool(raw.get("call_live")),
            "voice_owner": raw.get("voice_owner"),
            "agent_state": raw.get("agent_state"),
            "waking": bool(raw.get("waking")),
        }

    def state(self, gate: dict) -> dict:
        laptop, ages = self.laptop()
        return {
            "ok": True,
            "api_version": API_VERSION,
            "ts": time.time(),
            "laptop": laptop,
            "voice": self._voice(),
            "gate": gate,
            "caps": {
                "remote": False,
                "presence": self.presence_enabled,
                "webauthn": True,
            },
            "ages": ages,
        }

    # control
    def _poll_lock(self, want: bool) -> tuple[str, bool | None]:
        seen = None
        for i in range(READBACK_TRIES):
            if i:
                self.deps.sleep(READBACK_GAP_S)
            seen = self._read_lock() if self.deps.dbus_ok() else None
            if seen is want:
                break
            if seen is None and not self.deps.dbus_ok():
                break
        if seen is not None:
            self.probes["locked"].put(seen)
        if seen is want:
            return "verified", seen
        return ("mismatched" if seen is not None else "dispatched_unverified"), seen

    def _screens_readback(self, want: str) -> tuple[str, str | None]:
        seen = self._read_screens()
        if seen is not None:
            self.probes["screens"].put(seen)
        if seen == want:
            return "verified", seen
        return ("mismatched" if seen else "dispatched_unverified"), seen

    def _run_action(self, action: str) -> tuple[str, dict]:
        d = self.deps
        if action in ("guest_on", "guest_off"):
            if action == "guest_on":
                d.enter_guest()
            else:
                d.request_stop()
                d.restore_owner()
            guest = bool(d.guest_required())
            want = action == "guest_on"
            return ("verified" if guest is want else "mismatched"), {"guest": guest}
        if action == "lock":
            outcome = power_actions.dispatch_sync("lock", d.run)
            if outcome.state.value == "failed":
                return "failed", {"locked": None}
            status, seen = self._poll_lock(True)
            return status, {"locked": seen}
        if action == "unlock":
            result = _isolated(d.run_phone_tool, "unlock", {})
            if not (isinstance(result, dict) and result.get("ok")):
                return "failed", {"locked": None}
            status, seen = self._poll_lock(False)
            return status, {"locked": seen}
        if action in ("screens_off", "screens_on"):
            tool = "screen_off" if action == "screens_off" else "screens_restore"
            result = _isolated(d.run_phone_tool, tool, {})
            if action == "screens_on" and not (result or {}).get("ok"):
                # Nothing recorded as off (e.g. turned off elsewhere): enable all.
                result = _isolated(d.run_phone_tool, "screen_on", {})
            if not (isinstance(result, dict) and result.get("ok")):
                return "failed", {"screens": None}
            status, seen = self._screens_readback(
                "off" if action == "screens_off" else "on"
            )
            return status, {"screens": seen}
        if action in ("sleep", "restart", "shutdown"):
            outcome = power_actions.dispatch_sync(action, d.run)
            return (
                "failed" if outcome.state.value == "failed" else "dispatched_unverified"
            ), {}
        if action in ("mic_mute", "mic_unmute"):
            want = action == "mic_mute"
            reply = d.wake_rpc({"mute": want})
            muted = reply.get("muted") if isinstance(reply, dict) else None
            if not isinstance(muted, bool):
                return "failed", {"mic_muted": None}
            self.probes["mic_muted"].put(muted)
            return ("verified" if muted is want else "mismatched"), {"mic_muted": muted}
        raise ValueError(action)

    def control(self, principal, body, verify_stepup) -> tuple[int, dict]:
        """(status, payload) for POST /phone/v1/control."""
        action = body.get("action") if isinstance(body, dict) else None
        if action not in ACTIONS:
            return 400, {"ok": False, "error": "unknown action"}
        if action in STEPUP and not verify_stepup(
            principal, action, body.get("assertion")
        ):
            self.deps.log("phone_control", f"{action} stepup")
            return 403, {"ok": False, "error": "stepup", "stepup": action}
        try:
            status, readback = self._run_action(action)
        except Exception as exc:
            status, readback = "failed", {"error": str(exc)[:120]}
        self.deps.log("phone_control", f"{action} {status}")
        laptop, _ = self.laptop()
        payload = {
            "ok": status != "failed",
            "action": action,
            "status": status,
            "readback": readback,
            "state": laptop,
        }
        if status == "failed":
            payload["error"] = f"{action} failed"
        return 200, payload
