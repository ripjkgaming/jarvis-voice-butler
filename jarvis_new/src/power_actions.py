"""Bounded power dispatch receipts and one-use HUD confirmation tickets."""

from __future__ import annotations

import secrets
import threading
import time

from action_outcome import ActionOutcome

CONFIRM_ACTIONS = frozenset({"restart", "shutdown", "logout"})
LOCK_READ_ARGV = (
    "gdbus",
    "call",
    "--session",
    "--dest",
    "org.freedesktop.ScreenSaver",
    "--object-path",
    "/ScreenSaver",
    "--method",
    "org.freedesktop.ScreenSaver.GetActive",
)


class ConfirmationStore:
    """Memory-only, bearer-scope/action-bound, 3.5-second, consume-before-effect."""

    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self._tickets = {}
        self._lock = threading.Lock()

    def issue(self, action, scope):
        if action not in CONFIRM_ACTIONS or not scope:
            raise ValueError("unsupported confirmation")
        with self._lock:
            now = self.clock()
            self._tickets = {
                key: row
                for key, row in self._tickets.items()
                if row[2] > now and row[1] != scope
            }
            if len(self._tickets) >= 32:
                raise ValueError("confirmation capacity")
            token = secrets.token_urlsafe(32)
            self._tickets[token] = (action, scope, now + 3.5)
            return token

    def revoke(self, scope):
        with self._lock:
            self._tickets = {
                key: row for key, row in self._tickets.items() if row[1] != scope
            }

    def consume(self, token, action, scope):
        if not isinstance(token, str) or len(token) > 128:
            return False
        with self._lock:
            row = self._tickets.pop(token, None)
            return bool(
                row and row[0] == action and row[1] == scope and self.clock() < row[2]
            )


def argv(action):
    return {
        # Plural: bare lock-session only locks the caller's (seatless,
        # systemd manager) session and returns 0 with the screen still open.
        "lock": ("loginctl", "lock-sessions"),
        "suspend": ("systemctl", "suspend"),
        "reboot": ("systemctl", "reboot"),
        "shutdown": ("systemctl", "poweroff"),
        "logout": (
            "dbus-send",
            "--session",
            "--print-reply",
            "--dest=org.kde.Shutdown",
            "/Shutdown",
            "org.kde.Shutdown.logout",
        ),
    }.get(action)


def normalize(action):
    return {"restart": "reboot", "sleep": "suspend"}.get(action, action)


def parse_lock_active(text):
    return {"(true,)": True, "(false,)": False}.get(text.strip())


def lock_state(run):
    """Is the desktop screen locked right now? None when unreadable.

    Runs LOCK_READ_ARGV (ScreenSaver.GetActive, 2 s timeout). Never raises.
    Callers cache it (phone_control keeps a 2 s TTL).
    """
    try:
        result = run(list(LOCK_READ_ARGV), capture_output=True, text=True, timeout=2.0)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return parse_lock_active(result.stdout or "")


def receipt(action, rc, lock_active=None):
    action = normalize(action)
    evidence = {"launcher_exit_code": rc}
    if action == "lock" and lock_active is not None:
        evidence["lock_active"] = lock_active
    if rc == 0 and action == "lock" and lock_active is True:
        return ActionOutcome(
            "power.lock",
            "verified",
            "started",
            "matched",
            "owner_desktop",
            "lock_readback",
            evidence,
        )
    if rc not in (0, None, 124):
        return ActionOutcome(
            "power." + action,
            "failed",
            "started",
            "not_attempted",
            "owner_desktop",
            "command_failed",
            evidence,
        )
    check = (
        "mismatched"
        if lock_active is False
        else "unavailable"
        if action == "lock"
        else "not_attempted"
    )
    return ActionOutcome(
        "power." + action,
        "dispatched_unverified",
        "started" if rc == 0 else "unknown",
        check,
        "owner_desktop",
        "request_unverified",
        evidence,
    )


def dispatch_sync(action, run):
    action = normalize(action)
    command = argv(action)
    if command is None:
        raise ValueError("unsupported power action")
    try:
        result = run(list(command), capture_output=True, text=True, timeout=5.0)
    except Exception:
        return receipt(action, None)
    active = None
    if result.returncode == 0 and action == "lock":
        try:
            check = run(
                list(LOCK_READ_ARGV), capture_output=True, text=True, timeout=2.0
            )
            if check.returncode == 0:
                active = parse_lock_active(check.stdout)
        except Exception:
            pass
    return receipt(action, result.returncode, active)
