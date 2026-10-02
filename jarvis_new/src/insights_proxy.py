"""Read-only JSON access to two fixed local dashboard services.

These functions run in the bridge's existing HTTP request threads. They do
not start collectors, cache/reprice reports, follow redirects, or accept an
upstream URL from a caller. Successful payloads keep their original bytes so
the tracker remains authoritative for totals, missing values and freshness.
"""

from __future__ import annotations

import json
from contextlib import suppress
from http.client import HTTPConnection, HTTPException
from socket import SHUT_RDWR
from threading import Timer
from time import monotonic
from urllib.parse import parse_qs, urlencode

_TIMEOUT_SECONDS = 8.0
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_USAGE_PORT = 42731
_PAPER_MARKET_PORT = 8767
_RANGES = frozenset({"today", "week", "month", "all"})
_TIMEZONE = "Asia/Singapore"


def _error(status: int, message: str) -> tuple[int, bytes]:
    return status, json.dumps({"ok": False, "error": message}).encode()


def _remaining(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("Local dashboard request deadline exceeded")
    return remaining


def _reject_constant(value: str) -> None:
    raise ValueError("Nonfinite numbers are not JSON")


def _read_json(port: int, path: str, label: str) -> tuple[int, bytes]:
    # HTTPConnection goes directly to this literal address, ignoring proxy
    # environment variables. It also never follows a Location response.
    conn = HTTPConnection("127.0.0.1", port, timeout=_TIMEOUT_SECONDS)
    deadline = monotonic() + _TIMEOUT_SECONDS
    watchdog = None
    try:
        conn.connect()
        # Retain the socket before getresponse(): HTTP/1.0 responses detach
        # it from the connection while their file object still owns it.
        source_socket = conn.sock
        source_socket.settimeout(_remaining(deadline))

        def expire_request() -> None:
            # Inactivity timeouts alone cannot bound http.client's status/
            # header parser when a source keeps trickling bytes. Shutdown
            # wakes that parser as well as any blocked body read.
            with suppress(OSError):
                source_socket.shutdown(SHUT_RDWR)

        watchdog = Timer(_remaining(deadline), expire_request)
        watchdog.daemon = True
        watchdog.start()
        conn.request("GET", path, headers={"Accept": "application/json"})
        with conn.getresponse() as response:
            _remaining(deadline)
            if response.status != 200 and not 400 <= response.status <= 599:
                return _error(502, f"{label} returned an unexpected HTTP status")
            content_type = response.getheader("Content-Type", "").split(";", 1)[0]
            if content_type.strip().lower() != "application/json":
                return _error(502, f"{label} returned an invalid JSON response")
            declared_length = response.getheader("Content-Length")
            if declared_length is not None:
                declared_length = int(declared_length)
                if declared_length > _MAX_RESPONSE_BYTES:
                    return _error(502, f"{label} response is too large")
                if declared_length < 0:
                    return _error(502, f"{label} returned an invalid JSON response")
            raw = bytearray()
            while not response.isclosed():
                # Consuming Content-Length can close an HTTP/1.0 or explicit
                # Connection: close socket. No further socket operation is
                # needed once the declared body is complete.
                if declared_length is not None and len(raw) == declared_length:
                    break
                source_socket.settimeout(_remaining(deadline))
                # read1 performs at most one underlying socket read, allowing
                # the deadline to advance even if a source trickles bytes.
                chunk = response.read1(min(65536, _MAX_RESPONSE_BYTES + 1 - len(raw)))
                _remaining(deadline)
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    return _error(502, f"{label} response is too large")
            if declared_length is not None and len(raw) != declared_length:
                return _error(502, f"{label} returned an invalid JSON response")
            # Validate without reserializing or rounding any report values.
            if not isinstance(json.loads(raw, parse_constant=_reject_constant), dict):
                return _error(502, f"{label} returned an invalid JSON response")
            return response.status, bytes(raw)
    except (OSError, HTTPException):
        return _error(503, f"{label} is unavailable")
    except (ValueError, UnicodeError):
        return _error(502, f"{label} returned an invalid JSON response")
    finally:
        if watchdog is not None:
            watchdog.cancel()
            # Wait for a callback already in flight before releasing the
            # socket. The watchdog captures only this request's socket.
            watchdog.join()
        conn.close()


def usage(query: str = "") -> tuple[int, bytes]:
    """Return the ccusage report for an allowlisted range in Singapore time."""
    if len(query) > 256:
        return _error(400, "Invalid usage query")
    try:
        params = parse_qs(
            query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=2,
            errors="strict",
        )
    except (ValueError, UnicodeError):
        return _error(400, "Invalid usage query")
    if set(params) - {"range", "timezone"} or any(
        len(values) != 1 for values in params.values()
    ):
        return _error(400, "Only one range and timezone may be supplied")
    selected_range = params.get("range", ["today"])[0]
    timezone = params.get("timezone", [_TIMEZONE])[0]
    if selected_range not in _RANGES or timezone != _TIMEZONE:
        return _error(400, "Use range=today|week|month|all and timezone=Asia/Singapore")
    # Source is always ccusage; callers cannot select supplemental estimates,
    # change the destination, or add tracker filters that would alter totals.
    path = "/api/dashboard?" + urlencode(
        {"source": "ccusage", "range": selected_range, "timezone": timezone}
    )
    return _read_json(_USAGE_PORT, path, "Usage tracker")


def paper_market(query: str = "") -> tuple[int, bytes]:
    """Read the paper-market snapshot; no URL or query is caller-controlled."""
    if query:
        return _error(400, "Paper market does not accept query parameters")
    return _read_json(_PAPER_MARKET_PORT, "/api/paper-market", "Paper market")
