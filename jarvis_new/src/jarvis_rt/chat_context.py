# Copyright 2023 LiveKit, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Port of livekit/agents/llm/chat_context.py without any livekit dependency.
# Provider conversion is limited to google-genai (`to_gemini_contents`).

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, Field, TypeAdapter
from typing_extensions import TypedDict

if TYPE_CHECKING:
    from google.genai import types as genai_types

__all__ = [
    "AgentConfigUpdate",
    "AgentHandoff",
    "AudioContent",
    "ChatContent",
    "ChatContext",
    "ChatItem",
    "ChatMessage",
    "ChatRole",
    "FunctionCall",
    "FunctionCallOutput",
    "ImageContent",
    "MetricsReport",
]


def _shortuuid(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:12]


_EXPR_TAG_RE = re.compile(r"<expr\b[^>]*/>")


def _strip_expr_markup(text: str) -> str:
    """Remove LiveKit's ``<expr/>`` expressive tags (only that dialect)."""
    return _EXPR_TAG_RE.sub("", text).strip()


class ImageContent(BaseModel):
    """Image input for the chat context. ``image`` is a URL or data URL string."""

    id: str = Field(default_factory=lambda: _shortuuid("img_"))
    type: Literal["image_content"] = Field(default="image_content")
    image: str
    mime_type: str | None = None


class AudioContent(BaseModel):
    type: Literal["audio_content"] = Field(default="audio_content")
    frame: list[Any] = Field(default_factory=list)
    transcript: str | None = None


ChatRole: TypeAlias = Literal["developer", "system", "user", "assistant"]


class MetricsReport(TypedDict, total=False):
    started_speaking_at: float
    stopped_speaking_at: float
    transcription_delay: float
    end_of_turn_delay: float
    on_user_turn_completed_delay: float
    llm_node_ttft: float
    llm_node_tps: float
    llm_node_ttfs: float
    tts_node_ttfb: float
    playback_latency: float
    e2e_latency: float
    provider_request_ids: list[str]
    llm_metadata: dict[str, str]
    tts_metadata: dict[str, str]
    stt_metadata: dict[str, str]


class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: _shortuuid("item_"))
    type: Literal["message"] = "message"
    role: ChatRole
    content: list[ChatContent]
    interrupted: bool = False
    transcript_confidence: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    metrics: MetricsReport = Field(default_factory=lambda: MetricsReport())
    created_at: float = Field(default_factory=time.time)

    @property
    def text_content(self) -> str | None:
        """Text of the message; ``<expr/>`` tags are stripped from assistant messages.

        Multiple text parts are joined by a newline.
        """
        raw = self.raw_text_content
        if raw is None or self.role != "assistant":
            return raw
        return _strip_expr_markup(raw)

    @property
    def raw_text_content(self) -> str | None:
        """Text of the message exactly as stored, joined by a newline."""
        text_parts = [c for c in self.content if isinstance(c, str)]
        if not text_parts:
            return None
        return "\n".join(text_parts)


ChatContent: TypeAlias = ImageContent | AudioContent | str


class FunctionCall(BaseModel):
    id: str = Field(default_factory=lambda: _shortuuid("item_"))
    type: Literal["function_call"] = "function_call"
    call_id: str
    arguments: str
    name: str
    created_at: float = Field(default_factory=time.time)
    extra: dict[str, Any] = Field(default_factory=dict)
    group_id: str | None = None


class FunctionCallOutput(BaseModel):
    id: str = Field(default_factory=lambda: _shortuuid("item_"))
    type: Literal["function_call_output"] = Field(default="function_call_output")
    name: str = Field(default="")
    call_id: str
    output: str
    is_error: bool
    created_at: float = Field(default_factory=time.time)
    reply_required: bool = Field(default=True)


class AgentHandoff(BaseModel):
    id: str = Field(default_factory=lambda: _shortuuid("item_"))
    type: Literal["agent_handoff"] = Field(default="agent_handoff")
    old_agent_id: str | None = None
    new_agent_id: str
    created_at: float = Field(default_factory=time.time)


class AgentConfigUpdate(BaseModel):
    id: str = Field(default_factory=lambda: _shortuuid("item_"))
    type: Literal["agent_config_update"] = Field(default="agent_config_update")
    instructions: str | None = None
    tools_added: list[str] | None = None
    tools_removed: list[str] | None = None
    created_at: float = Field(default_factory=time.time)


ChatItem = Annotated[
    ChatMessage | FunctionCall | FunctionCallOutput | AgentHandoff | AgentConfigUpdate,
    Field(discriminator="type"),
]

_FUNCTION_TYPES = ("function_call", "function_call_output")


def _tool_name(tool: Any) -> str:
    if isinstance(tool, str):
        return tool
    name = getattr(tool, "name", None)
    if name is None:
        info = getattr(tool, "info", None)
        name = getattr(info, "name", None)
    return str(name)


class ChatContext:
    def __init__(self, items: Sequence[ChatItem] | None = None):
        self._items: list[ChatItem] = list(items) if items is not None else []

    @classmethod
    def empty(cls) -> ChatContext:
        return cls([])

    @property
    def items(self) -> list[ChatItem]:
        return self._items

    @items.setter
    def items(self, items: list[ChatItem]) -> None:
        self._items = items

    def messages(self) -> list[ChatMessage]:
        """Return only chat messages, ignoring function calls, outputs, and other events."""
        return [item for item in self._items if isinstance(item, ChatMessage)]

    def add_message(
        self,
        *,
        role: ChatRole,
        content: list[ChatContent] | str,
        id: str | None = None,  # noqa: A002 - mirrors livekit's keyword name
        interrupted: bool | None = None,
        created_at: float | None = None,
        metrics: MetricsReport | None = None,
        extra: dict[str, Any] | None = None,
    ) -> ChatMessage:
        kwargs: dict[str, Any] = {}
        if id is not None:
            kwargs["id"] = id
        if interrupted is not None:
            kwargs["interrupted"] = interrupted
        if created_at is not None:
            kwargs["created_at"] = created_at
        if metrics is not None:
            kwargs["metrics"] = metrics
        if extra is not None:
            kwargs["extra"] = extra

        content_list: list[ChatContent] = (
            [content] if isinstance(content, str) else list(content)
        )
        message = ChatMessage(role=role, content=content_list, **kwargs)

        if created_at is not None:
            self._items.insert(
                self.find_insertion_index(created_at=created_at), message
            )
        else:
            self._items.append(message)
        return message

    def insert(self, item: ChatItem | Iterable[ChatItem]) -> None:
        """Insert an item or list of items into the chat context by creation time."""
        items = [item] if isinstance(item, BaseModel) else list(item)
        for _item in items:
            idx = self.find_insertion_index(created_at=_item.created_at)
            self._items.insert(idx, _item)

    def remove(self, item: ChatItem | str) -> None:
        """Remove the first item by ChatItem or item ID. Raises ValueError if not found."""
        idx = self.index_by_id(item.id if not isinstance(item, str) else item)
        if idx is None:
            raise ValueError(f"Item not found: {item!r}")
        self._items.pop(idx)

    def get_by_id(self, item_id: str) -> ChatItem | None:
        return next((item for item in self.items if item.id == item_id), None)

    def index_by_id(self, item_id: str) -> int | None:
        return next(
            (i for i, item in enumerate(self.items) if item.id == item_id), None
        )

    def copy(
        self,
        *,
        exclude_function_call: bool = False,
        exclude_instructions: bool = False,
        exclude_empty_message: bool = False,
        exclude_handoff: bool = False,
        exclude_config_update: bool = False,
        tools: Sequence[Any] | None = None,
    ) -> ChatContext:
        valid_tools = {_tool_name(t) for t in tools} if tools else None

        items: list[ChatItem] = []
        for item in self.items:
            if exclude_function_call and item.type in _FUNCTION_TYPES:
                continue
            if (
                exclude_instructions
                and item.type == "message"
                and item.role in ("system", "developer")
            ):
                continue
            if exclude_empty_message and item.type == "message" and not item.content:
                continue
            if exclude_handoff and item.type == "agent_handoff":
                continue
            if exclude_config_update and item.type == "agent_config_update":
                continue
            if (
                valid_tools is not None
                and item.type in _FUNCTION_TYPES
                and item.name not in valid_tools
            ):
                continue
            items.append(item)

        return ChatContext(items)

    def truncate(self, *, max_items: int) -> ChatContext:
        """Keep only the last ``max_items`` items, in place.

        Leading function call items are dropped so the context never starts with an
        orphaned call/output. The first system/developer message is kept at the front.
        """
        if len(self._items) <= max_items:
            return self

        instructions = next(
            (
                item
                for item in self._items
                if item.type == "message" and item.role in ("system", "developer")
            ),
            None,
        )

        new_items = self._items[-max_items:] if max_items > 0 else []
        while new_items and new_items[0].type in _FUNCTION_TYPES:
            new_items.pop(0)

        if instructions and not any(item.id == instructions.id for item in new_items):
            new_items.insert(0, instructions)

        self._items[:] = new_items
        return self

    def merge(
        self,
        other_chat_ctx: ChatContext,
        *,
        exclude_function_call: bool = False,
        exclude_instructions: bool = False,
        exclude_config_update: bool = False,
    ) -> ChatContext:
        """Add items from ``other_chat_ctx`` without duplicates, keeping created_at order."""
        existing_ids = {item.id for item in self._items}

        for item in other_chat_ctx.items:
            if exclude_function_call and item.type in _FUNCTION_TYPES:
                continue
            if (
                exclude_instructions
                and item.type == "message"
                and item.role in ("system", "developer")
            ):
                continue
            if exclude_config_update and item.type == "agent_config_update":
                continue
            if item.id not in existing_ids:
                self._items.insert(
                    self.find_insertion_index(created_at=item.created_at), item
                )
                existing_ids.add(item.id)

        return self

    def to_dict(
        self,
        *,
        exclude_image: bool = True,
        exclude_audio: bool = True,
        exclude_timestamp: bool = True,
        exclude_function_call: bool = False,
        exclude_metrics: bool = False,
        exclude_config_update: bool = False,
        strip_markup: bool = False,
    ) -> dict[str, Any]:
        items: list[ChatItem] = []
        for item in self.items:
            if exclude_function_call and item.type in _FUNCTION_TYPES:
                continue
            if exclude_config_update and item.type == "agent_config_update":
                continue

            if item.type == "message":
                item = item.model_copy()
                if exclude_image:
                    item.content = [
                        c for c in item.content if not isinstance(c, ImageContent)
                    ]
                if exclude_audio:
                    item.content = [
                        c for c in item.content if not isinstance(c, AudioContent)
                    ]
                if strip_markup and item.role == "assistant":
                    item.content = [
                        _strip_expr_markup(c) if isinstance(c, str) else c
                        for c in item.content
                    ]

            items.append(item)

        exclude_fields: set[str] = set()
        if exclude_timestamp:
            exclude_fields.add("created_at")
        if exclude_metrics:
            exclude_fields.add("metrics")

        return {
            "items": [
                item.model_dump(mode="json", exclude_none=True, exclude=exclude_fields)
                for item in items
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChatContext:
        item_adapter = TypeAdapter(list[ChatItem])
        return cls(item_adapter.validate_python(data["items"]))

    @property
    def readonly(self) -> bool:
        return False

    def find_insertion_index(self, *, created_at: float) -> int:
        """Index to insert an item by creation time (after the last item with created_at <= t)."""
        for i in reversed(range(len(self._items))):
            if self._items[i].created_at <= created_at:
                return i + 1
        return 0

    def is_equivalent(self, other: ChatContext) -> bool:
        """True if both contexts hold the same items with matching essential fields.

        Timestamps and other metadata are ignored.
        """
        if self is other:
            return True
        if len(self.items) != len(other.items):
            return False

        for a, b in zip(self.items, other.items, strict=False):
            if a.id != b.id or a.type != b.type:
                return False
            if a.type == "message" and b.type == "message":
                if (
                    a.role != b.role
                    or a.interrupted != b.interrupted
                    or a.content != b.content
                ):
                    return False
            elif a.type == "function_call" and b.type == "function_call":
                if (
                    a.name != b.name
                    or a.call_id != b.call_id
                    or a.arguments != b.arguments
                ):
                    return False
            elif (
                a.type == "function_call_output"
                and b.type == "function_call_output"
                and (
                    a.name != b.name
                    or a.call_id != b.call_id
                    or a.output != b.output
                    or a.is_error != b.is_error
                )
            ):
                return False
        return True

    def to_gemini_contents(self) -> list[genai_types.Content]:
        """Convert to google-genai ``Content`` objects.

        ``user`` messages map to role ``user``, ``assistant`` to role ``model``.
        System/developer messages are excluded (pass them as system instruction).
        Handoffs and config updates are skipped.
        """
        from google.genai import types

        contents: list[types.Content] = []
        for item in self.items:
            if item.type == "message":
                if item.role not in ("user", "assistant"):
                    continue
                parts = [types.Part(text=c) for c in item.content if isinstance(c, str)]
                if not parts:
                    continue
                role = "user" if item.role == "user" else "model"
                contents.append(types.Content(role=role, parts=parts))
            elif item.type == "function_call":
                args = _parse_args(item.arguments)
                part = types.Part.from_function_call(name=item.name, args=args)
                contents.append(types.Content(role="model", parts=[part]))
            elif item.type == "function_call_output":
                response: dict[str, Any] = (
                    {"error": item.output} if item.is_error else {"output": item.output}
                )
                part = types.Part.from_function_response(
                    name=item.name, response=response
                )
                contents.append(types.Content(role="user", parts=[part]))
        return contents


def _parse_args(arguments: str) -> dict[str, Any]:
    try:
        parsed = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
