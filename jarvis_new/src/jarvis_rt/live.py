"""Direct Gemini Live backend (google-genai), replacing the LiveKit plugin.

Server-side activity detection is the only authority for end of user turn
and for cancelling a reply. This module streams mic audio up, maps server
messages to session backend events, runs the tool-response protocol, keeps
session resumption, and fails over along a model chain with a cooldown.
Behavior ported from livekit-plugins-google's RealtimeSession. Stall detection
lives in gemini_watchdog.StallClock and is driven from here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from google.genai import types

import gemini_watchdog

from .agent import Agent
from .chat_context import FunctionCall, FunctionCallOutput
from .gemini_schema import function_declarations
from .guards import RepeatGuard, is_foreign_speech

log = logging.getLogger("jarvis_rt.live")

INPUT_RATE = 16000
OUTPUT_RATE = 24000
MAX_TOOL_CALL_REJECTIONS = 3
MIC_BACKLOG_BLOCKS = 600  # ~19 s of 32 ms blocks held while (re)connecting
NO_REPLY_PLACEHOLDER_MODELS = ("3.1", "3.8")
GENERATE_REPLY_TIMEOUT_S = 10.0
RECOVERY_NOTICE = (
    "The voice connection was refreshed after a timeout. "
    "Do not execute or repeat any earlier request or tool. "
    "Say only: I lost that voice request, Sir. Please repeat it."
)


def http_options() -> types.HttpOptions:
    """Verified TLS defaults (certifi), built once off the audio path."""
    import ssl

    import certifi

    context = ssl.create_default_context(
        cafile=os.environ.get("SSL_CERT_FILE", certifi.where()),
        capath=os.environ.get("SSL_CERT_DIR"),
    )
    return types.HttpOptions(timeout=10_000, async_client_args={"ssl": context})


def _log_system_action(action: str, detail: str) -> None:
    """Best-effort entry in the desktop action log; never fails the audio path."""
    with contextlib.suppress(Exception):
        from system import log_action

        log_action(action, detail)


# Instruction turns are written as the model's own text, so it tends to read
# them aloud ("chain tool calls until the task is completed"). Mark them as
# private direction.
INTERNAL_DIRECTION = (
    "(Private direction to myself. Never say it, quote it or describe it "
    "aloud; just act on it: {text})"
)


def silence_ms() -> int:
    try:
        return max(
            200, int(os.environ.get("JARVIS_GEMINI_SILENCE_MS", "").strip() or 500)
        )
    except ValueError:
        return 500


def thinking_config(model_id: str) -> types.ThinkingConfig | None:
    """Per-generation thinking rules: 3.8 accepts none, 3.1 wants MINIMAL, 2.5 a small budget."""
    if "3.1" in model_id:
        return types.ThinkingConfig(thinking_level="MINIMAL")
    if "2.5" in model_id:
        return types.ThinkingConfig(thinking_budget=32)
    return None


def needs_reply_placeholder(model_id: str) -> bool:
    return not any(tag in model_id for tag in NO_REPLY_PLACEHOLDER_MODELS)


class ModelChain:
    """Ordered models; a failed model sits out for ``cooldown`` seconds."""

    def __init__(
        self,
        models: list[str],
        cooldown: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.models = list(models)
        self.cooldown = cooldown
        self._clock = clock
        self._failed_at: dict[str, float] = {}

    def fail(self, model: str) -> None:
        self._failed_at[model] = self._clock()

    def pick(self) -> str:
        now = self._clock()
        for m in self.models:
            at = self._failed_at.get(m)
            if at is None or now - at >= self.cooldown:
                return m
        # everything cooling down: retry the one that failed longest ago
        return min(self.models, key=lambda m: self._failed_at.get(m, 0.0))


class _Generation:
    def __init__(self, user_initiated: bool) -> None:
        self.user_initiated = user_initiated
        self.input_text = ""
        self.utterance_id = f"live-{time.monotonic_ns()}"
        self.output_closed = False
        self.done = False
        self.foreign = False


class LiveBackend:
    """``jarvis_rt.session.Backend`` implementation over Gemini Live."""

    def __init__(
        self,
        chain: list[str] | ModelChain,
        *,
        client: Any = None,
        voice: str = "Enceladus",
        http_options: types.HttpOptions | None = None,
        clock: Callable[[], float] = time.monotonic,
        watchdog: bool | None = None,
        generate_reply_timeout: float = GENERATE_REPLY_TIMEOUT_S,
    ) -> None:
        self.chain = (
            chain if isinstance(chain, ModelChain) else ModelChain(chain, clock=clock)
        )
        self.voice = voice
        self._client = client
        self._http_options = http_options
        self._clock = clock
        self.session: Any = None  # AgentSession
        self.agent: Agent | None = None
        self._conn: Any = None
        self._cm: Any = None
        self._model: str | None = None
        self._recv: asyncio.Task | None = None
        self._send: asyncio.Task | None = None
        self._runner: asyncio.Task | None = None
        self._mic: deque[bytes] = deque(maxlen=MIC_BACKLOG_BLOCKS)
        self._mic_ready = asyncio.Event()
        self._out: asyncio.Queue = asyncio.Queue()
        self._gen: _Generation | None = None
        self._repeat = RepeatGuard()
        self._pending_reply = False
        self._tool_choice_none = False
        self._rejected = 0
        self._handle: str | None = None
        self._closing = False
        self._restart = asyncio.Event()
        self._restart_error: BaseException | None = None
        self._fresh = False
        self._notice_pending = False
        self._recovery_until = 0.0
        #: Connection whose late resumption handles must be ignored after a recovery.
        self._stale_conn: Any = None
        self._drop_audio = False
        self._lock = asyncio.Lock()
        if watchdog is None:
            watchdog = gemini_watchdog.enabled()
        self._stall: gemini_watchdog.StallClock | None = (
            gemini_watchdog.StallClock() if watchdog else None
        )
        self._generate_reply_timeout = generate_reply_timeout
        self._reply_waiters: list[asyncio.Future] = []
        self._nudge_s = 1.5
        self._ready = asyncio.Event()

    # ---- Backend protocol -----------------------------------------------
    async def start(self, session: Any, agent: Agent) -> None:
        self.session = session
        self.agent = agent
        self._runner = asyncio.get_running_loop().create_task(self._run())
        # First connect must finish before on_enter speaks; bounded so a dead
        # network surfaces as an error event instead of hanging the call.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._ready.wait(), timeout=15.0)

    async def aclose(self) -> None:
        self._closing = True
        self._restart.set()
        for t in (self._runner,):
            if t:
                t.cancel()
        await self._teardown()

    def push_audio(self, pcm: bytes) -> None:
        if self._stall is not None:
            try:
                if self._stall.mic(
                    len(pcm) / 2 / INPUT_RATE,
                    gemini_watchdog.frame_rms(pcm),
                    self._clock(),
                ):
                    log.warning(
                        "Gemini silent %.0fs after speech; forcing reconnect",
                        gemini_watchdog.SILENT_S,
                    )
                    _log_system_action(
                        "gemini-watchdog", "stalled session, reconnecting"
                    )
                    self._recover()
            except Exception:
                log.exception("Gemini stall watchdog failed")
        self._mic.append(pcm)
        self._mic_ready.set()

    def push_video(self, frame: bytes, ts_ms: int) -> None:
        self._out.put_nowait(("video", frame))

    def push_text(self, text: str) -> None:
        self._out.put_nowait(
            (
                "content",
                [types.Content(role="user", parts=[types.Part(text=text)])],
                True,
            )
        )

    # Native audio may precede its transcript. The session validates a full
    # response before releasing native audio/captions to avoid spoken false
    # calendar-success claims before provider evidence exists.
    requires_response_validation = True

    async def say(self, text: str) -> None:
        await self.generate_reply(
            "Say exactly the following line to Sir, word for word, in your normal voice, "
            f"and nothing else: {text}",
            "none",
        )

    async def generate_reply(
        self, instructions: str | None, tool_choice: str | None
    ) -> None:
        self._pending_reply = True
        self._tool_choice_none = tool_choice == "none"
        self._rejected = 0
        turns: list[types.Content] = []
        if instructions:
            turns.append(
                types.Content(
                    role="model",
                    parts=[
                        types.Part(text=INTERNAL_DIRECTION.format(text=instructions))
                    ],
                )
            )
        if needs_reply_placeholder(self._model or self.chain.models[0]):
            turns.append(types.Content(role="user", parts=[types.Part(text=".")]))
        # An audio turn (no instructions) on a model without a placeholder needs
        # no client turn: server activity detection already answers the audio,
        # and an empty turn_complete would start a reply to the stale context.
        if turns:
            self._out.put_nowait(("content", turns, True))
        if self._stall is None:
            return
        waiter = asyncio.get_running_loop().create_future()
        self._reply_waiters.append(waiter)
        try:
            await asyncio.wait_for(waiter, timeout=self._generate_reply_timeout)
        except TimeoutError:
            self._reply_timed_out()
        finally:
            with contextlib.suppress(ValueError):
                self._reply_waiters.remove(waiter)

    def _reply_timed_out(self) -> None:
        """No generation began for this reply: recover, at most once per cooldown."""
        if self._closing or self._stall is None:
            return
        now = self._clock()
        if now - self._stall.forced_at < gemini_watchdog.COOLDOWN_S:
            return
        self._stall.forced_at = now
        self._stall.server(now)
        log.warning("Gemini reply creation timed out; opening a fresh connection")
        self._recover()

    def _release_reply_waiters(self, error: BaseException | None) -> None:
        waiters, self._reply_waiters = self._reply_waiters, []
        for waiter in waiters:
            if waiter.done():
                continue
            if error is None:
                waiter.set_result(None)
            else:
                waiter.set_exception(error)

    def interrupt(self) -> None:
        # Gemini cancels on user speech; locally just stop the rest of this reply.
        self._drop_audio = True

    async def send_tool_results(
        self, results: list[FunctionCallOutput], *, reply: bool
    ) -> None:
        responses = []
        for r in results:
            fr = types.FunctionResponse(
                id=r.call_id,
                name=r.name,
                response={"error": r.output} if r.is_error else {"output": r.output},
                scheduling=(
                    types.FunctionResponseScheduling.WHEN_IDLE
                    if reply
                    else types.FunctionResponseScheduling.SILENT
                ),
            )
            responses.append(fr)
        self._out.put_nowait(("tool_response", responses))
        if reply and os.environ.get("JARVIS_GEMINI_TURN_PATCH", "1").strip() != "0":
            before = self._gen
            asyncio.get_running_loop().call_later(self._nudge_s, self._nudge, before)

    def _nudge(self, before: _Generation | None) -> None:
        # Without a trailing empty turn Gemini can sit on a tool result and never speak it.
        gen = self._gen
        if gen is before and (gen is None or gen.done) and not self._closing:
            self._out.put_nowait(("content", [], True))

    async def agent_changed(self, agent: Agent) -> None:
        self.agent = agent
        self._request_restart()

    async def sync_agent(self, agent: Agent) -> None:
        self.agent = agent
        self._request_restart()

    # ---- connection management -------------------------------------------
    def recovery_pending(self) -> bool:
        """A forced reconnect is still within its bounded recovery window."""
        return (
            self._notice_pending or self._fresh or self._clock() < self._recovery_until
        )

    def _request_restart(self, error: BaseException | None = None) -> None:
        self._restart_error = error
        self._restart.set()

    def _recover(self) -> None:
        """Watchdog: fresh connection, no resumption identity, one-time notice."""
        self._fresh = True
        self._notice_pending = True
        self._handle = None
        self._recovery_until = self._clock() + gemini_watchdog.RECOVERY_S
        # The old receive task may still deliver a final handle while closing.
        self._stale_conn = self._conn
        self._request_restart()

    def _client_obj(self) -> Any:
        if self._client is None:
            from google import genai

            self._client = genai.Client(http_options=self._http_options)
        return self._client

    def _history_turns(self) -> list[types.Content]:
        if self.agent is None:
            return []
        chat = self.agent.chat_ctx.copy(
            exclude_function_call=True,
            exclude_instructions=True,
            exclude_empty_message=True,
        )
        return [c for c in chat.to_gemini_contents() if c.parts]

    def _config(self, model: str) -> types.LiveConnectConfig:
        agent = self.agent
        gen_cfg = types.GenerationConfig(
            candidate_count=1, thinking_config=thinking_config(model)
        )
        handle = None if self._fresh else self._handle
        self._fresh = False
        return types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            generation_config=gen_cfg,
            system_instruction=(
                types.Content(parts=[types.Part(text=agent.instructions)])
                if agent
                else None
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.voice
                    )
                )
            ),
            tools=function_declarations(agent.tools) if agent else [],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            session_resumption=types.SessionResumptionConfig(handle=handle),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                    silence_duration_ms=silence_ms(),
                )
            ),
        )

    async def _run(self) -> None:
        while not self._closing:
            model = self.chain.pick()
            self._restart.clear()
            error: BaseException | None = None
            try:
                async with self._lock:
                    self._cm = self._client_obj().aio.live.connect(
                        model=model, config=self._config(model)
                    )
                    self._conn = await self._cm.__aenter__()
                    self._model = model
                    if self._handle is None:
                        turns = self._history_turns()
                        if turns:
                            await self._conn.send_client_content(
                                turns=turns, turn_complete=False
                            )
                self._ready.set()
                self._send = asyncio.create_task(self._send_loop(self._conn))
                self._recv = asyncio.create_task(self._recv_loop(self._conn))
                waiter = asyncio.create_task(self._restart.wait())
                done, pending = await asyncio.wait(
                    [self._send, self._recv, waiter],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
                for task in done:
                    if task is not waiter and task.exception():
                        error = task.exception()
                if error is None and self._restart_error is not None:
                    error, self._restart_error = self._restart_error, None
            except asyncio.CancelledError:
                break
            except Exception as exc:
                error = exc
            await self._close_conn()
            self._finish_generation(abort=True)
            # A reply queued on this connection is lost with it.
            self._pending_reply = False
            self._release_reply_waiters(error)
            if self._closing:
                break
            if error is not None:
                self.chain.fail(model)
                log.warning("Gemini Live session error on %s: %s", model, error)
                self._emit("error", error=error)
                self._ready.set()
                await asyncio.sleep(0.2)

    async def _close_conn(self) -> None:
        cm, self._cm, self._conn = self._cm, None, None
        if cm is not None:
            with contextlib.suppress(Exception):
                await cm.__aexit__(None, None, None)

    async def _teardown(self) -> None:
        for t in (self._send, self._recv):
            if t:
                t.cancel()
        await self._close_conn()

    # ---- upstream --------------------------------------------------------
    async def _send_loop(self, conn: Any) -> None:
        if self._notice_pending:
            self._notice_pending = False
            await conn.send_client_content(
                turns=[
                    types.Content(role="user", parts=[types.Part(text=RECOVERY_NOTICE)])
                ],
                turn_complete=True,
            )
        mic = asyncio.create_task(self._mic_loop(conn))
        try:
            while True:
                item = await self._out.get()
                kind = item[0]
                if kind == "content":
                    await conn.send_client_content(
                        turns=item[1] or None, turn_complete=item[2]
                    )
                elif kind == "tool_response":
                    await conn.send_tool_response(function_responses=item[1])
                elif kind == "video":
                    await conn.send_realtime_input(
                        video=types.Blob(data=item[1], mime_type="image/jpeg")
                    )
        finally:
            mic.cancel()

    async def _mic_loop(self, conn: Any) -> None:
        while True:
            while self._mic:
                data = self._mic.popleft()
                await conn.send_realtime_input(
                    audio=types.Blob(
                        data=data, mime_type=f"audio/pcm;rate={INPUT_RATE}"
                    )
                )
            self._mic_ready.clear()
            if not self._mic:
                await self._mic_ready.wait()

    # ---- downstream ------------------------------------------------------
    def _emit(self, kind: str, **data: Any) -> None:
        if self.session is not None:
            self.session.backend_event(kind, **data)

    async def _recv_loop(self, conn: Any) -> None:
        async for msg in conn.receive():
            self._handle_message(msg, conn)
        # receive() ended: server closed this turn stream; keep listening
        # until the connection itself closes.
        while not self._closing and self._conn is conn:
            got = False
            async for msg in conn.receive():
                got = True
                self._handle_message(msg, conn)
            if not got:
                raise ConnectionError("Gemini Live stream closed")

    def _handle_message(self, msg: types.LiveServerMessage, conn: Any = None) -> None:
        sc = msg.server_content
        if msg.tool_call and self._tool_choice_none:
            self._reject_tools(msg.tool_call.function_calls or [])
            return

        if self._gen is None or self._gen.done:
            if sc is not None and sc.interrupted:
                if not self._pending_reply:
                    self._emit("user_speech_started")
                    self._emit("interrupted")
                sc = sc.model_copy(update={"interrupted": None})
                if not sc.model_dump(exclude_none=True):
                    sc = None
            if self._is_new_generation(msg, sc):
                self._start_generation()

        if msg.session_resumption_update:
            upd = msg.session_resumption_update
            # A recovered connection must not resume from its stalled predecessor.
            if (
                upd.resumable
                and upd.new_handle
                and (conn is None or conn is not self._stale_conn)
            ):
                self._handle = upd.new_handle
        if sc is not None:
            self._server_content(sc)
        if msg.tool_call:
            self._tool_calls(msg.tool_call)
        if msg.go_away:
            log.warning(
                "Gemini server indicates disconnection soon: %s", msg.go_away.time_left
            )
            self._request_restart()
        # liveness for the watchdog: model output or tool call counts as an answer
        if self._stall is not None and (
            msg.tool_call
            or (sc is not None and (sc.model_turn or sc.output_transcription))
        ):
            self._stall.server(self._clock())
            if not self._notice_pending:
                self._recovery_until = 0.0

    @staticmethod
    def _is_new_generation(
        msg: types.LiveServerMessage, sc: types.LiveServerContent | None
    ) -> bool:
        if msg.tool_call:
            return True
        return bool(
            sc
            and (
                sc.model_turn
                or (sc.output_transcription and sc.output_transcription.text)
                or (sc.input_transcription and sc.input_transcription.text)
            )
        )

    def _start_generation(self) -> None:
        self._rejected = 0
        if self._gen is not None and not self._gen.done:
            self._finish_generation()
        pending = self._pending_reply
        self._pending_reply = False
        self._gen = _Generation(user_initiated=pending)
        self._drop_audio = False
        self._repeat.start_turn()
        for waiter in self._reply_waiters:
            if not waiter.done():
                waiter.set_result(None)
        self._reply_waiters = []
        if not pending:
            # not a reply we asked for: Gemini heard the user speak
            self._tool_choice_none = False
            self._emit("user_speech_started", utterance_id=self._gen.utterance_id)

    def _server_content(self, sc: types.LiveServerContent) -> None:
        if sc.turn_complete:
            # tool_choice="none" covers the one reply it was requested for.
            self._tool_choice_none = False
        gen = self._gen
        if gen is None:
            return
        if sc.model_turn:
            for part in sc.model_turn.parts or []:
                if part.thought:
                    continue
                has_audio = part.inline_data and part.inline_data.data
                if has_audio and not gen.output_closed and not self._drop_audio:
                    self._emit("agent_audio", pcm24k=part.inline_data.data)
        if sc.input_transcription and sc.input_transcription.text:
            text = sc.input_transcription.text
            if not gen.input_text:
                text = text.lstrip()
            gen.input_text += text
            if not gen.foreign and is_foreign_speech(gen.input_text):
                # background noise heard as Korean/Chinese/...: not for Jarvis
                gen.foreign = True
                self._drop_audio = True
                log.info("dropping non-English transcript %r", gen.input_text[:40])
            self._emit(
                "user_transcript",
                text=gen.input_text,
                final=False,
                utterance_id=gen.utterance_id,
            )
        if sc.output_transcription and sc.output_transcription.text:
            if self._repeat.feed(sc.output_transcription.text, gen.input_text):
                self._drop_audio = True  # same sentence again: stay quiet
                log.info("suppressing repeated sentence")
            self._emit("agent_text", text=sc.output_transcription.text)
        if sc.generation_complete:
            gen.output_closed = True
        if sc.interrupted and not self._pending_reply:
            self._drop_audio = True
            self._emit("user_speech_started")
            self._emit("interrupted")
        if sc.turn_complete:
            self._finish_generation()

    def _finish_generation(self, *, abort: bool = False) -> None:
        gen = self._gen
        if gen is None or gen.done:
            return
        gen.done = True
        self._repeat.end_turn()
        if abort and not gen.input_text:
            self._emit("turn_complete")
            return
        self._emit("user_speech_stopped")
        if gen.input_text:
            self._emit(
                "user_transcript",
                text=gen.input_text,
                final=True,
                utterance_id=gen.utterance_id,
            )
        self._emit("turn_complete")

    def _tool_calls(self, tool_call: types.LiveServerToolCall) -> None:
        calls = [
            FunctionCall(
                call_id=fc.id or f"fnc-{time.monotonic_ns()}",
                name=fc.name or "",
                arguments=json.dumps(fc.args),
            )
            for fc in tool_call.function_calls or []
        ]
        gen = self._gen
        if calls and gen is not None and gen.foreign:
            log.info("rejecting tool calls from a non-English transcript")
            self._reject_tools(tool_call.function_calls or [])
            return
        if calls:
            self._emit("tool_calls", calls=calls)
        self._finish_generation()

    def _reject_tools(self, calls: list[types.FunctionCall]) -> None:
        if not calls:
            return
        self._rejected += 1
        if self._rejected > MAX_TOOL_CALL_REJECTIONS:
            return
        responses = [
            types.FunctionResponse(
                id=fc.id,
                name=fc.name,
                response={
                    "error": "Tool calls are disabled for this turn, respond to the user directly."
                },
                scheduling=types.FunctionResponseScheduling.WHEN_IDLE,
            )
            for fc in calls
        ]
        self._out.put_nowait(("tool_response", responses))
