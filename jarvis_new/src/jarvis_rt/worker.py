"""Local job runner replacing the LiveKit worker.

``AgentServer`` hosts the WebSocket voice hub. When a speaker connects to a
room that has no job yet, the registered entrypoint runs with a
``JobContext`` (room facade, shutdown callbacks). One job per room; the room
closes when the entrypoint returns.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import inspect
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .transport.server import HubRoom, VoiceHub
from .transport.tokens import FileNonceLedger, jarvis_home, load_secret

log = logging.getLogger("jarvis_rt.worker")

DEFAULT_PORT = 7880
_current_job: contextvars.ContextVar[JobContext | None] = contextvars.ContextVar(
    "jarvis_rt_job", default=None
)


@dataclass
class JobInfo:
    room: str
    metadata: str = ""
    agent_name: str = ""


class JobContext:
    def __init__(self, room: HubRoom, info: JobInfo) -> None:
        self.room = room
        self.job = info
        self.log_context_fields: dict[str, Any] = {}
        self._shutdown_callbacks: list[Callable[..., Any]] = []
        self._shutdown_done = False

    async def connect(self, **_: Any) -> None:
        """Already attached when the job starts; kept for call-site parity."""

    async def wait_for_participant(self, identity: str | None = None, **_: Any) -> Any:
        while True:
            for p in self.room.remote_participants.values():
                if identity is None or p.identity == identity:
                    return p
            await asyncio.sleep(0.05)

    def add_shutdown_callback(self, callback: Callable[..., Any]) -> None:
        self._shutdown_callbacks.append(callback)

    async def shutdown(self, reason: str = "") -> None:
        """Run every shutdown callback exactly once, newest first."""
        if self._shutdown_done:
            return
        self._shutdown_done = True
        for callback in reversed(self._shutdown_callbacks):
            try:
                result = callback() if _takes_no_args(callback) else callback(reason)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                log.exception("shutdown callback failed")


def _takes_no_args(callback: Callable[..., Any]) -> bool:
    try:
        sig = inspect.signature(callback)
    except (TypeError, ValueError):
        return True
    required = [
        p
        for p in sig.parameters.values()
        if p.default is p.empty
        and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    return not required


def get_job_context() -> JobContext:
    ctx = _current_job.get()
    if ctx is None:
        raise RuntimeError("no job context: not running inside a job")
    return ctx


@dataclass
class _Entry:
    fn: Callable[[JobContext], Awaitable[None]]
    agent_name: str
    options: dict[str, Any] = field(default_factory=dict)


class AgentServer:
    def __init__(self, **options: Any) -> None:
        self.options = options
        self._entries: dict[str, _Entry] = {}
        self._jobs: dict[str, asyncio.Task] = {}
        self.hub: VoiceHub | None = None

    def rtc_session(self, agent_name: str = "", **options: Any):
        def deco(fn: Callable[[JobContext], Awaitable[None]]):
            self._entries[agent_name] = _Entry(fn, agent_name, options)
            return fn

        return deco

    def _entry(self) -> _Entry | None:
        name = os.environ.get("AGENT_NAME", "my-agent")
        return self._entries.get(name) or next(iter(self._entries.values()), None)

    def active_call_exists(self) -> bool:
        return any(not t.done() for t in self._jobs.values())

    def _write_active(self) -> None:
        """Publish whether a call is live so other processes (wake client,
        mint_token.active_call_exists) can see it."""
        import json
        import os
        import tempfile

        path = jarvis_home() / "voice_active.json"
        data = {
            "active": self.active_call_exists(),
            "rooms": sorted(self._jobs),
            "pid": os.getpid(),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".voice_active")
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh)
            os.replace(tmp, path)
        except OSError:
            log.warning("could not write voice_active.json", exc_info=True)

    def build_hub(self) -> VoiceHub:
        home = jarvis_home()
        ledger = FileNonceLedger(home / "voice_nonces")
        hub = VoiceHub(load_secret(home), ledger=ledger)
        hub.on_join = self._on_join
        self.hub = hub
        return hub

    def _on_join(self, room: HubRoom, client: Any) -> None:
        if client.mode != "speaker" or room.name in self._jobs:
            return
        entry = self._entry()
        if entry is None:
            return
        info = JobInfo(
            room=room.name,
            metadata=client.attrs.get("jarvis.metadata", ""),
            agent_name=entry.agent_name,
        )
        self._jobs[room.name] = asyncio.get_running_loop().create_task(
            self._run_job(entry, room, info)
        )
        self._write_active()

    async def _run_job(self, entry: _Entry, room: HubRoom, info: JobInfo) -> None:
        ctx = JobContext(room, info)
        token = _current_job.set(ctx)
        gone = asyncio.Event()
        if not room.isconnected():
            gone.set()
        room.on("disconnected", gone.set)
        main = asyncio.get_running_loop().create_task(entry.fn(ctx))
        gone_wait = asyncio.get_running_loop().create_task(gone.wait())
        try:
            # A job lives until its speaker leaves, like the former room job:
            # the entrypoint usually returns once the session is started, and a
            # setup step stuck on a dead session must not keep the call "active".
            await asyncio.wait({main, gone_wait}, return_when=asyncio.FIRST_COMPLETED)
            if main.done() and not main.cancelled() and main.exception() is not None:
                log.error(
                    "job for room %s crashed", room.name, exc_info=main.exception()
                )
            elif main.done():
                await gone.wait()
        except asyncio.CancelledError:
            raise
        finally:
            for task in (main, gone_wait):
                if not task.done():
                    task.cancel()
            await asyncio.gather(main, gone_wait, return_exceptions=True)
            room.off("disconnected", gone.set)
            _current_job.reset(token)
            await ctx.shutdown("job_finished")
            with contextlib.suppress(Exception):
                await room.close()
            self._jobs.pop(room.name, None)
            self._write_active()

    async def serve(self, host: str = "127.0.0.1", port: int | None = None) -> Any:
        hub = self.build_hub()
        self._write_active()  # clear any "active" left by a crashed worker
        listen = (
            port
            if port is not None
            else int(os.environ.get("JARVIS_VOICE_PORT", DEFAULT_PORT))
        )
        return await hub.serve(host, listen)


def run_app(server: AgentServer) -> None:
    """``python agent.py <console|dev|start>``: serve the voice hub until stopped."""
    mode = sys.argv[1] if len(sys.argv) > 1 else "dev"
    if mode == "download-files":
        return
    logging.basicConfig(level=logging.DEBUG if mode == "dev" else logging.INFO)

    async def main() -> None:
        listener = await server.serve()
        log.info("voice hub listening (%s)", mode)
        try:
            await asyncio.Event().wait()
        finally:
            listener.close()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
