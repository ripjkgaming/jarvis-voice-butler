"""Trusted user ingress for the explicit, session-scoped visual disclosure.

Never register this coordinator as a model tool. Model start tools can start
metadata observation; only a fresh actual user command accepts image processing.
"""

from __future__ import annotations

import contextlib
import os
import re
import secrets
import threading
import time
import uuid

_transport_epochs = {}
_transport_lock = threading.RLock()


def accept_transport(fields):
    """Fence late requests from a stopped client without granting any consent."""
    source, epoch = fields.get("transport_id"), fields.get("transport_epoch")
    if source is None and epoch is None:
        return True  # physical native ingress and existing compatibility clients
    if (
        not isinstance(source, str)
        or len(source) != 32
        or type(epoch) is not int
        or epoch < 0
    ):
        return False
    with _transport_lock:
        if source not in _transport_epochs and len(_transport_epochs) >= 64:
            return False
        if epoch < _transport_epochs.get(source, 0):
            return False
        _transport_epochs[source] = epoch
        return True


def confirmation_command(text):
    clean = " ".join(re.sub(r"[^a-z ]", " ", str(text).lower()).split())
    return clean in {"confirm visual watch", "jarvis confirm visual watch"}


def _target():
    import active_window

    return active_window.active() or {}


def _identity(target):
    try:
        identity = (
            str(target.get("id", "")),
            str(target.get("app", "")),
            int(target.get("pid", 0)),
        )
        with contextlib.suppress(ValueError):
            identity = (
                str(uuid.UUID(identity[0].strip("{}"))),
                identity[1],
                identity[2],
            )
        return identity if identity[2] > 0 else ("", "", 0)
    except (AttributeError, ValueError, TypeError):
        return ("", "", 0)


def _start(**kwargs):
    import computer_watch

    return computer_watch.start_visual(**kwargs)


def _owner():
    import computer_watch

    return computer_watch.allowed()


def _capture_policy():
    from watch_capture_package import installed_helper

    return (
        "pinned_window"
        if os.environ.get("JARVIS_WATCH_CAPTURE_HELPER") or installed_helper()
        else "foreground_window"
    )


def _inspect_native_target(candidate):
    from watch_native_capture import inspect_target

    return inspect_target(
        _identity(candidate), helper=os.environ.get("JARVIS_WATCH_CAPTURE_HELPER")
    )


class WatchControls:
    def __init__(
        self,
        *,
        owner=_owner,
        target=_target,
        start=_start,
        clock=time.time,
        inspect_target=_inspect_native_target,
        capture_policy=_capture_policy,
    ):
        self.owner, self.target, self.start, self.clock = owner, target, start, clock
        self.inspect_target, self.capture_policy = inspect_target, capture_policy
        self._pending = None
        self._lock = threading.RLock()
        self._generation = 0

    def handle(self, text, session_id):
        import computer_watch

        action = computer_watch.parse_command(text)
        confirm = confirmation_command(text)
        if action is None and not confirm:
            return None
        if action == "stop":
            # Revoke before taking the enrollment lock so Stop remains responsive
            # while selected-window permission is being established.
            self._generation += 1
            self._pending = None
            stopped = computer_watch.stop()
            return {
                "ok": stopped.get("ok", True),
                "active": False,
                "say": "Stopped watching the computer.",
            }
        with self._lock:
            if not self.owner():
                self._pending = None
                return {
                    "ok": False,
                    "error": "Visual watch requires the local owner session.",
                }
            if action == "status":
                status = computer_watch.status()
                return {
                    "ok": True,
                    **status,
                    "say": f"Watch Mode is {status.get('state', 'unavailable')}. Visual observation: {status.get('screen_observation', False)}. Use Watch Mode briefing in the tray for details.",
                }
            if action == "briefing":
                status = computer_watch.status()
                return {
                    "ok": True,
                    "say": computer_watch.briefing(),
                    "visual_private": bool(
                        status.get("screen_observation") or status.get("visual")
                    ),
                }
            if action == "start":
                generation = self._generation
                target = self.target()
                identity = _identity(target)
                if not all(identity):
                    self._pending = None
                    return {
                        "ok": False,
                        "error": "The selected window is unavailable; select a window and ask again.",
                    }
                policy = self.capture_policy()
                if policy == "pinned_window":
                    try:
                        verified = self.inspect_target(target)
                        if _identity(verified) != identity:
                            raise ValueError("Target identity mismatch")
                        title = " ".join(str(verified.get("title", "")).split())[:120]
                        if not title:
                            raise ValueError("Missing fresh target title")
                    except Exception:
                        self._pending = None
                        return {
                            "ok": False,
                            "cause_code": "selected_window_unverified",
                            "error": "The chosen window could not be verified; no image captured. Select the application window and ask again.",
                        }
                else:
                    title = ""
                if generation != self._generation:
                    return {
                        "ok": False,
                        "error": "Watch request was cancelled by Stop.",
                    }
                self._pending = (
                    str(session_id),
                    identity,
                    self.clock() + 120,
                    generation,
                    policy,
                )
                app = identity[1][:100]
                scope = (
                    f"the named {app} window '{title}' even while switching apps; minimizing or closing it pauses observation"
                    if policy == "pinned_window"
                    else f"the selected {app} window; changing windows pauses observation"
                )
                disclosure = f"Visual Watch Mode watches {scope} and sends that window's screenshots to the configured Gemini provider for read-only analysis. Images and raw OCR are not retained. Permission lasts up to two hours and sixty image calls."
                spoken = re.sub(
                    r"confirm[\W_]+visual[\W_]+watch",
                    "the approval phrase",
                    disclosure,
                    flags=re.I,
                )
                return {
                    "ok": True,
                    "confirmation_required": True,
                    "say": disclosure
                    + " Say 'confirm visual watch' within two minutes to start, or 'stop watching' to cancel.",
                    "spoken_disclosure": spoken
                    + " Review the named window and approve using the Watch Mode confirmation control in the tray.",
                }
            pending, self._pending = self._pending, None
            if not pending or pending[4] != self.capture_policy():
                return {
                    "ok": False,
                    "error": "Visual permission or capture policy changed. Ask to watch the computer again.",
                }
            try:
                target = (
                    self.inspect_target(
                        {
                            "id": pending[1][0],
                            "app": pending[1][1],
                            "pid": pending[1][2],
                        }
                    )
                    if pending[4] == "pinned_window"
                    else self.target()
                )
            except Exception:
                return {
                    "ok": False,
                    "cause_code": "selected_window_unverified",
                    "error": "The named window could not be verified; no image captured. Ask to watch the computer again.",
                }
            identity = _identity(target)
            if (
                not pending
                or pending[0] != str(session_id)
                or self.clock() > pending[2]
                or identity != pending[1]
                or self._generation != pending[3]
            ):
                return {
                    "ok": False,
                    "error": "Visual permission expired or the selected window changed. Ask to watch the computer again.",
                }
            from watch_visual import WatchConsent

            consent = WatchConsent(
                provider="gemini",
                selected_window=True,
                expires_at=self.clock() + 7200,
                max_calls=60,
                capture_policy=pending[4],
            )
            generation = pending[3]
            result = self.start(
                session_id=secrets.token_hex(16),
                consent=consent,
                expected_target=pending[1],
                consent_valid=lambda: generation == self._generation and self.owner(),
            )
            if result.get("ok"):
                result["say"] = (
                    "Visual Watch Mode is watching the selected window. Stop watching or use the tray Stop Watch Mode control to end it."
                )
            return result


controls = WatchControls()
