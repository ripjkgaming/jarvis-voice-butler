"""Run a chess session: game + games server (+ voice engine) + board window."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from games import window
from games.chess.game import ChessGame
from games.chess.settings import ChessSettings
from games.chess.settings import load as load_settings
from games.lock import GameLock
from games.server import PORT, UI_PORT, GamesServer, serve_ui

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_VOICE_UNAVAILABLE = 4
EXIT_INTERRUPTED = 130


@dataclass
class RunOptions:
    mode: str = "classic"  # classic | voice
    fen: str | None = None
    level: str | None = None
    colour: str | None = None
    time: str | None = None
    resume: bool = False
    spectate: bool = False
    listen: bool = False
    no_window: bool = False
    port: int = PORT
    ui_port: int = UI_PORT
    backend: str = "auto"
    ui_dir: Path | None = None
    watch_wake: bool = True
    engine: Any = None


class Runner:
    def __init__(
        self,
        opts: RunOptions,
        *,
        settings: ChessSettings | None = None,
        game: ChessGame | None = None,
    ):
        self.opts = opts
        self.settings = settings or load_settings()
        self.game = game or ChessGame(
            self.settings, engine=opts.engine, persist_settings=True
        )
        self.game.ui_mode = opts.mode
        self.server = GamesServer(self.game, port=opts.port)
        self.voice = None
        self.quit_event = asyncio.Event()
        self._httpd = None
        self.backend_used = "none"
        self._ever_connected = False

    async def start(self) -> None:
        o = self.opts
        g = self.game
        await g.start()
        if o.level:
            g.settings.difficulty = o.level
        if o.colour:
            g.settings.colour = o.colour
        if o.time:
            g.settings.time_control = o.time
        resumed = False
        if o.resume:
            resumed = await g.resume_last()
        if not resumed:
            await g.new_game(fen=o.fen)
        g.on_quit = self._quit
        self.server.handlers["listen"] = self._listen_cmd
        self.server.handlers["resume_listening"] = self._resume_cmd
        if o.mode == "voice":
            self.server.handlers["move"] = self._reject_pointer_move
        await self.server.start()
        d = o.ui_dir or window.ui_dir()
        if d is not None and not o.no_window:
            with contextlib.suppress(OSError):
                self._httpd = serve_ui(d, o.ui_port)
        if o.mode == "voice" or o.listen:
            await self.set_listen(True)
        if o.spectate:
            await g.command("spectate")
        if not o.no_window:
            self.backend_used = window.open_window(
                o.mode, port=o.ui_port, backend=o.backend
            )

    async def _quit(self) -> None:
        self.quit_event.set()

    def _reject_pointer_move(self, **_: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "say": None,
            "text": "Voice Mode is hands-free: say your move.",
            "kind": "voice_only",
            "data": {},
        }

    async def _listen_cmd(self, on: bool = True) -> dict[str, Any]:
        ok = await self.set_listen(bool(on))
        return {
            "ok": ok,
            "say": None,
            "text": None,
            "kind": "listen",
            "data": {"listening": self.game.listening},
        }

    async def _resume_cmd(self) -> dict[str, Any]:
        if self.voice:
            self.voice.manual_resume()
        return {"ok": True, "say": None, "text": None, "kind": "resume", "data": {}}

    async def set_listen(self, on: bool) -> bool:
        if on:
            if self.voice is not None:
                return True
            from games.voice.factory import build_voice_engine

            try:
                eng = build_voice_engine(self.game, watch_wake=self.opts.watch_wake)
                await eng.start()
            except Exception as exc:
                self.game.mic_state = "off"
                self.game.last_say = f"Voice unavailable: {exc}"
                self.game._changed_soon()
                return False
            self.voice = eng
            return True
        if self.voice is not None:
            await self.voice.stop()
            self.voice = None
            self.game.listening = False
            self.game.mic_state = "off"
            self.game._changed_soon()
        return True

    def window_gone(self) -> bool:
        """The board window connected once and every client has left."""
        n = len(self.server._clients)
        if n:
            self._ever_connected = True
        return self._ever_connected and n == 0

    async def wait(self, grace: float = 8.0) -> str:
        """Block until quit, Ctrl+C or the window closing. Returns the reason."""
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, stop.set)
        gone_since: float | None = None
        while True:
            if self.quit_event.is_set():
                return "quit"
            if stop.is_set():
                return "interrupted"
            if self.opts.no_window:
                pass
            elif self.window_gone():
                gone_since = gone_since or loop.time()
                if loop.time() - gone_since > grace:
                    return "window"
            else:
                gone_since = None
            await asyncio.sleep(0.2)

    async def stop(self) -> None:
        with contextlib.suppress(Exception):
            await self.set_listen(False)
        with contextlib.suppress(Exception):
            self.game._autosave()
        with contextlib.suppress(Exception):
            await self.server.stop()
        with contextlib.suppress(Exception):
            await self.game.close()
        if self._httpd is not None:
            with contextlib.suppress(Exception):
                self._httpd.shutdown()


async def run_session(
    opts: RunOptions, lock: GameLock | None = None
) -> tuple[int, dict]:
    """Full session lifecycle. Returns (exit code, summary for the outro)."""
    lock = lock or GameLock()
    if not lock.acquire():
        window.focus_window(opts.mode, port=opts.ui_port)
        print(
            "Jarvis Chess is already running — bringing the board forward.",
            file=sys.stderr,
        )
        return EXIT_OK, {}
    runner = Runner(opts)
    try:
        await runner.start()
        print("♞ Jarvis Chess running — press Ctrl+C here to quit.", flush=True)
        reason = await runner.wait()
        st = runner.game.state
        res = st.result()
        summary = {
            "result": res.outcome if res else "*",
            "moves": (st.move_count + 1) // 2,
            "reason": reason,
        }
        code = EXIT_INTERRUPTED if reason == "interrupted" else EXIT_OK
        return code, summary
    finally:
        await runner.stop()
        lock.release()
