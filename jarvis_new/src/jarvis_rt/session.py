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
from collections import OrderedDict
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
        #: Agents this session has been on before the current one. The live
        #: model can still call tools declared for them until it reconnects.
        self._previous_agents: list[Agent] = []
        self._history = ChatContext.empty()
        self._user_state = "listening"
        self._agent_state = "initializing"
        self._speech: SpeechHandle | None = None
        self._agent_text = ""
        self._agent_audio_seen = False
        self._validated_audio: list[bytes] = []
        self._validated_audio_bytes = 0
        self._validation_overflow = False
        # Streaming release of validated replies (see _pump_validated).
        self._hold_turn = False
        self._released_audio_bytes = 0
        self._released_text_len = 0
        # Playout clock: when queued speech ends, so state stays "speaking".
        self._playout_until = 0.0
        self._playout_timer: asyncio.TimerHandle | None = None
        self._started = False
        self._room_io: Any = None
        self._closed = False
        self._tool_tasks: set[asyncio.Task] = set()
        self._away_task: asyncio.Task | None = None
        self.session_open_at = time.monotonic()
        self._input_epoch = 0
        self._input_id = "implicit-0"
        self._input_explicit = False
        self._input_receipts: OrderedDict[str, str] = OrderedDict()
        self._input_partial = ""
        self._tool_receipts: OrderedDict[str, None] = OrderedDict()

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
        self._cancel_playout_timer()
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
        self._validated_audio = []
        self._validated_audio_bytes = 0
        self._validation_overflow = False
        self._hold_turn = False
        self._released_audio_bytes = 0
        self._released_text_len = 0
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
        elif self._playout_timer is not None and self._agent_state == "speaking":
            # Generation is over but speech is still playing out: a barge-in
            # must still cut it off.
            self._playout_until = 0.0
            self._cancel_playout_timer()
            if self.output.flush is not None:
                self.output.flush()
            if not self._closed:
                self._set_agent_state("listening")
        fut.set_result(None)
        return fut

    def push_text(self, text: str) -> None:
        if self.backend is not None:
            self.backend.push_text(text)

    def clear_user_turn(self) -> None:
        self._remember_input(self._input_id, "cancelled")
        self._input_partial = ""

    def _remember_input(self, key: str, outcome: str) -> None:
        self._input_receipts[key] = outcome
        self._input_receipts.move_to_end(key)
        while len(self._input_receipts) > 128:
            self._input_receipts.popitem(last=False)

    # ---- agent swaps -----------------------------------------------------
    def update_agent(self, agent: Agent) -> None:
        old = self._agent
        if old is agent:
            return
        agent._session = self
        if old is not None:
            old._session = None
        self._agent = agent
        if old is not None and all(a is not old for a in self._previous_agents):
            self._previous_agents.append(old)
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

    def _on_user_speech_started(self, utterance_id: str | None = None) -> None:
        if utterance_id is not None:
            if utterance_id in self._input_receipts or (
                self._input_explicit and utterance_id == self._input_id
            ):
                return
            self._input_id = utterance_id
            self._input_explicit = True
        elif self._user_state != "speaking":
            self._input_epoch += 1
            self._input_id = f"implicit-{self._input_epoch}"
            self._input_explicit = False
        self._input_partial = ""
        self._set_user_state("speaking")
        speech = self._speech
        if speech is not None and not speech.done() and self._agent_state == "speaking":
            speech.interrupt(force=False)

    def _on_user_speech_stopped(self) -> None:
        self._set_user_state("listening")
        self._arm_away()

    def _on_user_transcript(
        self, text: str, final: bool, utterance_id: str | None = None
    ) -> None:
        text = " ".join(str(text or "").split())
        key = utterance_id or self._input_id
        if not text or key in self._input_receipts:
            return
        if utterance_id and self._input_explicit and key != self._input_id:
            return  # delayed callback from an older, superseded utterance
        if not final and text == self._input_partial:
            return
        self._input_partial = text
        if final:
            self._remember_input(key, "final")  # before callbacks: reentrancy safe
        self.emit(
            "user_input_transcribed",
            UserInputTranscribedEvent(transcript=text, is_final=final, item_id=key),
        )
        if final:
            self._add_item(ChatMessage(role="user", content=[text]))

    def _on_interrupted(self) -> None:
        self._finish_turn(interrupted=True)

    def _on_agent_audio(self, pcm24k: bytes) -> None:
        if not self._agent_audio_seen:
            self._agent_audio_seen = True
            if self._speech is None or self._speech.done():
                handle = SpeechHandle.create(allow_interruptions=True)
                pending_text = self._agent_text
                self._begin_speech(
                    handle, user_initiated=False, source="generate_reply"
                )
                self._agent_text = pending_text
            self._agent_audio_seen = True
            self._set_agent_state("speaking")
        if getattr(self.backend, "requires_response_validation", False):
            self._validated_audio_bytes += len(pcm24k)
            if self._validated_audio_bytes <= 24_000 * 2 * 60:
                self._validated_audio.append(pcm24k)
            else:
                self._validation_overflow = True
            self._pump_validated()
            return
        self._play(pcm24k)

    def _on_agent_text(self, text: str) -> None:
        self._agent_text += text
        if getattr(self.backend, "requires_response_validation", False):
            self._pump_validated()
            return
        if self.output.transcription_enabled and self.output.transcription is not None:
            self.output.transcription(text, False)

    # ---- streaming release of validated replies ---------------------------
    #: Speech rate used to bound audio by transcript (chars per second).
    #: Set above real speech, so released audio never runs far ahead of
    #: the text that was checked.
    STREAM_CHARS_PER_S = 14.0
    #: Audio allowed past the checked transcript (transcript lags audio).
    STREAM_LEAD_S = 1.2

    def _play(self, pcm24k: bytes) -> None:
        if self.output.audio_enabled and self.output.audio is not None:
            self.output.audio(pcm24k)
            now = time.monotonic()
            self._playout_until = (
                max(self._playout_until, now) + len(pcm24k) / (24_000 * 2)
            )

    def _release_text(self, upto: int) -> None:
        if upto <= self._released_text_len:
            return
        chunk = self._agent_text[self._released_text_len : upto]
        self._released_text_len = upto
        if (
            chunk
            and self.output.transcription_enabled
            and self.output.transcription is not None
        ):
            self.output.transcription(chunk, False)

    def _pump_validated(self) -> None:
        """Play a validated reply while it streams in, not after it ends.

        Audio is released only after clean transcript exists, and only up
        to the duration that transcript can cover (plus a small lead). A
        reply that touches the calendar (context, domain word, or unsafe
        claim) holds the rest for the full check in _finish_turn.
        """
        if self._hold_turn or self._validation_overflow:
            return
        text = self._agent_text
        if not text.strip():
            return
        import claim_guard

        if claim_guard.calendar_risk(text):
            self._hold_turn = True
            return
        self._release_text(len(text))
        budget = int(
            (len(text.strip()) / self.STREAM_CHARS_PER_S + self.STREAM_LEAD_S)
            * 24_000
        ) * 2
        while self._validated_audio:
            room = budget - self._released_audio_bytes
            if room <= 0:
                break
            packet = self._validated_audio[0]
            if len(packet) > room:
                head, tail = packet[:room], packet[room:]
                self._validated_audio[0] = tail
                packet = head
            else:
                self._validated_audio.pop(0)
            self._validated_audio_bytes -= len(packet)
            self._released_audio_bytes += len(packet)
            self._play(packet)

    def _cancel_playout_timer(self) -> None:
        if self._playout_timer is not None:
            self._playout_timer.cancel()
            self._playout_timer = None

    def _settle_listening(self) -> None:
        """Turn over: go "listening" once queued speech has played out."""
        self._cancel_playout_timer()
        remaining = self._playout_until - time.monotonic()
        if remaining > 0.25 and self._agent_state == "speaking":
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                self._playout_timer = loop.call_later(remaining, self._playout_done)
                return
        if not self._closed and self._agent_state != "thinking":
            self._set_agent_state("listening")

    def _playout_done(self) -> None:
        self._playout_timer = None
        if self._closed or self._agent_state != "speaking":
            return
        if self._speech is not None and not self._speech.done():
            return  # a new reply owns the state now
        self._set_agent_state("listening")

    def _on_turn_complete(self) -> None:
        self._finish_turn(interrupted=False)

    def _finish_turn(self, *, interrupted: bool) -> None:
        text = self._agent_text.strip()
        blocked_line = ""
        if getattr(self.backend, "requires_response_validation", False):
            import claim_guard

            safe = claim_guard.calendar_safe_text(text)
            blocked = (
                safe != text
                or self._validation_overflow
                or (self._validated_audio and not text)
            )
            if blocked:
                if not interrupted:
                    blocked_line = (
                        safe
                        if safe != text
                        else "I could not verify that response, Sir."
                    )
                text = ""  # Never retain a rejected or cancelled false claim.
            elif not interrupted:
                # Release what streaming held back (calendar hold or the
                # tail past the transcript budget).
                self._release_text(len(self._agent_text))
                for packet in self._validated_audio:
                    self._play(packet)
            self._validated_audio = []
            self._validated_audio_bytes = 0
            self._validation_overflow = False
            self._hold_turn = False
            self._released_audio_bytes = 0
            self._released_text_len = 0
        if text:
            self._add_item(
                ChatMessage(role="assistant", content=[text], interrupted=interrupted)
            )
        self._agent_text = ""
        self._agent_audio_seen = False
        speech, self._speech = self._speech, None
        if speech is not None and not speech.done():
            speech._mark_done()
        if interrupted:
            self._playout_until = 0.0
            self._cancel_playout_timer()
            if self.output.flush is not None:
                self.output.flush()
        elif (
            self.output.transcription_enabled and self.output.transcription is not None
        ):
            self.output.transcription("", True)
        self._settle_listening()
        self._arm_away()
        if blocked_line and not self._closed:
            self.say(blocked_line)

    def _on_error(self, error: Any, source: Any = None) -> None:
        self.emit("error", ErrorEvent(error=error, source=source))

    def _on_tool_calls(self, calls: list[FunctionCall]) -> None:
        fresh = []
        for call in calls:
            if call.call_id and call.call_id in self._tool_receipts:
                log.info("ignoring replayed tool call id=%s", call.call_id)
                continue
            if call.call_id:
                self._tool_receipts[call.call_id] = None
                while len(self._tool_receipts) > 256:
                    self._tool_receipts.popitem(last=False)
            fresh.append(call)
        if fresh:
            self._set_agent_state("thinking")
            self._spawn(self._run_tools(fresh))

    def _callable_tools(self, agent: Agent) -> dict[str, Any]:
        """Tools a call may resolve to: the current agent's, then earlier agents'.

        The live model may still call tools declared for an agent this session
        has just left (until it reconnects with the new declarations). Those
        names resolve here; the current agent's tool wins on a name clash, and
        the most recently left agent wins among earlier ones.
        """
        tools: dict[str, Any] = {}
        for prev in self._previous_agents:
            tools.update({t.id: t for t in prev.tools})
        tools.update({t.id: t for t in agent.tools})
        return tools

    async def _run_tools(self, calls: list[FunctionCall]) -> None:
        agent = self._agent
        if agent is None:
            return
        self._set_agent_state("thinking")
        tools = self._callable_tools(agent)
        for call in calls:
            self._history.items.append(call)
            agent.chat_ctx.items.append(call)

        def ctx_for(call: FunctionCall) -> RunContext:
            return RunContext(
                session=self, speech_handle=self._speech, function_call=call
            )

        log.info("tool calls: %s", ", ".join(c.name for c in calls))
        results = await asyncio.gather(
            *(execute_call(c, tools, ctx_for(c)) for c in calls),
            return_exceptions=False,
        )
        for r in results:
            if r.output.is_error:
                log.warning(
                    "tool %s failed: %s", r.call.name, str(r.output.output)[:300]
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
