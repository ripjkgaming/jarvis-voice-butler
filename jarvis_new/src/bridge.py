"""Localhost control plane for embedding Jarvis in a desktop shell.

A desktop app (Electron/Tauri) cannot reach into the LiveKit worker or the
wake-word listener directly, so this module exposes host-side state over a
loopback-only HTTP server using stdlib only (no new dependencies)::

    .venv/bin/python src/bridge.py            # serves 127.0.0.1:4317
    JARVIS_BRIDGE_PORT=4320 .venv/bin/python src/bridge.py

Endpoints (all JSON):
    GET  /health    liveness + uptime + version
    GET  /status    voice pipeline state: livekit configured (presence only,
                   never secret values), pipeline mode, wake threshold,
                   voice model present, WhatsApp CDP reachable, log stats
    GET  /actions   tail of ~/.jarvis/actions.log (?limit=1..200, default 50)
    GET  /sys       cpu load + memory + home-disk (Linux /proc; degraded
                   JSON elsewhere instead of crashing)
    GET  /config    sanitized config surface for a settings screen

Deliberately NOT here: mic mute/unmute, hangup, sendText, hotword events.
Those live inside the LiveKit room (token via frontend/app/api/token) and
the wake_client process, which have no out-of-process API yet. A shell that
needs them should drive them through its own LiveKit client, not this
bridge. Every endpoint is fail-soft: a missing file, binary, or socket
degrades one field, never the whole response.

Security: binds 127.0.0.1 only. If JARVIS_BRIDGE_TOKEN is set, all
endpoints require `Authorization: Bearer <token>` (constant-time compare).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse

VERSION = "0.1.0"
DEFAULT_PORT = 4317
_STARTED_AT = time.time()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _actions_log_path() -> Path:
    override = _env("JARVIS_ACTIONS_LOG")
    if override:
        return Path(override)
    return Path.home() / ".jarvis" / "actions.log"


def _voice_model_present() -> bool:
    model = _env("JARVIS_VOICE_MODEL", "")
    if model:
        return Path(model.split(":")[0]).expanduser().exists()
    return (Path.home() / ".jarvis" / "voices").exists()


def _whatsapp_reachable(timeout: float = 1.0) -> bool | None:
    """Probe the WhatSie CDP endpoint. None = probe not possible."""
    try:
        req = urllib.request.Request("http://127.0.0.1:9223/json/version")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _sys_stats() -> dict:
    stats: dict = {}
    try:
        with open("/proc/loadavg") as fh:
            stats["load_1_5_15"] = fh.read().split()[:3]
        mem: dict = {}
        with open("/proc/meminfo") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[0].rstrip(":") in (
                    "MemTotal",
                    "MemAvailable",
                ):
                    mem[parts[0].rstrip(":")] = int(parts[1]) * 1024
        stats["mem_bytes"] = mem
        du = os.statvfs(str(Path.home()))
        stats["home_free_bytes"] = du.f_bavail * du.f_frsize
    except OSError:
        stats["unsupported"] = True
    return stats


def _tail_log(path: Path, limit: int) -> list[str]:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            step, buf = 4096, b""
            while len(buf.splitlines()) <= limit and size > 0:
                step = min(step, size)
                size -= step
                fh.seek(size)
                buf = fh.read(step) + buf
                if size == 0:
                    break
        lines = buf.decode(errors="replace").splitlines()
    except (OSError, ValueError):
        return []
    return lines[-limit:]


def _log_stats() -> dict:
    path = _actions_log_path()
    try:
        st = path.stat()
        return {"path": str(path), "bytes": st.st_size, "mtime": int(st.st_mtime)}
    except OSError:
        return {"path": str(path), "bytes": 0, "mtime": 0}


def build_status() -> dict:
    return {
        "ok": True,
        "version": VERSION,
        "uptime_s": int(time.time() - _STARTED_AT),
        "pipeline": _env("JARVIS_PIPELINE", "realtime"),
        "agent_name": _env("AGENT_NAME", "my-agent"),
        "local_unlocked": _env("JARVIS_LOCAL") == "1",
        "livekit_configured": bool(
            _env("LIVEKIT_URL")
            and _env("LIVEKIT_API_KEY")
            and _env("LIVEKIT_API_SECRET")
        ),
        "needle_enabled": _env("JARVIS_NEEDLE", "0") != "0",
        "wake_threshold": _env("JARVIS_WAKE_THRESHOLD", "0.5"),
        "voice_model_present": _voice_model_present(),
        "whatsapp_reachable": _whatsapp_reachable(),
        "log": _log_stats(),
    }


class _Handler(BaseHTTPRequestHandler):
    token: str = ""

    def log_message(self, *args: object) -> None:  # keep voice logs clean
        pass

    def _authorized(self) -> bool:
        if not self.token:
            return True
        got = self.headers.get("Authorization", "")
        want = "Bearer " + self.token
        return (
            hashlib.sha256(got.encode()).digest()
            == hashlib.sha256(want.encode()).digest()
        )

    def _send(self, code: int, payload: dict | list) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # The Tauri webview (tauri://localhost) and the Next dev server
        # fetch the bridge cross-origin. Loopback-only server + no cookies:
        # a wildcard is safe here (auth rides the Authorization header,
        # which browsers allow under `*` outside credentialed mode).
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        """Preflight sink: GET-only API, so always allow + short-circuit."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if not self._authorized():
            self._send(401, {"ok": False, "error": "unauthorized"})
            return
        parsed = urlparse(self.path)
        route, qs = parsed.path, parse_qs(parsed.query)
        if route == "/health":
            self._send(
                200,
                {
                    "ok": True,
                    "version": VERSION,
                    "uptime_s": int(time.time() - _STARTED_AT),
                },
            )
        elif route == "/status":
            self._send(200, build_status())
        elif route == "/actions":
            try:
                limit = max(1, min(200, int(qs.get("limit", ["50"])[0])))
            except ValueError:
                limit = 50
            self._send(
                200, {"ok": True, "actions": _tail_log(_actions_log_path(), limit)}
            )
        elif route == "/sys":
            self._send(200, {"ok": True, **_sys_stats()})
        elif route == "/config":
            self._send(
                200,
                {
                    "ok": True,
                    "pipeline": _env("JARVIS_PIPELINE", "realtime"),
                    "wake_threshold": _env("JARVIS_WAKE_THRESHOLD", "0.5"),
                    "needle_enabled": _env("JARVIS_NEEDLE", "0") != "0",
                    "local_unlocked": _env("JARVIS_LOCAL") == "1",
                    "livekit_configured": bool(
                        _env("LIVEKIT_URL")
                        and _env("LIVEKIT_API_KEY")
                        and _env("LIVEKIT_API_SECRET")
                    ),
                    "bridge_token_set": bool(self.token),
                },
            )
        else:
            self._send(404, {"ok": False, "error": "unknown route"})


def create_server(port: int = DEFAULT_PORT, token: str = "") -> ThreadingHTTPServer:
    """Create (not start) a loopback-bound server. Port 0 = ephemeral."""

    class BoundHandler(_Handler):
        pass

    BoundHandler.token = token
    server = ThreadingHTTPServer(("127.0.0.1", port), BoundHandler)
    server.daemon_threads = True
    return server


def serve_forever(port: int = DEFAULT_PORT, token: str = "") -> None:
    server = create_server(port, token)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    try:
        port = int(_env("JARVIS_BRIDGE_PORT", str(DEFAULT_PORT)))
    except ValueError:
        port = DEFAULT_PORT
    serve_forever(port=port, token=_env("JARVIS_BRIDGE_TOKEN"))


if __name__ == "__main__":
    main()


def _run_in_thread(
    port: int = 0, token: str = ""
) -> tuple[ThreadingHTTPServer, Thread]:
    """Test helper: serve on an ephemeral port in a background thread."""
    server = create_server(port, token)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
