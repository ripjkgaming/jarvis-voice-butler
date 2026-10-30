"""Client side of the voice WebSocket transport (mirrors server.py).

Close codes 4001-4013 are terminal (no retry). Any other drop reconnects with
exponential backoff. Call tokens are single use, so the token may be a callable
that mints a fresh token for every connection attempt.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import random
from collections import deque
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import quote

from websockets.asyncio.client import connect
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    InvalidHandshake,
)

from .frames import (
    KIND_AUDIO,
    KIND_MIC,
    decode_binary,
    decode_text,
    encode_binary,
    encode_text,
    encode_video,
)
from .server import CHAT_MAX_CHARS, spawn

FATAL_CODES = frozenset(range(4001, 4014))
_CLIENT_CLOSE_CODE = 4010
_MAX_MESSAGE = 1 << 20


class VoiceClosedError(Exception):
    """Server closed the call with a terminal close code (no retry)."""

    def __init__(self, code: int, reason: str = "") -> None:
        super().__init__(f"voice call closed: {code} {reason}".strip())
        self.code = code
        self.reason = reason


class VoiceClient:
    def __init__(
        self,
        url: str,
        token: str | Callable[[], str],
        *,
        backoff_base: float = 0.5,
        backoff_max: float = 15.0,
        audio_queue_limit: int = 4096,
        open_timeout: float = 10.0,
        rng: random.Random | None = None,
    ) -> None:
        self._url = url
        self._token = token
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._audio_limit = audio_queue_limit
        self._open_timeout = open_timeout
        self._rng = rng or random.Random()
        self._ws: Any = None
        self._attempt = 0
        self._closing = False
        self._stopped = False
        self._task: asyncio.Task | None = None
        self._audio: deque[bytes] = deque()
        self._audio_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        self._topic_callbacks: dict[str, list[Callable[[Any], Any]]] = {}
        self.hello: dict[str, Any] | None = None
        self.connected = False
        self.last_close_code: int | None = None
        self.dropped_audio = 0

    # -- lifecycle --------------------------------------------------------
    def _connect_url(self) -> str:
        token = self._token() if callable(self._token) else self._token
        return f"{self._url}?token={quote(token, safe='')}"

    async def run(self) -> None:
        try:
            while not self._closing:
                code, reason = await self._connect_once()
                self.last_close_code = code
                if self._closing:
                    return
                if code is not None and code in FATAL_CODES:
                    raise VoiceClosedError(code, reason)
                await self._backoff()
        finally:
            self._stopped = True
            self._audio_event.set()

    async def _connect_once(self) -> tuple[int | None, str]:
        code: int | None = None
        reason = ""
        try:
            async with connect(
                self._connect_url(),
                open_timeout=self._open_timeout,
                max_size=_MAX_MESSAGE,
            ) as ws:
                self._ws = ws
                try:
                    async for message in ws:
                        self._dispatch(message)
                except ConnectionClosedError as exc:
                    if exc.rcvd is not None:
                        code, reason = exc.rcvd.code, exc.rcvd.reason
                    else:
                        code = 1006
                else:
                    code, reason = ws.close_code, ws.close_reason or ""
        except (OSError, asyncio.TimeoutError, InvalidHandshake, ConnectionClosed):
            code = None
        finally:
            self._ws = None
            self.connected = False
            self._connected_event.clear()
        return code, reason

    async def _backoff(self) -> None:
        delay = min(self._backoff_max, self._backoff_base * (2**self._attempt))
        delay = min(self._backoff_max, delay * (0.5 + self._rng.random()))
        self._attempt += 1
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), delay)

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self.run())
        return self._task

    async def wait_connected(self, timeout: float | None = None) -> None:
        await asyncio.wait_for(self._connected_event.wait(), timeout)

    async def close(self) -> None:
        self._closing = True
        self._stop_event.set()
        ws = self._ws
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close(_CLIENT_CLOSE_CODE, "client requested close")
        if self._task is not None:
            await asyncio.wait({self._task}, timeout=5)
        self._stopped = True
        self._audio_event.set()

    # -- inbound ----------------------------------------------------------
    def _dispatch(self, message: Any) -> None:
        if isinstance(message, bytes):
            decoded = decode_binary(message)
            if decoded is not None and decoded[0] == KIND_AUDIO:
                self._push_audio(decoded[1])
            return
        decoded_text = decode_text(message)
        if decoded_text is None:
            return
        topic, payload = decoded_text
        if topic == "hello" and isinstance(payload, dict):
            self.hello = payload
            self.connected = True
            self._attempt = 0
            self._connected_event.set()
        elif topic == "flush":
            self._audio.clear()
        for callback in list(self._topic_callbacks.get(topic, ())):
            result = callback(payload)
            if inspect.isawaitable(result):
                spawn(result)

    def _push_audio(self, pcm: bytes) -> None:
        if len(self._audio) >= self._audio_limit:
            self._audio.popleft()
            self.dropped_audio += 1
        self._audio.append(pcm)
        self._audio_event.set()

    async def audio_frames(self) -> AsyncIterator[bytes]:
        """Agent audio chunks (PCM16 mono 24 kHz) in arrival order."""
        while True:
            while not self._audio:
                if self._stopped:
                    return
                self._audio_event.clear()
                await self._audio_event.wait()
            yield self._audio.popleft()

    def on_topic(self, topic: str, callback: Callable[[Any], Any]) -> None:
        self._topic_callbacks.setdefault(topic, []).append(callback)

    # -- outbound ---------------------------------------------------------
    async def send_mic(self, pcm: bytes) -> bool:
        if not self.connected or self._ws is None:
            return False
        frame = encode_binary(KIND_MIC, pcm)
        try:
            await self._ws.send(frame)
        except ConnectionClosed:
            return False
        return True

    async def send_video(self, ts_ms: int, jpeg: bytes) -> bool:
        if not self.connected or self._ws is None:
            return False
        try:
            await self._ws.send(encode_video(ts_ms, jpeg))
        except ConnectionClosed:
            return False
        return True

    async def send_chat(self, text: str) -> bool:
        text = text.strip()
        if not 0 < len(text) <= CHAT_MAX_CHARS:
            raise ValueError("chat text must be 1..8000 characters")
        return await self._send_text("lk.chat", {"text": text})

    async def ping(self) -> bool:
        return await self._send_text("ping", None)

    async def _send_text(self, topic: str, payload: Any) -> bool:
        if not self.connected or self._ws is None:
            return False
        try:
            await self._ws.send(encode_text(topic, payload))
        except ConnectionClosed:
            return False
        return True
