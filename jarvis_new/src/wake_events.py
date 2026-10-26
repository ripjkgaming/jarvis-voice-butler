"""Wake-event broadcast: tells other local apps when Jarvis wakes and hangs up.

``~/.jarvis/wake-events.sock`` is a Unix socket of newline-delimited JSON,
any number of subscribers, strictly best-effort: publishing never blocks or
raises into the wake loop. Events: ``{"event": "wake", "ts": ...}`` and
``{"event": "call_ended", "ts": ...}``. LiveKit-free (chess subscribes).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import queue
import socket
import threading
import time
from pathlib import Path

logger = logging.getLogger("jarvis-wake-events")


def wake_events_path() -> Path:
    """``$JARVIS_HOME/wake-events.sock`` (default ``~/.jarvis``). Pure."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    base = Path(home) if home else Path.home() / ".jarvis"
    return base / "wake-events.sock"


def encode_event(
    event: str,
    ts: float | None = None,
    character: str | None = None,
    char_turn: int | None = None,
) -> bytes:
    payload: dict = {"event": event, "ts": ts if ts is not None else time.time()}
    if character is not None:
        payload["character"] = character
    if char_turn is not None:
        payload["char_turn"] = char_turn
    return (json.dumps(payload) + "\n").encode()


class WakeEventBroadcaster:
    """Accepts subscribers on a Unix socket and fans events out to them."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._subs: list[socket.socket] = []
        self._lock = threading.Lock()
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=64)
        self._server: socket.socket | None = None
        self._started = False

    @property
    def path(self) -> Path:
        return self._path or wake_events_path()

    def start(self) -> bool:
        """Bind and serve in daemon threads. False if the socket is unavailable."""
        if self._started:
            return True
        path = self.path
        with contextlib.suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(path))
        except OSError:
            if _socket_live(str(path)):
                server.close()
                return False
            with contextlib.suppress(OSError):
                path.unlink()
            try:
                server.bind(str(path))
            except OSError as exc:
                logger.warning("wake events socket unavailable: %s", exc)
                server.close()
                return False
        server.listen(8)
        self._server = server
        self._started = True
        threading.Thread(
            target=self._accept_loop, name="wake-events-accept", daemon=True
        ).start()
        threading.Thread(
            target=self._send_loop, name="wake-events-send", daemon=True
        ).start()
        return True

    def stop(self) -> None:
        self._started = False
        if self._server:
            with contextlib.suppress(OSError):
                self._server.close()
        with self._lock:
            for s in self._subs:
                with contextlib.suppress(OSError):
                    s.close()
            self._subs.clear()
        with contextlib.suppress(OSError):
            self.path.unlink()

    def publish(self, event: str, ts: float | None = None) -> None:
        """Queue an event. Never blocks, never raises."""
        with contextlib.suppress(queue.Full, Exception):
            self._queue.put_nowait(encode_event(event, ts))

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def _accept_loop(self) -> None:
        assert self._server is not None
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            conn.settimeout(0.5)
            with self._lock:
                self._subs.append(conn)

    def _send_loop(self) -> None:
        while self._started:
            try:
                data = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            with self._lock:
                subs = list(self._subs)
            dead = []
            for s in subs:
                try:
                    s.sendall(data)
                except OSError:
                    dead.append(s)
            if dead:
                with self._lock:
                    for s in dead:
                        with contextlib.suppress(ValueError, OSError):
                            self._subs.remove(s)
                            s.close()


def _socket_live(path: str) -> bool:
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(0.3)
    try:
        probe.connect(path)
    except OSError:
        return False
    finally:
        probe.close()
    return True
