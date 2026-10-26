"""Strict health-only HTTP adapter. No server, sampler or threads on import."""

from __future__ import annotations

import json
import threading
from urllib.parse import parse_qs

from health_recorder import HealthRecorder, current_snapshot, tools_enabled

RECORDER = HealthRecorder()
_GATE = threading.Lock()


def handle(handler, method, parsed, *, recorder=None, allowed=None):
    recorder = RECORDER if recorder is None else recorder
    if not parsed.path.startswith("/health-recorder/"):
        return False

    def send(code, body):
        handler._send(code, body)

    if not handler.token:
        send(403, {"ok": False, "error": "owner token required"})
        return True
    if allowed is None:

        def allowed():
            from build_monitor import owner_allowed

            return owner_allowed(handler.client_address[0])

    if not allowed():
        send(403, {"ok": False, "error": "owner access required"})
        return True
    snapshot_request = method == "GET" and parsed.path == "/health-recorder/incident"
    if snapshot_request and not tools_enabled():
        send(503, {"ok": False, "error": "health diagnostics disabled"})
        return True
    if not recorder.enabled and not (snapshot_request and tools_enabled()):
        send(503, {"ok": False, "error": "health recording disabled"})
        return True
    if not _GATE.acquire(blocking=False):
        send(429, {"ok": False, "error": "health recorder busy"})
        return True
    try:
        if method == "GET" and parsed.path == "/health-recorder/incident":
            query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=2)
            if set(query) - {"window_s"} or len(query.get("window_s", ["300"])) != 1:
                raise ValueError("invalid query")
            window_s = int(query.get("window_s", ["300"])[0])
            if not 30 <= window_s <= 1800:
                raise ValueError("invalid window")
            result = (
                recorder.report(window_s) if recorder.enabled else current_snapshot()
            )
            if not allowed():
                send(403, {"ok": False, "error": "owner access required"})
            else:
                send(200, {"ok": True, **result})
        elif method == "POST" and parsed.path in {
            "/health-recorder/events",
            "/health-recorder/clear",
        }:
            if parsed.query or handler.headers.get("Transfer-Encoding"):
                raise ValueError("unsupported request framing")
            length = int(handler.headers.get("Content-Length", "0"))
            if not 1 <= length <= 16384:
                raise ValueError("invalid request size")
            handler.connection.settimeout(0.5)
            raw = handler.rfile.read(length)
            if len(raw) != length:
                raise ValueError("truncated request")
            payload = json.loads(raw)
            if not allowed():
                send(403, {"ok": False, "error": "owner access required"})
                return True
            if parsed.path.endswith("/clear"):
                if payload != {}:
                    raise ValueError("invalid clear request")
                recorder.clear()
                send(200, {"ok": True})
            else:
                if (
                    type(payload) is not dict
                    or set(payload) != {"version", "events"}
                    or type(payload["version"]) is not int
                    or payload["version"] != 1
                ):
                    raise ValueError("invalid event batch")
                now = recorder.clock()
                if (
                    recorder._last_ingest is not None
                    and now - recorder._last_ingest < 5
                ):
                    send(429, {"ok": False, "error": "health event rate exceeded"})
                else:
                    accepted = recorder.record_batch(payload["events"])
                    recorder._last_ingest = now
                    send(200, {"ok": True, "accepted": accepted})
        else:
            send(404, {"ok": False, "error": "unknown health recorder route"})
    except (ValueError, OSError, UnicodeError, RecursionError):
        send(400, {"ok": False, "error": "invalid health recorder request"})
    finally:
        _GATE.release()
    return True
