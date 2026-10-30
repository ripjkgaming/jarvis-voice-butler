"""Session event types and a small synchronous/async EventEmitter."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

log = logging.getLogger("jarvis_rt.events")

UserState = Literal["speaking", "listening", "away"]
AgentState = Literal["initializing", "idle", "listening", "thinking", "speaking"]


class CloseReason(str, Enum):
    ERROR = "error"
    JOB_SHUTDOWN = "job_shutdown"
    PARTICIPANT_DISCONNECTED = "participant_disconnected"
    USER_INITIATED = "user_initiated"
    TASK_COMPLETED = "task_completed"


@dataclass
class UserStateChangedEvent:
    old_state: UserState
    new_state: UserState
    type: str = "user_state_changed"
    created_at: float = field(default_factory=time.time)


@dataclass
class AgentStateChangedEvent:
    old_state: AgentState
    new_state: AgentState
    type: str = "agent_state_changed"
    created_at: float = field(default_factory=time.time)


@dataclass
class UserInputTranscribedEvent:
    transcript: str
    is_final: bool
    type: str = "user_input_transcribed"
    item_id: str | None = None
    speaker_id: str | None = None
    language: str | None = None
    created_at: float = field(default_factory=time.time)


@dataclass
class ConversationItemAddedEvent:
    item: Any
    type: str = "conversation_item_added"
    created_at: float = field(default_factory=time.time)


@dataclass
class FunctionToolsExecutedEvent:
    function_calls: list
    function_call_outputs: list
    type: str = "function_tools_executed"
    created_at: float = field(default_factory=time.time)

    def zipped(self) -> list[tuple[Any, Any]]:
        return list(zip(self.function_calls, self.function_call_outputs))


@dataclass
class SpeechCreatedEvent:
    user_initiated: bool
    source: Literal["say", "generate_reply"]
    speech_handle: Any
    type: str = "speech_created"
    created_at: float = field(default_factory=time.time)


@dataclass
class ErrorEvent:
    error: Any
    source: Any = None
    type: str = "error"
    created_at: float = field(default_factory=time.time)


@dataclass
class CloseEvent:
    reason: CloseReason
    error: Any = None
    type: str = "close"
    created_at: float = field(default_factory=time.time)


class EventEmitter:
    """on/off/once/emit; coroutine handlers are scheduled as tasks.

    Handler exceptions are logged and never reach the emitter.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[..., Any]]] = {}
        self._once: set[Callable[..., Any]] = set()

    def on(self, event: str, callback: Callable[..., Any] | None = None):
        def register(cb: Callable[..., Any]):
            self._handlers.setdefault(event, []).append(cb)
            return cb

        return register(callback) if callback is not None else register

    def once(self, event: str, callback: Callable[..., Any] | None = None):
        def register(cb: Callable[..., Any]):
            self._once.add(cb)
            return self.on(event, cb)

        return register(callback) if callback is not None else register

    def off(self, event: str, callback: Callable[..., Any]) -> None:
        handlers = self._handlers.get(event, [])
        if callback in handlers:
            handlers.remove(callback)
        self._once.discard(callback)

    def emit(self, event: str, *args: Any) -> None:
        for cb in list(self._handlers.get(event, ())):
            if cb in self._once:
                self.off(event, cb)
            try:
                result = cb(*args)
                if inspect.isawaitable(result):
                    task = asyncio.ensure_future(result)
                    task.add_done_callback(_log_task_error)
            except Exception:
                log.exception("handler for %s failed", event)


def _log_task_error(task: asyncio.Future) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("async handler failed", exc_info=task.exception())
