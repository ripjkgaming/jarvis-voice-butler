"""Content-free, room/session-owned HUD liveness and execution telemetry."""

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import time
import uuid
import weakref
from pathlib import Path

STATES = {"idle", "listening", "thinking", "speaking"}
MAX_AGE_S = 12
HEARTBEAT_S = 2
_monitors = weakref.WeakKeyDictionary()


def state_path(room=None):
    base = Path(os.environ.get("JARVIS_HOME") or Path.home() / ".jarvis")
    if room is None:
        return base / "voice_state.json"  # latest-session diagnostics / legacy reader
    key = hashlib.sha256(str(room).encode()).hexdigest()[:24]
    return base / "voice_states" / f"{key}.json"


def display_state(agent, user, executing=0):
    if agent not in ("listening", "thinking", "speaking"):
        return "idle"
    if executing or agent == "thinking":
        return "thinking"
    # Quiet listening is still an ACTIVE CALL; both listening and speech are purple.
    return "speaking" if agent == "speaking" else "listening"


class VoiceStatePublisher:
    """Older session callbacks/cleanup cannot overwrite the same room's new owner."""

    def __init__(self, room):
        self.room = room
        self.owner = uuid.uuid4().hex
        self.agent = "initializing"
        self.user = "listening"
        self.executing = 0
        self.closed = False
        self.owned = True
        self._write(claim=True)

    def _write(self, claim=False):
        data = {
            "room": self.room,
            "owner": self.owner,
            "agent_state": self.agent,
            "user_state": self.user,
            "executing": self.executing,
            "state": display_state(self.agent, self.user, self.executing),
            "live": not self.closed
            and self.agent in ("listening", "thinking", "speaking"),
            "ts": time.time(),
        }
        try:
            path = state_path(self.room)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.with_suffix(".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if not claim:
                    current = json.loads(path.read_text())
                    if current.get("owner") != self.owner:
                        self.owned = False
                        return
                tmp = path.with_suffix(f".{os.getpid()}.tmp")
                tmp.write_text(json.dumps(data))
                tmp.replace(path)
            # Preserve the existing read-only diagnostic without making it
            # the source of truth for unrelated simultaneous rooms.
            latest = state_path()
            with latest.with_suffix(".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if claim or (
                    latest.exists()
                    and json.loads(latest.read_text()).get("owner") == self.owner
                ):
                    tmp = latest.with_suffix(f".{os.getpid()}.tmp")
                    tmp.write_text(json.dumps(data))
                    tmp.replace(latest)
        except (OSError, TypeError, ValueError, AttributeError):
            pass

    def update(self, *, agent=None, user=None, executing=None):
        if self.closed or not self.owned:
            return
        if agent is not None:
            self.agent = str(agent)
        if user is not None:
            self.user = str(user)
        if executing is not None:
            self.executing = max(0, int(executing))
        self._write()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.agent, self.user, self.executing = "closed", "away", 0
        self._write()


def read_snapshot(room):
    if not room:
        return None
    with contextlib.suppress(OSError, KeyError, TypeError, ValueError, AttributeError):
        path = state_path(room)
        data = json.loads((path if path.exists() else state_path()).read_text())
        age = time.time() - float(data["ts"])
        if data["room"] == room and data["state"] in STATES and 0 <= age < MAX_AGE_S:
            # Recompute from SDK facts, including old telemetry during upgrade.
            live = data.get("agent_state") in ("listening", "thinking", "speaking")
            return {
                **data,
                "live": live,
                "state": display_state(
                    data.get("agent_state"),
                    data.get("user_state"),
                    data.get("executing", 0),
                ),
            }
    return None


def read(room):
    data = read_snapshot(room)
    return data["state"] if data else None


class VoiceStateMonitor:
    """One emitter and heartbeat per AgentSession; closure is terminal."""

    def __init__(self, session, room):
        self.session = session
        self.room = room
        self.publisher = VoiceStatePublisher(room.name)
        self.executing = 0
        self.task = None
        self.seen_connected = False
        self.listeners = [
            (session, "agent_state_changed", self.refresh),
            (session, "user_state_changed", self.refresh),
            (session, "close", self.close),
            (room, "disconnected", self.close),
        ]
        for emitter, event, callback in self.listeners:
            emitter.on(event, callback)
        _monitors[session] = self
        self.refresh()

    def refresh(self, *_):
        self.publisher.update(
            agent=getattr(self.session, "agent_state", "initializing"),
            user=getattr(self.session, "user_state", "listening"),
            executing=self.executing,
        )

    async def heartbeat(self):
        try:
            while not self.publisher.closed and self.publisher.owned:
                # The monitor is installed before ctx.connect()/session.start().
                # A room which has never joined is initializing, not disconnected.
                if self.room.isconnected():
                    self.seen_connected = True
                elif self.seen_connected:
                    break
                self.refresh()
                await asyncio.sleep(HEARTBEAT_S)
        finally:
            self.close()

    def start(self):
        self.task = asyncio.create_task(self.heartbeat())
        return self.task

    def close(self, *_):
        self.publisher.close()
        for emitter, event, callback in self.listeners:
            emitter.off(event, callback)
        self.listeners.clear()
        if _monitors.get(self.session) is self:
            del _monitors[self.session]
        if (
            self.task is not None
            and not self.task.done()
            and self.task is not asyncio.current_task()
        ):
            self.task.cancel()


def execution_pending(session):
    """Actual work remains active even without an installed HUD monitor."""
    return bool(getattr(session, "_jarvis_executing", 0))


#: Scopes still held, per asyncio task that entered them (see release_task).
_held = weakref.WeakKeyDictionary()


def _current_task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _release(token):
    session, monitor, released = token
    if released:
        return
    token[2] = True
    if session is not None:
        session._jarvis_executing = max(0, getattr(session, "_jarvis_executing", 0) - 1)
    if monitor:
        monitor.executing = max(0, monitor.executing - 1)
        monitor.refresh()


def release_task(task):
    """Force-release every scope ``task`` still holds (a hung, abandoned tool).

    The scope's own exit later becomes a no-op, so nothing is released twice.
    Returns how many scopes were released.
    """
    tokens = (_held.pop(task, None) or []) if task is not None else []
    for token in tokens:
        _release(token)
    return len(tokens)


@contextlib.contextmanager
def execution_scope(session):
    """Actual tool lifetime outranks SDK speech; errors/cancellation release it."""
    monitor = _monitors.get(session) if session is not None else None
    if session is not None:
        session._jarvis_executing = getattr(session, "_jarvis_executing", 0) + 1
    if monitor:
        monitor.executing += 1
        monitor.refresh()
    token = [session, monitor, False]
    task = _current_task()
    if task is not None:
        _held.setdefault(task, []).append(token)
    try:
        yield
    finally:
        if task is not None:
            with contextlib.suppress(KeyError, ValueError):
                _held[task].remove(token)
        _release(token)
