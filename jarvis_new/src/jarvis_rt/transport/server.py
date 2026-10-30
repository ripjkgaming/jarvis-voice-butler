"""Server side of the voice WebSocket transport (docs/voice-transport.md).

``VoiceHub.handle(ws, path_query)`` authenticates a call token and serves one
connection; ``HubRoom`` is a Room-compatible facade for in-process agent code.
Every outbound client has its own bounded queue and writer task, so a slow
client never blocks the producer or other clients.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import inspect
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from websockets.exceptions import ConnectionClosed

from .frames import (
    BARGE_MAX_BYTES,
    BARGE_TOPIC,
    KIND_AUDIO,
    KIND_MIC,
    KIND_VIDEO,
    decode_binary,
    decode_text,
    decode_video,
    encode_binary,
    encode_text,
)
from .tokens import (
    TokenError,
    valid_room_name,
    verify_call_token,
)

AUDIO_OUT_BYTES_PER_SEC = 48000  # 24 kHz mono PCM16
AUDIO_LEAD_SECONDS = 0.2
MAX_OBSERVERS = 16
CHAT_MAX_CHARS = 8000
ROOM_CLOSED = 4011
CONTROL_TOPICS = frozenset({"hello", "flush", "pong"})
_AUDIO_SLICE_BYTES = 32 * 1024
_VIDEO_QUEUE = 8
#: Topics a speaker may publish to the server (delivered via HubRoom.on_topic).
INBOUND_TOPICS = frozenset({"jarvis.turn", BARGE_TOPIC})


log = logging.getLogger("jarvis_rt.transport")


@dataclass
class _Item:
    cls: str  # "control" | "telemetry" | "audio"
    data: str | bytes
    gen: int


async def _close(ws: Any, code: int, reason: str) -> None:
    with contextlib.suppress(Exception):
        await ws.close(code, reason)


class _Client:
    def __init__(
        self,
        ws: Any,
        identity: str,
        mode: str,
        room: HubRoom,
        hub: VoiceHub,
        attrs: dict[str, str] | None = None,
    ) -> None:
        self.ws = ws
        self.attrs = dict(attrs or {})
        self.disconnect_reason: str | None = None
        self.identity = identity
        self.mode = mode
        self.room = room
        self._hub = hub
        self.queue: collections.deque[_Item] = collections.deque()
        self._counts = {"control": 0, "telemetry": 0, "audio": 0}
        self._audio_bytes = 0
        self.generation = 0
        self._next_audio_at = 0.0
        self._wake = asyncio.Event()
        self._writer: asyncio.Task | None = None
        self._closed = False

    def start(self) -> None:
        self._writer = asyncio.create_task(self._run_writer())

    async def stop(self) -> None:
        self._closed = True
        if self._writer is not None:
            self._writer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._writer

    async def force_close(self, code: int, reason: str) -> None:
        self._closed = True
        await _close(self.ws, code, reason)

    def _drop_first(self, cls: str) -> None:
        for index, item in enumerate(self.queue):
            if item.cls == cls:
                del self.queue[index]
                self._counts[cls] -= 1
                if cls == "audio":
                    self._audio_bytes -= len(item.data)
                return

    def enqueue(self, cls: str, data: str | bytes) -> None:
        """Synchronous and never awaits: the producer can never be stalled."""
        if self._closed:
            return
        stats = self._hub.stats
        if cls == "audio":
            size = len(data)
            while (
                self._audio_bytes + size > self._hub.audio_limit_bytes
                and self._counts["audio"]
            ):
                self._drop_first("audio")
                stats["dropped_audio"] += 1
        elif cls == "telemetry":
            while self._counts["telemetry"] >= self._hub.telemetry_limit:
                self._drop_first("telemetry")
                stats["dropped_telemetry"] += 1
        elif self._counts["control"] >= self._hub.control_limit:
            stats["control_overflow"] += 1
            spawn(self.force_close(1011, "control overflow"))
            return
        self.queue.append(_Item(cls, data, self.generation))
        self._counts[cls] += 1
        if cls == "audio":
            self._audio_bytes += len(data)
        self._wake.set()

    def flush(self) -> None:
        """Drop queued playback now; in-flight audio is discarded by generation."""
        self.queue = collections.deque(i for i in self.queue if i.cls != "audio")
        self._counts["audio"] = 0
        self._audio_bytes = 0
        self.generation += 1
        self._next_audio_at = 0.0
        self.enqueue("control", encode_text("flush", None))

    async def _run_writer(self) -> None:
        try:
            while True:
                while not self.queue:
                    self._wake.clear()
                    await self._wake.wait()
                item = self.queue.popleft()
                self._counts[item.cls] -= 1
                if item.cls == "audio":
                    self._audio_bytes -= len(item.data)
                    await self._pace(item)
                    if item.gen != self.generation:
                        continue
                    self._next_audio_at = (
                        max(self._next_audio_at, time.monotonic())
                        + len(item.data) / AUDIO_OUT_BYTES_PER_SEC
                    )
                await asyncio.wait_for(self.ws.send(item.data), self._hub.send_timeout)
                self._hub.stats["sent"] += 1
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, ConnectionClosed, OSError):
            await self.force_close(1011, "send failed")

    async def _pace(self, item: _Item) -> None:
        """Server-side pacing: keep audio at most AUDIO_LEAD_SECONDS ahead."""
        ahead = self._next_audio_at - time.monotonic() - AUDIO_LEAD_SECONDS
        if ahead > 0:
            await asyncio.sleep(ahead)


class _LocalParticipant:
    def __init__(self, room: HubRoom) -> None:
        self._room = room
        self.identity = room.identity

    async def publish_data(
        self, payload: str | bytes, topic: str = "", reliable: bool = True
    ) -> None:
        if isinstance(payload, (bytes, bytearray)):
            if topic == BARGE_TOPIC and len(payload) > BARGE_MAX_BYTES:
                raise ValueError("barge packet exceeds 256 bytes")
            try:
                text = bytes(payload).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("payload is not UTF-8") from exc
        elif isinstance(payload, str):
            text = payload
        else:
            raise TypeError("payload must be str or bytes")
        try:
            value: Any = json.loads(text)
        except ValueError:
            value = text
        self._room._broadcast(topic, encode_text(topic, value))


class Participant:
    """Remote party in a room (identity, token attributes, close reason)."""

    def __init__(self, client: _Client) -> None:
        self._client = client
        self.identity = client.identity
        self.mode = client.mode

    @property
    def attributes(self) -> dict[str, str]:
        return self._client.attrs

    @property
    def disconnect_reason(self) -> str | None:
        return self._client.disconnect_reason


class HubRoom:
    """Room-compatible facade: one per room name, owned by a VoiceHub."""

    def __init__(self, hub: VoiceHub, name: str) -> None:
        self.name = name
        self.identity = "jarvis"
        self.local_participant = _LocalParticipant(self)
        self.video: collections.deque[tuple[int, bytes]] = collections.deque(
            maxlen=_VIDEO_QUEUE
        )
        self._hub = hub
        self._clients: list[_Client] = []
        self._mic: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._mic_callbacks: list[Callable[[bytes], Any]] = []
        self._chat_callbacks: list[Callable[[str], Any]] = []
        self._topic_callbacks: dict[str, list[Callable[[Any], Any]]] = {}
        self._closed = False
        self._events: dict[str, list[Callable[..., Any]]] = {}
        self._participants: dict[str, Participant] = {}

    # -- membership -------------------------------------------------------
    @property
    def speaker(self) -> _Client | None:
        return next((c for c in self._clients if c.mode == "speaker"), None)

    @property
    def observers(self) -> list[_Client]:
        return [c for c in self._clients if c.mode == "observer"]

    @property
    def identities(self) -> set[str]:
        return {c.identity for c in self._clients}

    @property
    def remote_participants(self) -> dict[str, Participant]:
        return dict(self._participants)

    def on(self, event: str, callback: Callable[..., Any]) -> Callable[..., Any]:
        self._events.setdefault(event, []).append(callback)
        return callback

    def off(self, event: str, callback: Callable[..., Any]) -> None:
        handlers = self._events.get(event, [])
        if callback in handlers:
            handlers.remove(callback)

    def _emit(self, event: str, *args: Any) -> None:
        for callback in list(self._events.get(event, ())):
            try:
                _invoke(callback, *args)
            except Exception:
                log.exception("room handler for %s failed", event)

    def _add(self, client: _Client) -> None:
        self._clients.append(client)
        participant = self._participants[client.identity] = Participant(client)
        self._emit("participant_connected", participant)

    def _remove(self, client: _Client) -> None:
        if client in self._clients:
            self._clients.remove(client)
        participant = self._participants.pop(client.identity, None)
        if participant is not None:
            self._emit("participant_disconnected", participant)
        if client.mode == "speaker":
            self._emit("disconnected")

    def isconnected(self) -> bool:
        return not self._closed and self.speaker is not None

    # -- inbound ----------------------------------------------------------
    async def mic_frames(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._mic.get()
            if item is None:
                self._mic.put_nowait(None)  # Wake any other iterator too.
                return
            yield item

    def on_mic(self, callback: Callable[[bytes], Any]) -> None:
        self._mic_callbacks.append(callback)

    def on_chat(self, callback: Callable[[str], Any]) -> None:
        self._chat_callbacks.append(callback)

    def on_topic(self, topic: str, callback: Callable[[Any], Any]) -> None:
        """Receive speaker-sent decoded payloads for an inbound topic."""
        if topic not in INBOUND_TOPICS:
            raise ValueError(f"topic {topic!r} is not inbound")
        self._topic_callbacks.setdefault(topic, []).append(callback)

    def _deliver_mic(self, payload: bytes) -> None:
        self._mic.put_nowait(payload)
        for callback in list(self._mic_callbacks):
            _invoke(callback, payload)

    def _deliver_video(self, ts_ms: int, jpeg: bytes) -> None:
        self.video.append((ts_ms, jpeg))

    def _deliver_chat(self, text: str) -> None:
        for callback in list(self._chat_callbacks):
            _invoke(callback, text)

    def _deliver_topic(self, topic: str, payload: Any) -> None:
        for callback in list(self._topic_callbacks.get(topic, ())):
            try:
                _invoke(callback, payload)
            except Exception:
                log.exception("topic handler for %s failed", topic)

    # -- outbound ---------------------------------------------------------
    def _broadcast(self, topic: str, text: str) -> None:
        cls = "control" if topic in CONTROL_TOPICS else "telemetry"
        for client in list(self._clients):
            client.enqueue(cls, text)

    def send_audio(self, pcm24k: bytes) -> None:
        if len(pcm24k) % 2:
            raise ValueError("PCM16 audio needs an even byte count")
        if self._closed:
            return
        for start in range(0, len(pcm24k), _AUDIO_SLICE_BYTES):
            frame = encode_binary(
                KIND_AUDIO, bytes(pcm24k[start : start + _AUDIO_SLICE_BYTES])
            )
            for client in list(self._clients):
                client.enqueue("audio", frame)

    def flush_playback(self) -> None:
        for client in list(self._clients):
            client.flush()

    def send_text(self, topic: str, payload: Any) -> None:
        self._broadcast(topic, encode_text(topic, payload))

    async def close(self, code: int = ROOM_CLOSED) -> None:
        if self._closed:
            return
        self._closed = True
        self._mic.put_nowait(None)
        clients = list(self._clients)
        await asyncio.gather(
            *(c.force_close(code, "room closed") for c in clients),
            return_exceptions=True,
        )


_BACKGROUND: set[asyncio.Future[Any]] = set()


def spawn(awaitable: Any) -> None:
    """Fire-and-forget with a strong reference so the task is not collected."""
    task = asyncio.ensure_future(awaitable)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


def _invoke(callback: Callable[..., Any], *args: Any) -> None:
    result = callback(*args)
    if inspect.isawaitable(result):
        spawn(result)


_REASONS = {
    1000: "CLIENT_INITIATED",
    1001: "CLIENT_INITIATED",
    4010: "CLIENT_INITIATED",
    4011: "ROOM_CLOSED",
    4012: "DUPLICATE_IDENTITY",
    4013: "PARTICIPANT_REMOVED",
}


def _reason_for(code: int | None) -> str:
    return _REASONS.get(code or 0, "CONNECTION_LOST")


class VoiceHub:
    """Authenticates and serves voice WebSocket calls for many rooms."""

    def __init__(
        self,
        secret: bytes,
        *,
        ledger: Any = None,
        clock: Callable[[], float] = time.time,
        max_observers: int = MAX_OBSERVERS,
        telemetry_limit: int = 256,
        audio_limit_bytes: int = 4 * 1024 * 1024,
        control_limit: int = 64,
        send_timeout: float = 5.0,
    ) -> None:
        self._secret = secret
        self._ledger = ledger
        self._clock = clock
        self._max_observers = max_observers
        self.telemetry_limit = telemetry_limit
        self.audio_limit_bytes = audio_limit_bytes
        self.control_limit = control_limit
        self.send_timeout = send_timeout
        self._rooms: dict[str, HubRoom] = {}
        #: Called as ``on_join(room, client)`` for every accepted connection.
        self.on_join: Callable[..., Any] | None = None
        self.stats: collections.Counter[str] = collections.Counter()

    def room(self, name: str) -> HubRoom:
        if not valid_room_name(name):
            raise ValueError("invalid room name")
        room = self._rooms.get(name)
        if room is None:
            room = self._rooms[name] = HubRoom(self, name)
        return room

    async def serve(self, host: str = "127.0.0.1", port: int = 0) -> Any:
        from websockets.asyncio.server import serve as ws_serve

        async def _handler(ws: Any) -> None:
            await self.handle(ws, ws.request.path)

        return await ws_serve(_handler, host, port, max_size=1 << 20)

    async def handle(self, ws: Any, path_query: str) -> None:
        parts = urlsplit(path_query)
        if parts.path != "/voice":
            await _close(ws, 1008, "not found")
            return
        tokens = parse_qs(parts.query).get("token", [])
        if len(tokens) != 1:
            await _close(ws, 4001, "bad token")
            return
        try:
            claims = verify_call_token(
                tokens[0], self._secret, now=self._clock(), ledger=self._ledger
            )
        except TokenError as exc:
            await _close(ws, exc.code, type(exc).__name__)
            return
        room = self.room(claims.room)
        if claims.mode == "speaker" and room.speaker is not None:
            await _close(ws, 4003, "call already active")
            return
        if claims.mode == "observer" and len(room.observers) >= self._max_observers:
            await _close(ws, 1013, "observer limit")
            return
        if claims.identity in room.identities:
            await _close(ws, 4012, "duplicate identity")
            return
        client = _Client(ws, claims.identity, claims.mode, room, self, claims.attrs)
        room._add(client)
        if self.on_join is not None:
            try:
                _invoke(self.on_join, room, client)
            except Exception:
                log.exception("on_join failed")
        client.start()
        client.enqueue(
            "control",
            encode_text(
                "hello",
                {"identity": claims.identity, "room": claims.room, "mode": claims.mode},
            ),
        )
        try:
            async for message in ws:
                if not await self._on_message(client, room, message):
                    break
        except ConnectionClosed:
            pass
        finally:
            client.disconnect_reason = _reason_for(getattr(ws, "close_code", None))
            room._remove(client)
            await client.stop()

    async def _on_message(self, client: _Client, room: HubRoom, message: Any) -> bool:
        """Return False when the connection must end (close already sent)."""
        if isinstance(message, bytes):
            if client.mode == "observer":
                await _close(client.ws, 4004, "observer may not send")
                return False
            decoded = decode_binary(message)
            if decoded is None:
                return True
            kind, payload = decoded
            if kind == KIND_MIC:
                room._deliver_mic(payload)
            elif kind == KIND_VIDEO:
                parts = decode_video(payload)
                if parts is not None:
                    room._deliver_video(*parts)
            else:
                self.stats["dropped_kind"] += 1
            return True
        decoded_text = decode_text(message)
        if decoded_text is None:
            return True
        topic, payload = decoded_text
        if client.mode == "observer" and topic not in {"ping", "pong"}:
            await _close(client.ws, 4004, "observer may not send")
            return False
        if topic == "ping":
            client.enqueue("control", encode_text("pong", None))
        elif topic == "lk.chat":
            text = payload.get("text") if isinstance(payload, dict) else None
            if isinstance(text, str) and 0 < len(text) <= CHAT_MAX_CHARS:
                room._deliver_chat(text)
            else:
                self.stats["dropped_malformed"] += 1
        elif topic in INBOUND_TOPICS:
            # Barge packets are size/shape checked by frames.decode_text.
            if topic == "jarvis.turn" and not (
                isinstance(payload, dict) and payload.get("type") == "jarvis_turn"
            ):
                self.stats["dropped_malformed"] += 1
            else:
                room._deliver_topic(topic, payload)
        elif topic != "pong":
            self.stats["dropped_forbidden_topic"] += 1
        return True
