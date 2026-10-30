# Portions derived from livekit-agents (Copyright 2023 LiveKit, Inc.),
# licensed under the Apache License, Version 2.0.
"""Agent: instructions + tools + chat context, swappable on a live session."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .chat_context import ChatContext
from .tools import FunctionTool, RawFunctionTool, find_function_tools

if TYPE_CHECKING:
    from .session import AgentSession

NOT_GIVEN: Any = object()


def is_given(value: Any) -> bool:
    return value is not NOT_GIVEN


def _tool_id(tool: Any) -> str:
    return tool.id


class Agent:
    """Holds instructions, tools and chat context for one conversation role.

    Tools come from ``tools=[...]`` plus every ``@function_tool`` method on the
    instance. Duplicate tool names raise, as in the previous framework.
    """

    def __init__(
        self,
        *,
        instructions: str,
        id: str | None = None,  # noqa: A002
        chat_ctx: ChatContext | None = None,
        tools: list[Any] | None = None,
        llm: Any = NOT_GIVEN,
        stt: Any = NOT_GIVEN,
        tts: Any = NOT_GIVEN,
        vad: Any = NOT_GIVEN,
        turn_detection: Any = NOT_GIVEN,
        allow_interruptions: Any = NOT_GIVEN,
        min_endpointing_delay: Any = NOT_GIVEN,
        max_endpointing_delay: Any = NOT_GIVEN,
    ) -> None:
        tools = list(tools or [])
        self._id = id or (
            "default_agent" if type(self) is Agent else type(self).__name__
        )
        self._instructions = instructions
        self._llm = llm
        self._stt = stt
        self._tts = tts
        self._vad = vad
        self._turn_detection = turn_detection
        self._allow_interruptions = allow_interruptions
        self._min_endpointing_delay = min_endpointing_delay
        self._max_endpointing_delay = max_endpointing_delay
        self._session: AgentSession | None = None
        self._tools = self._unique_tools(tools + find_function_tools(self))
        self._chat_ctx = (
            chat_ctx.copy() if chat_ctx is not None else ChatContext.empty()
        )

    @staticmethod
    def _unique_tools(tools: list[Any]) -> list[Any]:
        seen: dict[str, Any] = {}
        for tool in tools:
            if not isinstance(tool, (FunctionTool, RawFunctionTool)):
                raise TypeError(f"Invalid tool type: {type(tool)}")
            key = _tool_id(tool)
            if key in seen and seen[key] is not tool:
                raise ValueError(f"duplicate function name: {key}")
            seen[key] = tool
        return list(seen.values())

    @property
    def id(self) -> str:
        return self._id

    @property
    def label(self) -> str:
        return f"{type(self).__module__}.{type(self).__name__}"

    @property
    def instructions(self) -> str:
        return self._instructions

    @property
    def tools(self) -> list[Any]:
        return list(self._tools)

    @property
    def chat_ctx(self) -> ChatContext:
        return self._chat_ctx

    @property
    def llm(self) -> Any:
        return self._llm

    @property
    def stt(self) -> Any:
        return self._stt

    @property
    def tts(self) -> Any:
        return self._tts

    @property
    def vad(self) -> Any:
        return self._vad

    @property
    def allow_interruptions(self) -> Any:
        return self._allow_interruptions

    @property
    def session(self) -> AgentSession:
        if self._session is None:
            raise RuntimeError("Agent is not running inside a session")
        return self._session

    async def update_instructions(self, instructions: str) -> None:
        self._instructions = instructions
        if self._session is not None:
            await self._session._agent_instructions_changed(self)

    async def update_tools(self, tools: list[Any]) -> None:
        self._tools = self._unique_tools(list(tools))
        if self._session is not None:
            await self._session._agent_tools_changed(self)

    async def update_chat_ctx(
        self, chat_ctx: ChatContext, *, exclude_invalid_function_calls: bool = True
    ) -> None:
        self._chat_ctx = chat_ctx.copy()
        if self._session is not None:
            await self._session._agent_chat_ctx_changed(self)

    async def on_enter(self) -> None:
        pass

    async def on_exit(self) -> None:
        pass

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: Any
    ) -> None:
        pass
