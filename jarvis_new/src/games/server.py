"""Loopback games server: ws://127.0.0.1:4320 (state snapshots + commands).

Port note: the brief said 4318, but Jarvis's mic-uplink already listens on
TCP 4318 (see shell manager.rs), so chess uses 4320 (UI on 4321).


Voice, mouse and keyboard all call the same ``ChessGame.command`` API. The
same process can also serve the static board UI over HTTP for the
chess-only shell. Loopback only; no auth because nothing leaves the machine.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import http.server
import json
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, serve

HOST = "127.0.0.1"
PORT = 4320
UI_PORT = 4321

_ALLOWED_ORIGIN_PREFIXES = (
    "http://127.0.0.1",
    "http://localhost",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
)


def origin_ok(origin: str | None) -> bool:
    """Block web pages from other sites; allow the board UI and tools."""
    if not origin:
        return True
    return origin.startswith(_ALLOWED_ORIGIN_PREFIXES)


class GamesServer:
    def __init__(self, game: Any, host: str = HOST, port: int = PORT):
        self.game = game
        self.host = host
        self.port = port
        self._clients: set[ServerConnection] = set()
        self._server = None
        self._ticker: asyncio.Task | None = None
        self._dirty = asyncio.Event()
        self._pump: asyncio.Task | None = None
        self.handlers: dict[str, Callable[..., Awaitable[dict] | dict]] = {}
        game.on_change = self.notify

    # ----------------------------------------------------------- lifecycle
    async def start(self) -> None:
        self._server = await serve(
            self._handle,
            self.host,
            self.port,
            origins=None,
            process_request=self._process_request,
            max_size=2**20,
        )
        if self.port == 0:
            self.port = self._server.sockets[0].getsockname()[1]
        self._pump = asyncio.ensure_future(self._pump_loop())
        self._ticker = asyncio.ensure_future(self._tick_loop())

    async def stop(self) -> None:
        for t in (self._pump, self._ticker):
            if t:
                t.cancel()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for c in list(self._clients):
            await c.close()

    def _process_request(self, connection: ServerConnection, request):  # type: ignore[no-untyped-def]
        origin = request.headers.get("Origin")
        if not origin_ok(origin):
            return connection.respond(403, "forbidden origin\n")
        return None

    # ------------------------------------------------------------ messaging
    def notify(self) -> None:
        self._dirty.set()

    async def _pump_loop(self) -> None:
        """Coalesce bursts of changes into one snapshot per ~30 ms."""
        while True:
            await self._dirty.wait()
            await asyncio.sleep(0.03)
            self._dirty.clear()
            await self.broadcast()

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            with contextlib.suppress(Exception):  # never let the ticker die
                await self.game.tick()
            st = self.game.state
            if self._clients and (st.clock.running and not st.clock.paused):
                self.notify()

    def _snapshot_msg(self) -> str:
        return json.dumps({"type": "snapshot", "data": self.game.snapshot()})

    async def broadcast(self) -> None:
        if not self._clients:
            return
        msg = self._snapshot_msg()
        await asyncio.gather(*(self._send(c, msg) for c in list(self._clients)))

    async def _send(self, c: ServerConnection, msg: str) -> None:
        try:
            await c.send(msg)
        except Exception:
            self._clients.discard(c)

    async def _handle(self, ws: ServerConnection) -> None:
        self._clients.add(ws)
        try:
            await ws.send(self._snapshot_msg())
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("type") != "command":
                    continue
                reply = await self.dispatch(msg.get("name", ""), msg.get("args") or {})
                await self._send(
                    ws, json.dumps({"type": "reply", "id": msg.get("id"), **reply})
                )
                self.notify()
        except Exception:
            pass
        finally:
            self._clients.discard(ws)

    async def dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name in self.handlers:
            out = self.handlers[name](**args)
            if asyncio.iscoroutine(out):
                out = await out
            return out
        try:
            return await self.game.command(name, **args)
        except Exception as exc:  # a bad command must not drop the board
            return {
                "ok": False,
                "say": None,
                "text": f"error: {exc}",
                "kind": "error",
                "data": {},
            }


# ------------------------------------------------------------------ static UI
def serve_ui(
    directory: Path, port: int = UI_PORT, host: str = HOST
) -> http.server.ThreadingHTTPServer:
    """Serve the exported board UI (daemon thread). Used by the chess-only shell."""

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:  # silence
            pass

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

    handler = functools.partial(Handler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True, name="chess-ui").start()
    return httpd
