"""Loopback dashboard API. Refreshing quotes never invokes a trading model."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from .calendar import session_at
from .engine import refresh
from .ledger import Ledger, utcnow

HOST = "127.0.0.1"
PORT = 8767


def handler_for(ledger: Ledger):
    class Handler(BaseHTTPRequestHandler):
        server_version = "JarvisPaperMarket/1"
        sys_version = ""

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *_):
            pass

        def _json(self, status: int, payload: dict):
            raw = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            port = self.server.server_port
            if self.headers.get("Host") not in {
                f"127.0.0.1:{port}",
                f"localhost:{port}",
            }:
                self._json(403, {"error": "Loopback Host required."})
                return
            # The fixed authenticated Jarvis bridge is the UI access path. No
            # CORS, browser Origin, request-selected upstream, or write endpoint.
            if self.headers.get("Origin"):
                self._json(
                    403, {"error": "Use the Jarvis bridge to access paper data."}
                )
                return
            if self.path == "/health":
                self._json(
                    200,
                    {
                        "service": "jarvis-paper-market",
                        "schema_version": 1,
                        "mode": "paper_only",
                    },
                )
            elif self.path == "/api/paper-market":
                try:
                    self._json(200, ledger.snapshot())
                except (RuntimeError, ValueError, TypeError):
                    self._json(503, {"error": "The paper ledger is unavailable."})
            else:
                self._json(404, {"error": "Unknown read-only endpoint."})

        def _reject_write(self):
            self._json(405, {"error": "This paper dashboard API is read-only."})

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _reject_write  # noqa: N815

    return Handler


def serve(ledger: Ledger, *, port: int = PORT):
    if not 1024 <= port <= 65535:
        raise ValueError("Paper service port must be between 1024 and 65535.")
    stopped = threading.Event()
    server = HTTPServer((HOST, port), handler_for(ledger))
    server.timeout = 1

    def mark_loop():
        while not stopped.is_set():
            now = utcnow()
            if now >= datetime.fromisoformat(ledger.experiment()["expires_at"]):
                # Preserve the final ledger and serve it; no post-cutoff marks.
                return
            try:
                refresh(ledger)
            except Exception:
                ledger.runtime(
                    last_refresh_error="Quote refresh failed; last observed values remain visible."
                )
            else:
                ledger.runtime(last_refresh_error=None)
            # Closed markets are checked infrequently, but wake for the next
            # opening. This thread refreshes only; it never schedules decisions.
            market = session_at(utcnow())
            delay = 60.0 if market.is_open else 900.0
            if market.next_open_at:
                delay = min(
                    delay, max(1.0, (market.next_open_at - utcnow()).total_seconds())
                )
            stopped.wait(delay)

    thread = threading.Thread(target=mark_loop, name="paper-quote-marks", daemon=True)
    thread.start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        stopped.set()
        server.server_close()
        thread.join(timeout=20)
