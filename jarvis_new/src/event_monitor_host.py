"""Roomless, model-free event agent; durable text acceptance, never speech proof."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from event_monitor.core import Session


class SDKEngine:
    """Actual SDK lifecycle without a job, console, room, providers or tools."""

    def __init__(self):
        self.session = None

    async def open(self, identity, messages):
        from livekit.agents import Agent, AgentSession, cli, get_job_context

        if (
            get_job_context(required=False) is not None
            or cli.AgentsConsole.get_instance().enabled
        ):
            raise RuntimeError("Event agent must run outside voice jobs/console")
        await self.aclose()
        session = AgentSession(
            llm=None,
            stt=None,
            tts=None,
            vad=None,
            user_away_timeout=None,
            tools=[],
            mcp_servers=[],
        )
        self.session = session
        try:
            await session.start(
                agent=Agent(
                    instructions="Public event ledger. No actions or replies.",
                    llm=None,
                    stt=None,
                    tts=None,
                    vad=None,
                    tools=[],
                ),
                record=False,
                session_host=False,
            )
            session.input.set_audio_enabled(False)
            session.input.set_video_enabled(False)
            session.output.set_audio_enabled(False)
            session.output.set_video_enabled(False)
            assert session.input.audio is None and session.output.audio is None
            for message in messages:
                await self.put(message)
        except BaseException:
            await self.aclose()
            raise

    async def put(self, message):
        ctx = self.session.current_agent.chat_ctx.copy()
        text = json.dumps(
            {
                "event_key": message["key"],
                "title": message["title"],
                "facts": message["detail"],
            },
            ensure_ascii=True,
        )
        ctx.add_message(role="assistant", content=text)
        # Bound the SDK context, independent of monitor lifetime.
        if len(ctx.items) > 20:
            ctx.items[:] = ctx.items[-20:]
        await self.session.current_agent.update_chat_ctx(ctx)

    async def replace(self, messages):
        ctx = self.session.current_agent.chat_ctx.copy()
        # Preserve SDK instructions/config records while replacing event projection.
        ctx.items[:] = [
            item for item in ctx.items if getattr(item, "role", None) != "assistant"
        ]
        for message in messages[-20:]:
            ctx.add_message(
                role="assistant",
                content=json.dumps(
                    {
                        "event_key": message["key"],
                        "title": message["title"],
                        "facts": message["detail"],
                    },
                    ensure_ascii=True,
                ),
            )
        await self.session.current_agent.update_chat_ctx(ctx)

    async def aclose(self):
        session, self.session = self.session, None
        if session is not None:
            await session.aclose()


class LoopEngine:
    """Lazy owned loop. All SDK operations finish or cancel within a deadline."""

    def __init__(self):
        self.loop = None
        self.thread = None
        self.engine = SDKEngine()

    def _call(self, fn, *args):
        if self.loop is None:
            ready = threading.Event()

            def run():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self.loop = loop
                ready.set()
                loop.run_forever()
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                loop.close()

            self.thread = threading.Thread(
                target=run, name="monitor-text-agent", daemon=True
            )
            self.thread.start()
            if not ready.wait(2):
                raise TimeoutError("Event loop startup deadline")

        async def bounded():
            return await asyncio.wait_for(fn(*args), 2)

        future = asyncio.run_coroutine_threadsafe(bounded(), self.loop)
        try:
            return future.result(3)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError("Event SDK deadline") from None

    def start(self, identity, messages):
        self._call(self.engine.open, identity, messages)

    def append(self, message):
        self._call(self.engine.put, message)

    def replace(self, messages):
        self._call(self.engine.replace, messages)

    def close(self):
        if self.loop is not None:
            try:
                self._call(self.engine.aclose)
            finally:
                self.loop.call_soon_threadsafe(self.loop.stop)
                self.thread.join(3)
                if self.thread.is_alive():
                    raise TimeoutError("Event SDK cleanup deadline")
                self.loop = self.thread = None


class EventHost:
    """One bounded desktop event session. Persist acceptance before context projection.

    Host guard returns a nonempty authority epoch string, or None. Guard and SDK
    callbacks must not reenter the monitor store: dispatch holds its write lock.
    """

    def __init__(self, path, *, guard, engine=None, clock=time.time, processor=None):
        self.path, self.guard, self.clock = Path(path), guard, clock
        self.engine = engine or LoopEngine()
        self.processor = processor
        self.lock = threading.RLock()
        self.active = None
        self.projected_keys = []
        self.epoch = None
        self.deadline = 0
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if (
            self.path.parent.stat().st_uid != os.getuid()
            or self.path.parent.stat().st_mode & 0o077
        ):
            raise PermissionError("Private owner host directory required")
        if self.path.is_symlink():
            raise PermissionError("Host state symlink refused")
        existed = self.path.exists()
        if existed and (
            self.path.stat().st_uid != os.getuid() or self.path.stat().st_mode & 0o077
        ):
            raise PermissionError("Existing host state must be private")
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS receipts (key TEXT PRIMARY KEY,owner TEXT,scope TEXT,identity TEXT,title TEXT,detail TEXT,accepted REAL)"
            )
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS starts (key TEXT PRIMARY KEY,owner TEXT,scope TEXT,identity TEXT,epoch TEXT,expires REAL)"
            )
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS retired (key TEXT PRIMARY KEY)")
        if not existed:
            os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _epoch(self):
        try:
            value = self.guard()
            return value if type(value) is str and 0 < len(value) <= 256 else None
        except Exception:  # noqa: BLE001 - fail closed at injected host/SDK boundaries
            return None

    def provider(self):
        with self.lock:
            epoch = self._epoch()
            if self.active and (
                not epoch or epoch != self.epoch or self.clock() >= self.deadline
            ):
                self.engine.close()
                self.active = self.epoch = None
                self.projected_keys = []
            return self.active if epoch else None

    def start(self, key, owner, scope):
        with self.lock:
            if owner != "owner" or scope != "desktop" or not self._epoch():
                return None
            if not isinstance(key, str) or not 0 < len(key) <= 256:
                return None
            current = self.provider()
            epoch = self._epoch()
            now = self.clock()
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM starts WHERE expires<?", (now - 8 * 86400,))
                row = db.execute("SELECT * FROM starts WHERE key=?", (key,)).fetchone()
                if row:
                    if (
                        row["owner"] != owner
                        or row["scope"] != scope
                        or row["epoch"] != epoch
                        or row["expires"] <= now
                    ):
                        return None
                    identity, deadline = row["identity"], row["expires"]
                    if current and current.identity != identity:
                        return None
                else:
                    if db.execute("SELECT count(*) FROM starts").fetchone()[0] >= 5000:
                        return None
                    identity = (
                        current.identity
                        if current
                        else "event-session-" + uuid.uuid4().hex
                    )
                    deadline = self.deadline if current else now + 300
                    db.execute(
                        "INSERT INTO starts VALUES(?,?,?,?,?,?)",
                        (key, owner, scope, identity, epoch, deadline),
                    )
            if current:
                return current
            history = self.history(owner)
            self.engine.start(identity, history)
            if epoch != self._epoch():
                self.engine.close()
                return None
            self.active = Session(owner, scope, identity, True, False, False)
            self.epoch = epoch
            self.deadline = deadline
            self.projected_keys = [r["key"] for r in history]
            return self.active

    def sink(self, key, session, signal):
        with self.lock:
            if (
                self.provider() != session
                or session.owner != "owner"
                or session.scope != "desktop"
            ):
                return False
            result = self.processor.get(key) if self.processor else None
            if self.processor and (
                not result
                or result["state"] != "completed"
                or result["task"] == "ignore"
            ):
                return False
            message = {"key": key, "title": signal.title, "detail": signal.detail}
            if result:
                message["title"] = (result["mode"] + ": " + result["summary"])[:256]
            if (
                not isinstance(key, str)
                or len(key) > 256
                or len(message["title"]) > 256
                or len(message["detail"]) > 100000
            ):
                return False
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "DELETE FROM receipts WHERE accepted<?", (self.clock() - 8 * 86400,)
                )
                db.execute(
                    "DELETE FROM retired WHERE key NOT IN (SELECT key FROM receipts)"
                )
                if db.execute("SELECT 1 FROM retired WHERE key=?", (key,)).fetchone():
                    return False
                old = db.execute(
                    "SELECT * FROM receipts WHERE key=?", (key,)
                ).fetchone()
                if old:
                    return (
                        old["owner"] == session.owner
                        and old["scope"] == session.scope
                        and old["title"] == message["title"]
                        and old["detail"] == signal.detail
                    )
                if (
                    self.provider() != session
                    or db.execute("SELECT count(*) FROM receipts").fetchone()[0] >= 5000
                ):
                    return False
                db.execute(
                    "INSERT INTO receipts VALUES(?,?,?,?,?,?,?)",
                    (
                        key,
                        session.owner,
                        session.scope,
                        session.identity,
                        message["title"],
                        message["detail"],
                        self.clock(),
                    ),
                )
            # Durable acceptance is authoritative; projection can recover from history.
            try:
                self.engine.append(message)
                self.projected_keys = (self.projected_keys + [key])[-20:]
            except Exception:  # noqa: BLE001 - fail closed at injected host/SDK boundaries
                self.engine.close()
                self.active = self.epoch = None
                self.projected_keys = []
            return True

    def history(self, owner):
        with self.db() as db:
            rows = db.execute(
                "SELECT key,title,detail FROM receipts WHERE owner=? AND key NOT IN (SELECT key FROM retired) ORDER BY accepted DESC,key DESC LIMIT 20",
                (owner,),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def view(self, owner):
        if owner != "owner":
            return {"mode": "text-event-agent", "active": False, "events": []}
        with self.db() as db:
            rows = db.execute(
                "SELECT key,title,detail FROM receipts WHERE owner=? ORDER BY accepted DESC,key DESC LIMIT 20",
                (owner,),
            ).fetchall()
        return {
            "mode": "text-event-agent",
            "active": bool(
                self.active
                and self.clock() < self.deadline
                and self._epoch() == self.epoch
            ),
            "events": [dict(r) for r in reversed(rows)],
            "acceptance": "durable-text-only",
            "audio": False,
            "models": False,
        }

    def dispatch(self, store, now, *, quiet=None):
        return store.dispatch(
            now,
            self.provider,
            self.sink,
            quiet=quiet,
            start_session=self.start,
            live_effects=True,
            activation_guard=lambda owner, scope: (
                owner == "owner" and scope == "desktop" and bool(self._epoch())
            ),
        )

    def cancel_keys(self, keys):
        # Runtime maps monitor IDs to exact durable batch keys. Receipts associate
        # every accepted key with its generation, including reused-session accepts.
        with self.lock:
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                db.executemany(
                    "INSERT OR IGNORE INTO retired SELECT key FROM receipts WHERE key=?",
                    [(key,) for key in keys],
                )
                if not self.active:
                    return
                started = db.execute(
                    "SELECT key FROM starts WHERE identity=?", (self.active.identity,)
                ).fetchall()
                affected = bool(set(self.projected_keys) & keys)
                if not affected:
                    if not self.projected_keys and any(
                        r["key"] in keys for r in started
                    ):
                        self.close()
                    return
                remaining = [key for key in self.projected_keys if key not in keys]
                messages = {
                    key: dict(
                        db.execute(
                            "SELECT key,title,detail FROM receipts WHERE key=?", (key,)
                        ).fetchone()
                    )
                    for key in remaining
                }
            if not remaining:
                self.close()
                return
            try:
                self.engine.replace([messages[key] for key in remaining])
                self.projected_keys = remaining
            except Exception:  # noqa: BLE001 - failed projection drains; definitions/results survive
                self.close()

    def close(self):
        with self.lock:
            self.engine.close()
            self.active = self.epoch = None
            self.projected_keys = []
