"""Bounded opt-in telemetry transport; no activation or credential I/O on import."""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import urllib.request
from collections import deque

from health_recorder import current_snapshot, tools_enabled, validate_event


class BridgeClient:
    def __init__(self, *, opener=None, environ=None):
        env = os.environ if environ is None else environ
        host = env.get("JARVIS_BRIDGE_BIND", "127.0.0.1")
        # MVP intentionally rejects ambiguous/non-loopback deployments.
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("local recorder bridge unavailable")
        port = int(env.get("JARVIS_BRIDGE_PORT", "4317"))
        if not 1 <= port <= 65535:
            raise ValueError("invalid local bridge port")
        host = "[::1]" if host == "::1" else host
        self.base = f"http://{host}:{port}"
        self.token = env.get("JARVIS_BRIDGE_TOKEN", "")
        self.opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect()
        )

    def request(self, route, payload=None):
        if not self.token:
            raise RuntimeError("recorder owner authentication unavailable")
        data = (
            None if payload is None else json.dumps(payload, allow_nan=False).encode()
        )
        if data is not None and len(data) > 16384:
            raise ValueError("batch exceeded bounds")
        request = urllib.request.Request(
            self.base + route,
            data=data,
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
            },
        )
        with self.opener.open(request, timeout=0.5) as response:
            raw = response.read(16385)
        if len(raw) > 16384:
            raise ValueError("report exceeded bounds")
        result = json.loads(raw)
        if type(result) is not dict:
            raise ValueError("invalid recorder response")
        return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HealthEventSender:
    def __init__(self, *, enabled=False, client=None, clock=time.monotonic):
        self.enabled, self.client, self.clock = enabled, client, clock
        self._queue = deque(maxlen=128)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker = None
        self.dropped = 0
        self._last_flush = None

    def offer(self, metric, value, scope, error=None):
        if not self.enabled:
            return False
        row = {
            "at": self.clock(),
            "metric": metric,
            "value": value,
            "scope": scope,
            "error": error,
        }
        try:
            validate_event(row, row["at"])
        except ValueError:
            return False
        if not self._lock.acquire(blocking=False):
            self.dropped = min(2**31 - 1, self.dropped + 1)
            return False
        try:
            if len(self._queue) == 128:
                self.dropped = min(2**31 - 1, self.dropped + 1)
                return False
            self._queue.append(row)
            return True
        finally:
            self._lock.release()

    def flush(self):
        """Off the voice callback path; at most one batch per five seconds."""
        if not self.enabled:
            return False
        now = self.clock()
        with self._lock:
            if self._last_flush is not None and now - self._last_flush < 5:
                return False
            self._last_flush = now
            rows = [self._queue.popleft() for _ in range(min(32, len(self._queue)))]
        if not rows:
            return True
        try:
            result = self.client.request(
                "/health-recorder/events", {"version": 1, "events": rows}
            )
            accepted = result.get("accepted", 0)
            if type(accepted) is not int or not 0 <= accepted <= len(rows):
                accepted = 0
            self.dropped = min(2**31 - 1, self.dropped + len(rows) - accepted)
            return bool(accepted)
        except Exception:
            self.dropped = min(2**31 - 1, self.dropped + len(rows))
            return False

    def start(self):
        if not self.enabled or self._worker is not None:
            return

        def run():
            delay = 5
            while not self._stop.wait(delay):
                delay = 5 if self.flush() else min(60, delay * 2)

        self._worker = threading.Thread(
            target=run, name="jarvis-health-sender", daemon=True
        )
        self._worker.start()

    def close(self):
        self.enabled = False
        self._stop.set()
        # No flush or network during shutdown.
        with self._lock:
            self._queue.clear()
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.join(timeout=0.1)


def classify_error(error, fallback="session_error"):
    # Do not inspect message strings, stack traces or provider bodies.
    import asyncio

    if isinstance(error, asyncio.CancelledError):
        return "cancelled"
    if isinstance(error, TimeoutError):
        return "timeout"
    try:
        status = getattr(error, "status_code", None)
    except Exception:
        return fallback
    if type(status) is int and status == 429:
        return "quota"
    return fallback


def owner_allowed():
    """Fail closed, including revoked/guest/private phone contexts."""
    try:
        import phone_session
        import watch_mode

        return not (
            phone_session.private()
            or phone_session.guest_required()
            or watch_mode.guest_required()
        )
    except Exception:
        return False


async def incident_report(window_s=300, *, allowed=None, client=None):
    import asyncio

    allowed = allowed or owner_allowed
    if not allowed():
        return {
            "say": "Health reports are unavailable in this session.",
            "text": "Health reports are unavailable in this session.",
        }
    if type(window_s) is not int or not 30 <= window_s <= 1800:
        return {
            "say": "Choose a health window between 30 and 1800 seconds.",
            "text": "Invalid health window.",
        }
    if client is None and not tools_enabled():
        return {
            "say": "Health diagnostics are disabled.",
            "text": "Health diagnostics are disabled.",
        }
    if os.environ.get("JARVIS_HEALTH_RECORDER") != "1" and client is None:
        try:
            result = await asyncio.to_thread(current_snapshot)
            if not allowed():
                raise RuntimeError("owner authority changed")
            return {"say": result["say"], "text": result["text"]}
        except Exception:
            return {
                "say": "Current health measurements are unavailable.",
                "text": "Current health measurements are unavailable.",
            }
    try:
        client = client or BridgeClient()
        result = await asyncio.to_thread(
            client.request, f"/health-recorder/incident?window_s={window_s}"
        )
        if not allowed():
            raise RuntimeError("owner authority changed")
        # Only accept a successful bounded local report. Never return error bodies.
        if (
            result.get("ok") is not True
            or type(result.get("say")) is not str
            or type(result.get("text")) is not str
        ):
            raise ValueError("unavailable report")
        return {"say": result["say"][:2048], "text": result["text"][:4096]}
    except Exception:
        return {
            "say": "Recent health measurements are unavailable.",
            "text": "Recent health measurements are unavailable.",
        }


def install_session_recorder(session, *, sender=None, allowed=None):
    """Guard callbacks on the session thread, before crossing into a worker."""
    existing = getattr(session, "_jarvis_health", None)
    if existing is not None:
        return existing
    if sender is None:
        if os.environ.get("JARVIS_HEALTH_RECORDER") != "1":
            return None
        try:
            sender = HealthEventSender(enabled=True, client=BridgeClient())
            sender.start()
        except Exception:
            return None
    allowed = allowed or owner_allowed

    def sink(metric, value, scope, error=None):
        try:
            if allowed():
                sender.offer(metric, value, scope, error)
        except Exception:
            pass

    session._jarvis_health = (sender, sink)

    def close(_event=None):
        sender.close()

    session.on("close", close)

    def on_error(event):
        # Inspect type/category only; never stringify/retain exception objects.
        with contextlib.suppress(Exception):
            error = getattr(event, "error", None)
            code = classify_error(error)
            sink(None, None, "session", code)

    session.on("error", on_error)
    return session._jarvis_health
