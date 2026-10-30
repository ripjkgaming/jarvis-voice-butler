"""AgentSession: turn state machine, tool loop and handoffs over a model backend.

The backend (``jarvis_rt.live`` Gemini Live client, or the direct pipeline)
reports what happened; the session owns state, events, chat history,
tool execution and agent swaps. Event names and payloads match the
framework this replaces.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Protocol

from .agent import Agent
from .chat_context import ChatContext, ChatMessage, FunctionCall
from .events import (
    AgentStateChangedEvent,
    CloseEvent,
    CloseReason,
    ConversationItemAddedEvent,
    ErrorEvent,
    EventEmitter,
    FunctionToolsExecutedEvent,
    SpeechCreatedEvent,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
)
from .executor import execute_call
from .speech_handle import SpeechHandle
from .tools import RunContext

log = logging.getLogger("jarvis_rt.session")


class Backend(Protocol):
    """Model side. Calls ``session.backend_event(kind, **data)`` for output."""

    async def start(self, session: AgentSession, agent: Agent) -> None: ...
    async def aclose(self) -> None: ...
    def push_audio(self, pcm16_16k: bytes) -> None: ...
    def push_video(self, frame: bytes, ts_ms: int) -> None: ...
    def push_text(self, text: str) -> None: ...
    async def say(self, text: str) -> None: ...
    async def generate_reply(
        self, instructions: str | None, tool_choice: str | None
    ) -> None: ...
    def interrupt(self) -> None: ...
    async def send_tool_results(self, results: list[Any], *, reply: bool) -> None: ...
    async def agent_changed(self, agent: Agent) -> None: ...
    async def sync_agent(self, agent: Agent) -> None: ...


class _Output:
    def __init__(self) -> None:
        self.audio: Any = None  # callable(bytes) sink
        self.transcription: Any = None  # callable(str, final) sink
        self.flush: Any = None  # callable(): drop queued playback and unheard text
        self.audio_enabled = True
        self.transcription_enabled = True

    def set_audio_enabled(self, enabled: bool) -> None:
        self.audio_enabled = enabled

    def set_transcription_enabled(self, enabled: bool) -> None:
        self.transcription_enabled = enabled


class _Input:
    def __init__(self, session: AgentSession | None = None) -> None:
        self._session = session
        self.audio: Any = None
        self._video: Any = None
        self._video_task: asyncio.Task | None = None
        self.audio_enabled = True
        self.video_enabled = True

    @property
    def video(self) -> Any:
        return self._video

    @video.setter
    def video(self, source: Any) -> None:
        """A VideoInput whose ``frames()`` yields ``(jpeg_bytes, ts_ms)``."""
        if self._video_task is not None:
            self._video_task.cancel()
            self._video_task = None
        self._video = source
        if source is not None and self._session is not None:
            self._video_task = self._session._spawn(self._pump_video(source))

    async def _pump_video(self, source: Any) -> None:
        session = self._session
        try:
            async for jpeg, ts_ms in source.frames():
                if (
                    self.video_enabled
                    and session is not None
                    and session.backend is not None
                ):
                    session.backend.push_video(jpeg, ts_ms)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("video input failed")

    def set_audio_enabled(self, enabled: bool) -> None:
        self.audio_enabled = enabled

    def set_video_enabled(self, enabled: bool) -> None:
        self.video_enabled = enabled


class AgentSession(EventEmitter):
    def __init__(
        self,
        *,
        backend: Backend | None = None,
        user_away_timeout: float | None = None,
        userdata: Any = None,
        **options: Any,
    ) -> None:
        super().__init__()
        self.backend = backend
        self.options = options
        self.userdata = userdata
        self.user_away_timeout = user_away_timeout
        self.input = _Input(self)
        self.output = _Output()
        self._agent: Agent | None = None
        self._history = ChatContext.empty()
        self._user_state = "listening"
        self._agent_state = "initializing"
        self._speech: SpeechHandle | None = None
        self._agent_text = ""
        self._agent_audio_seen = False
        self._started = False
        self._room_io: Any = None
        self._closed = False
        self._tool_tasks: set[asyncio.Task] = set()
        self._away_task: asyncio.Task | None = None
        self.session_open_at = time.monotonic()

    # ---- read-only state -------------------------------------------------
    @property
    def current_agent(self) -> Agent:
        if self._agent is None:
            raise RuntimeError("AgentSession isn't running")
        return self._agent

    @property
    def history(self) -> ChatContext:
        return self._history

    @property
    def user_state(self) -> str:
        return self._user_state

    @property
    def agent_state(self) -> str:
        return self._agent_state

    @property
    def current_speech(self) -> SpeechHandle | None:
        return self._speech

    # ---- lifecycle -------------------------------------------------------
    async def start(
        self, agent: Agent, *, room: Any = None, room_options: Any = None, **_: Any
    ) -> None:
        if self._started:
            raise RuntimeError("AgentSession is already running")
        self._started = True
        self._agent = agent
        agent._session = self
        if room is not None:
            from .room_io import RoomIO

            self._room_io = RoomIO(self, room, room_options)
            await self._room_io.start()
        self._set_agent_state("initializing")
        if self.backend is not None:
            await self.backend.start(self, agent)
        self._set_agent_state("listening")
        self._arm_away()
        await agent.on_enter()

    async def aclose(self, reason: CloseReason = CloseReason.USER_INITIATED) -> None:
        if self._closed:
            return
        self._closed = True
        if self._away_task:
            self._away_task.cancel()
        for t in list(self._tool_tasks):
            t.cancel()
        agent = self._agent
        if agent is not None:
            with contextlib.suppress(Exception):
                await agent.on_exit()
        if self.backend is not None:
            with contextlib.suppress(Exception):
                await self.backend.aclose()
        if self._room_io is not None:
            with contextlib.suppress(Exception):
                await self._room_io.aclose()
        self.emit("close", CloseEvent(reason=reason))

    def _spawn(self, coro: Any) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tool_tasks.add(task)
        task.add_done_callback(self._tool_tasks.discard)
        return task

    def shutdown(self, *, reason: CloseReason = CloseReason.USER_INITIATED) -> None:
        """Close soon without awaiting (safe from sync callbacks and tools)."""
        if not self._closed:
            self._spawn(self.aclose(reason))

    # ---- state helpers ---------------------------------------------------
    def _set_user_state(self, new: str) -> None:
        old, self._user_state = self._user_state, new
        if old != new:
            self.emit(
                "user_state_changed",
                UserStateChangedEvent(old_state=old, new_state=new),
            )

    def _set_agent_state(self, new: str) -> None:
        old, self._agent_state = self._agent_state, new
        if old != new:
            self.emit(
                "agent_state_changed",
                AgentStateChangedEvent(old_state=old, new_state=new),
            )

    def _arm_away(self) -> None:
        if not self.user_away_timeout or self._closed:
            return
        if self._away_task:
            self._away_task.cancel()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _away() -> None:
            await asyncio.sleep(self.user_away_timeout)
            if self._user_state == "listening" and self._agent_state == "listening":
                self._set_user_state("away")

        self._away_task = loop.create_task(_away())

    def _add_item(self, item: Any) -> None:
        self._history.items.append(item)
        if self._agent is not None:
            self._agent.chat_ctx.items.append(item)
        self.emit("conversation_item_added", ConversationItemAddedEvent(item=item))

    # ---- public speech API ----------------------------------------------
    def say(
        self,
        text: str,
        *,
        audio: Any = None,
        allow_interruptions: bool = True,
        add_to_chat_ctx: bool = True,
        **_: Any,
    ) -> SpeechHandle:
        handle = SpeechHandle.create(allow_interruptions=allow_interruptions)
        self._begin_speech(handle, user_initiated=False, source="say")
        if add_to_chat_ctx:
            self._pending_say_item = text
        self._spawn(self._run_say(handle, text))
        return handle

    async def _run_say(self, handle: SpeechHandle, text: str) -> None:
        try:
            if self.backend is not None:
                await self.backend.say(text)
        except Exception as exc:
            self.backend_event("error", error=exc)

    def generate_reply(
        self,
        *,
        user_input: str | None = None,
        instructions: str | None = None,
        tool_choice: str | None = None,
        allow_interruptions: bool = True,
        **_: Any,
    ) -> SpeechHandle:
        handle = SpeechHandle.create(allow_interruptions=allow_interruptions)
        self._begin_speech(
            handle, user_initiated=user_input is not None, source="generate_reply"
        )
        if user_input is not None:
            self._add_item(ChatMessage(role="user", content=[user_input]))
            if self.backend is not None:
                self.backend.push_text(user_input)
            if instructions is None:
                return handle
        self._spawn(self._run_reply(handle, instructions, tool_choice))
        return handle

    async def _run_reply(
        self, handle: SpeechHandle, instructions: str | None, tool_choice: str | None
    ) -> None:
        try:
            if self.backend is not None:
                await self.backend.generate_reply(instructions, tool_choice)
        except Exception as exc:
            self.backend_event("error", error=exc)

    def _begin_speech(
        self, handle: SpeechHandle, *, user_initiated: bool, source: str
    ) -> None:
        self._speech = handle
        self._agent_text = ""
        self._agent_audio_seen = False
        self.emit(
            "speech_created",
            SpeechCreatedEvent(
                user_initiated=user_initiated, source=source, speech_handle=handle
            ),
        )
        self._set_agent_state("thinking")

    def interrupt(self, *, force: bool = False) -> asyncio.Future:
        speech = self._speech
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        if (
            speech is not None
            and not speech.done()
            and (speech.allow_interruptions or force)
        ):
            speech.interrupt(force=force)
            if self.backend is not None:
                self.backend.interrupt()
            self._finish_turn(interrupted=True)
        fut.set_result(None)
        return fut

    def push_text(self, text: str) -> None:
        if self.backend is not None:
            self.backend.push_text(text)

    def clear_user_turn(self) -> None:
        pass

    # ---- agent swaps -----------------------------------------------------
    def update_agent(self, agent: Agent) -> None:
        old = self._agent
        if old is agent:
            return
        agent._session = self
        if old is not None:
            old._session = None
        self._agent = agent
        if agent.chat_ctx.items == [] and old is not None:
            agent._chat_ctx = old.chat_ctx.copy()
        self._spawn(self._enter_agent(old, agent))

    async def _enter_agent(self, old: Agent | None, new: Agent) -> None:
        if old is not None:
            with contextlib.suppress(Exception):
                await old.on_exit()
        if self.backend is not None:
            await self.backend.agent_changed(new)
        await new.on_enter()

    async def _agent_instructions_changed(self, agent: Agent) -> None:
        if self.backend is not None and agent is self._agent:
            await self.backend.sync_agent(agent)

    _agent_tools_changed = _agent_instructions_changed
    _agent_chat_ctx_changed = _agent_instructions_changed

    # ---- media in --------------------------------------------------------
    def push_audio(self, pcm16_16k: bytes) -> None:
        if self.backend is not None and self.input.audio_enabled:
            self.backend.push_audio(pcm16_16k)

    # ---- backend -> session ---------------------------------------------
    def backend_event(self, kind: str, **data: Any) -> None:
        handler = getattr(self, f"_on_{kind}", None)
        if handler is None:
            log.debug("ignoring backend event %s", kind)
            return
        handler(**data)

    def _on_user_speech_started(self) -> None:
        self._set_user_state("speaking")
        speech = self._speech
        if speech is not None and not speech.done() and self._agent_state == "speaking":
            speech.interrupt(force=False)

    def _on_user_speech_stopped(self) -> None:
        self._set_user_state("listening")
        self._arm_away()

    def _on_user_transcript(self, text: str, final: bool) -> None:
        self.emit(
            "user_input_transcribed",
            UserInputTranscribedEvent(transcript=text, is_final=final),
        )
        if final and text.strip():
            self._add_item(ChatMessage(role="user", content=[text]))

    def _on_interrupted(self) -> None:
        self._finish_turn(interrupted=True)

    def _on_agent_audio(self, pcm24k: bytes) -> None:
        if not self._agent_audio_seen:
            self._agent_audio_seen = True
            if self._speech is None or self._speech.done():
                handle = SpeechHandle.create(allow_interruptions=True)
                self._begin_speech(
                    handle, user_initiated=False, source="generate_reply"
                )
            self._agent_audio_seen = True
            self._set_agent_state("speaking")
        if self.output.audio_enabled and self.output.audio is not None:
            self.output.audio(pcm24k)

    def _on_agent_text(self, text: str) -> None:
        self._agent_text += text
        if self.output.transcription_enabled and self.output.transcription is not None:
            self.output.transcription(text, False)

    def _on_turn_complete(self) -> None:
        self._finish_turn(interrupted=False)

    def _finish_turn(self, *, interrupted: bool) -> None:
        text = self._agent_text.strip()
        if text:
            self._add_item(
                ChatMessage(role="assistant", content=[text], interrupted=interrupted)
            )
        self._agent_text = ""
        self._agent_audio_seen = False
        speech, self._speech = self._speech, None
        if speech is not None and not speech.done():
            speech._mark_done()
        if interrupted and self.output.flush is not None:
            self.output.flush()
        elif (
            self.output.transcription_enabled and self.output.transcription is not None
        ):
            self.output.transcription("", True)
        if not self._closed and self._agent_state != "thinking":
            self._set_agent_state("listening")
        self._arm_away()

    def _on_error(self, error: Any, source: Any = None) -> None:
        self.emit("error", ErrorEvent(error=error, source=source))

    def _on_tool_calls(self, calls: list[FunctionCall]) -> None:
        self._set_agent_state("thinking")
        self._spawn(self._run_tools(calls))

    async def _run_tools(self, calls: list[FunctionCall]) -> None:
        agent = self._agent
        if agent is None:
            return
        self._set_agent_state("thinking")
        tools = {t.id: t for t in agent.tools}
        for call in calls:
            self._history.items.append(call)
            agent.chat_ctx.items.append(call)

        def ctx_for(call: FunctionCall) -> RunContext:
            return RunContext(
                session=self, speech_handle=self._speech, function_call=call
            )

        results = await asyncio.gather(
            *(execute_call(c, tools, ctx_for(c)) for c in calls),
            return_exceptions=False,
        )
        new_agent = next((r.agent for r in results if r.agent is not None), None)
        for r in results:
            self._history.items.append(r.output)
            agent.chat_ctx.items.append(r.output)
        self.emit(
            "function_tools_executed",
            FunctionToolsExecutedEvent(
                function_calls=[r.call for r in results],
                function_call_outputs=[r.output for r in results],
            ),
        )
        if new_agent is not None:
            self.update_agent(new_agent)
        # a handoff batch is answered by the incoming agent, not by the router
        reply = any(r.output.reply_required for r in results) and new_agent is None
        if self.backend is not None and not self._closed:
            await self.backend.send_tool_results(
                [r.output for r in results], reply=reply
            )
        if not reply and self._agent_state == "thinking":
            self._set_agent_state("listening")
