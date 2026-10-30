"""In-process fake of google-genai's live API for deterministic backend tests."""

from __future__ import annotations

import asyncio
from typing import Any

from google.genai import types


class FakeConn:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.closed = False

    async def send_realtime_input(self, **kw: Any) -> None:
        self.sent.append(("realtime", kw))

    async def send_client_content(self, **kw: Any) -> None:
        self.sent.append(("content", kw))

    async def send_tool_response(self, **kw: Any) -> None:
        self.sent.append(("tool_response", kw))

    async def receive(self):
        while True:
            item = await self.inbox.get()
            if isinstance(item, BaseException):
                raise item
            if item is None:
                return
            yield item

    # scripting helpers
    def push(self, **fields: Any) -> None:
        self.inbox.put_nowait(types.LiveServerMessage(**fields))

    def content(self, **sc: Any) -> None:
        self.push(server_content=types.LiveServerContent(**sc))

    def audio(self, data: bytes) -> None:
        self.content(
            model_turn=types.Content(
                parts=[
                    types.Part(
                        inline_data=types.Blob(
                            data=data, mime_type="audio/pcm;rate=24000"
                        )
                    )
                ]
            )
        )

    def kinds(self) -> list[str]:
        return [k for k, _ in self.sent]


class _Connect:
    def __init__(self, owner: FakeClient, model: str, config: Any) -> None:
        self.owner, self.model, self.config = owner, model, config

    async def __aenter__(self) -> FakeConn:
        self.owner.connects.append((self.model, self.config))
        fail = self.owner.fail_models.get(self.model)
        if fail is not None:
            raise fail
        conn = FakeConn()
        self.owner.conns.append(conn)
        return conn

    async def __aexit__(self, *exc: Any) -> None:
        self.owner.conns[-1].closed = True


class FakeClient:
    def __init__(self) -> None:
        self.connects: list[tuple[str, Any]] = []
        self.conns: list[FakeConn] = []
        self.fail_models: dict[str, BaseException] = {}
        self.aio = self
        self.live = self

    def connect(self, *, model: str, config: Any) -> _Connect:
        return _Connect(self, model, config)
