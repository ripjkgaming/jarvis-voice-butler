# Derived from livekit-agents (Apache-2.0), Copyright 2023 LiveKit, Inc.
# Turn-taking behaviour (barge-in confirmation after a minimum speech duration,
# false-interruption resume, preemptive generation, endpointing delay) follows
# livekit/agents voice/agent_activity.py and voice/audio_recognition.py. The
# logic is re-implemented here against the jarvis_rt Backend protocol; no code
# is copied.
"""Direct pipeline backend: VAD -> STT -> Gemini text LLM -> TTS.

The previous stack ran livekit's AgentSession with Silero VAD, FasterWhisper
STT, google.LLM over a fallback chain and Piper TTS. This backend keeps that
turn behaviour on the in-repo runtime. It implements the ``Backend`` protocol
of ``jarvis_rt.session``; all state (history, speech handles, tool execution)
stays in the session, and this class reports through ``backend_event``.

Collaborators are injected and duck-typed:

* ``vad.stream()`` returns an object with ``push_frame(pcm16_16k)``, async
  iteration yielding events whose type is ``start_of_speech`` or
  ``end_of_speech``, and an optional ``aclose()``.
* ``stt`` offers ``transcribe(pcm16_16k) -> str`` (or async), or the
  ``recognize(AudioFrame)`` API of ``jarvis_rt.stt.STT``.
* ``tts`` offers ``synthesize(text)``. It may return PCM bytes (awaitable or
  not) or a ``ChunkedStream``-like async iterable of ``SynthesizedAudio``.
  Output is resampled to 24 kHz PCM16 when the provider rate differs.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from google.genai import types

from .agent import Agent
from .chat_context import ChatMessage, FunctionCall, FunctionCallOutput
from .gemini_schema import function_declarations
from .live import ModelChain

log = logging.getLogger("jarvis_rt.pipeline_direct")

INPUT_RATE = 16000
OUTPUT_RATE = 24000
HTTP_TIMEOUT_MS = 60_000
MODEL_COOLDOWN_S = 60.0
PREROLL_BYTES = int(0.3 * INPUT_RATE) * 2  # 300 ms of int16 mono kept before onset
MAX_SENTENCE_CHARS = 220
DEFAULT_BARGE_MIN_S = 0.3

_SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*\s|\n")


class SentenceSplitter:
    """Cuts streamed text at sentence ends, never inside a number or word."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> list[str]:
        self._buf += text
        out: list[str] = []
        while True:
            match = _SENTENCE_END.search(self._buf)
            if match is None:
                break
            out.append(self._buf[: match.end()].strip())
            self._buf = self._buf[match.end() :]
        if len(self._buf) > MAX_SENTENCE_CHARS:
            cut = self._buf.rfind(" ", 0, MAX_SENTENCE_CHARS)
            if cut > 0:
                out.append(self._buf[:cut].strip())
                self._buf = self._buf[cut:]
        return [s for s in out if s]

    def flush(self) -> list[str]:
        rest, self._buf = self._buf.strip(), ""
        return [rest] if rest else []


def split_sentences(text: str) -> list[str]:
    splitter = SentenceSplitter()
    return splitter.feed(text) + splitter.flush()


def _is_noise(text: str) -> bool:
    """No transcript, or only punctuation/whitespace: treated as no speech."""
    return not any(ch.isalnum() for ch in text)


def _vad_kind(event: Any) -> str | None:
    raw = getattr(event, "type", None)
    if raw is None:
        raw = getattr(event, "kind", None)
    if raw is None:
        raw = event
    text = str(getattr(raw, "value", raw)).lower()
    if "start" in text:
        return "start"
    if "end" in text:
        return "end"
    return None


def _to_output_rate(pcm: bytes, rate: int) -> bytes:
    if not pcm or rate == OUTPUT_RATE or rate <= 0:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n_out = max(1, round(len(samples) * OUTPUT_RATE / rate))
    positions = np.linspace(0, len(samples) - 1, n_out)
    resampled = np.interp(positions, np.arange(len(samples)), samples)
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()


def _http_options() -> types.HttpOptions:
    from .live import http_options

    return http_options().model_copy(update={"timeout": HTTP_TIMEOUT_MS})


def _chunk_parts(chunk: Any) -> list[Any]:
    for candidate in (getattr(chunk, "candidates", None) or [])[:1]:
        content = getattr(candidate, "content", None)
        if content is not None and content.parts:
            return list(content.parts)
    return []


@dataclass(eq=False)
class _Reply:
    """One LLM generation plus the speech rendered from it.

    ``hold`` means output is buffered rather than emitted (speculative reply,
    thinking reply while the user talks, or a suspended reply during barge-in).
    """

    contents: list[types.Content]
    system: str | None
    tools_enabled: bool
    hold: bool = False
    interrupted: bool = False
    speculative_text: str | None = None
    dead: bool = False
    finished: bool = False
    producer_done: bool = False
    producer: asyncio.Task | None = None
    speaker: asyncio.Task | None = None
    sentences: deque[str] = field(default_factory=deque)
    held: list[str] = field(default_factory=list)
    tool_calls: list[FunctionCall] = field(default_factory=list)
    splitter: SentenceSplitter = field(default_factory=SentenceSplitter)
    wake: asyncio.Event = field(default_factory=asyncio.Event)


class DirectBackend:
    """``jarvis_rt.session.Backend`` over VAD, STT, a text LLM and TTS."""

    def __init__(
        self,
        *,
        vad: Any,
        stt: Any,
        tts: Any,
        llm_models: list[str],
        barge_in: dict[str, Any] | None = None,
        endpointing_delay: float = 0.15,
        preemptive_generation: bool = True,
        client: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not llm_models:
            raise ValueError("DirectBackend needs at least one LLM model")
        barge = dict(barge_in or {})
        self.vad = vad
        self.stt = stt
        self.tts = tts
        self.chain = ModelChain(llm_models, cooldown=MODEL_COOLDOWN_S, clock=clock)
        self.min_interrupt_s = float(barge.get("min_duration", DEFAULT_BARGE_MIN_S))
        self.resume_false_interruption = bool(
            barge.get("resume_false_interruption", True)
        )
        self.endpointing_delay = max(0.0, float(endpointing_delay))
        self.preemptive = bool(preemptive_generation)
        self._client = client
        self.session: Any = None
        self.agent: Agent | None = None
        self._vad_stream: Any = None
        self._vad_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        # user turn state
        self._in_speech = False
        self._pending = False  # utterance ended, turn not yet committed
        self._turn_open = False  # user_speech_started emitted, not yet stopped
        self._utt = bytearray()
        self._preroll: deque[bytes] = deque()
        self._preroll_len = 0
        self._finalize_task: asyncio.Task | None = None
        self._barge_task: asyncio.Task | None = None
        # agent output state
        self._reply: _Reply | None = None
        self._speaking = False
        self._closed = False

    # ---- Backend protocol ------------------------------------------------
    async def start(self, session: Any, agent: Agent) -> None:
        self.session = session
        self.agent = agent
        stream = self.vad.stream()
        if inspect.isawaitable(stream):
            stream = await stream
        self._vad_stream = stream
        self._vad_task = self._spawn(self._vad_loop(stream))

    async def aclose(self) -> None:
        self._closed = True
        if self._reply is not None:
            self._discard(self._reply)
        for task in (self._vad_task, self._finalize_task, self._barge_task):
            if task is not None:
                task.cancel()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        if self._vad_stream is not None and hasattr(self._vad_stream, "aclose"):
            with contextlib.suppress(Exception):
                await self._vad_stream.aclose()
        if hasattr(self.tts, "aclose"):
            with contextlib.suppress(Exception):
                await self.tts.aclose()

    def push_audio(self, pcm16_16k: bytes) -> None:
        if self._vad_stream is None or self._closed:
            return
        from .stt import AudioFrame

        self._vad_stream.push_frame(
            AudioFrame(data=pcm16_16k, sample_rate=INPUT_RATE, num_channels=1)
        )
        if self._in_speech or self._pending:
            self._utt.extend(pcm16_16k)
            return
        self._preroll.append(pcm16_16k)
        self._preroll_len += len(pcm16_16k)
        while self._preroll_len > PREROLL_BYTES and self._preroll:
            self._preroll_len -= len(self._preroll.popleft())

    def push_video(self, frame: bytes, ts_ms: int) -> None:
        """No vision in the direct pipeline; frames are dropped."""

    def push_text(self, text: str) -> None:
        """Record typed text as a user turn and answer it (as a spoken turn would)."""
        agent = self.agent
        if agent is None or self._closed:
            return
        last_user = next(
            (
                item
                for item in reversed(agent.chat_ctx.items)
                if isinstance(item, ChatMessage) and item.role == "user"
            ),
            None,
        )
        if last_user is None or last_user.raw_text_content != text:
            agent.chat_ctx.items.append(ChatMessage(role="user", content=[text]))
        if self._reply is not None:
            self._discard(self._reply)
        self._start_reply()

    async def say(self, text: str) -> None:
        """Speak ``text`` verbatim through TTS; the LLM is not called."""
        if self._reply is not None:
            self._discard(self._reply)
        reply = _Reply(contents=[], system=None, tools_enabled=False)
        reply.producer_done = True
        reply.sentences.extend(split_sentences(text))
        self._reply = reply
        if text.strip():
            self._emit("agent_text", text=text)
        self._start_speaker(reply)

    async def generate_reply(
        self, instructions: str | None, tool_choice: str | None
    ) -> None:
        if self._reply is not None:
            self._discard(self._reply)
        self._start_reply(
            system=instructions or None, tools_enabled=tool_choice != "none"
        )

    def interrupt(self) -> None:
        reply = self._reply
        if reply is not None:
            self._discard(reply)
        self._speaking = False

    async def send_tool_results(self, results: list[Any], *, reply: bool) -> None:
        self._ensure_results_in_chat(results)
        if not reply or self._closed:
            return
        if self._reply is not None:
            self._discard(self._reply)
        self._start_reply()

    async def agent_changed(self, agent: Agent) -> None:
        # Instructions and tools are read from the agent at each generation.
        self.agent = agent

    async def sync_agent(self, agent: Agent) -> None:
        self.agent = agent

    # ---- event plumbing --------------------------------------------------
    def _emit(self, kind: str, **data: Any) -> None:
        if self.session is not None:
            self.session.backend_event(kind, **data)

    def _spawn(self, coro: Any) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _cancel_task(self, task: asyncio.Task | None) -> None:
        if task is not None:
            task.cancel()

    def _ensure_results_in_chat(self, results: list[Any]) -> None:
        if self.agent is None:
            return
        items = self.agent.chat_ctx.items
        for result in results:
            if not isinstance(result, FunctionCallOutput):
                continue
            if not any(
                isinstance(i, FunctionCallOutput) and i.call_id == result.call_id
                for i in items
            ):
                items.append(result)

    # ---- user turn: VAD gating --------------------------------------------
    async def _vad_loop(self, stream: Any) -> None:
        try:
            async for event in stream:
                kind = _vad_kind(event)
                if kind == "start":
                    self._on_speech_start()
                elif kind == "end":
                    self._on_speech_end()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("VAD stream failed")
            self._emit("error", error=exc)

    def _on_speech_start(self) -> None:
        if self._in_speech:
            return
        self._in_speech = True
        loop = asyncio.get_running_loop()
        if self._pending:
            # The user kept talking: the pending turn continues in this utterance.
            self._cancel_task(self._finalize_task)
            self._finalize_task = None
            reply = self._reply
            if reply is not None and reply.speculative_text is not None:
                self._discard(reply)
        else:
            self._utt = bytearray(b"".join(self._preroll))
            self._preroll.clear()
            self._preroll_len = 0

        reply = self._reply
        if self._speaking and reply is not None and not reply.hold:
            # Agent is audible: only a confirmed interruption cuts it off.
            self._barge_task = loop.create_task(self._barge_after(self.min_interrupt_s))
            return
        if not self._turn_open:
            self._turn_open = True
            self._emit("user_speech_started")
        if reply is not None and not reply.hold:
            # Thinking reply: hold its output until this utterance is judged.
            self._suspend(reply, interrupted=False)

    async def _barge_after(self, delay: float) -> None:
        await asyncio.sleep(delay)
        self._barge_task = None
        if not self._in_speech:
            return
        self._turn_open = True
        self._emit("user_speech_started")
        reply = self._reply
        if reply is not None:
            self._suspend(reply, interrupted=True)

    def _on_speech_end(self) -> None:
        if not self._in_speech:
            return
        self._in_speech = False
        if self._barge_task is not None:
            # Shorter than the barge-in threshold: noise, nothing was interrupted.
            self._cancel_task(self._barge_task)
            self._barge_task = None
            self._utt = bytearray()
            return
        self._pending = True
        loop = asyncio.get_running_loop()
        audio = bytes(self._utt)
        self._finalize_task = loop.create_task(self._finalize(audio, loop.time()))

    async def _finalize(self, audio: bytes, ended_at: float) -> None:
        text = await self._transcribe(audio)
        if self.preemptive and text.strip() and not _is_noise(text):
            self._speculate(text)
        loop = asyncio.get_running_loop()
        remaining = self.endpointing_delay - (loop.time() - ended_at)
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._commit(text)

    async def _transcribe(self, pcm: bytes) -> str:
        if not pcm:
            return ""
        try:
            if hasattr(self.stt, "transcribe"):
                result = self.stt.transcribe(pcm)
            else:
                from .stt import AudioFrame

                frame = AudioFrame(data=pcm, sample_rate=INPUT_RATE, num_channels=1)
                result = self.stt.recognize(frame)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, str):
                return result
            alternatives = getattr(result, "alternatives", None) or []
            return alternatives[0].text if alternatives else ""
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("transcription failed: %s", exc)
            self._emit("error", error=exc)
            return ""

    def _speculate(self, text: str) -> None:
        if self._reply is not None:
            self._discard(self._reply)
        reply = self._start_reply(hold=True, extra_user=text)
        reply.speculative_text = text.strip()

    def _commit(self, text: str) -> None:
        self._finalize_task = None
        self._pending = False
        self._utt = bytearray()
        text = text.strip()
        reply = self._reply
        if not text or _is_noise(text):
            self._turn_open = False
            self._emit("user_speech_stopped")
            if reply is not None and reply.hold and not reply.dead:
                if reply.interrupted and not self.resume_false_interruption:
                    self._discard(reply)
                else:
                    self._release(reply)
            return
        if reply is not None and reply.speculative_text == text:
            reply.speculative_text = None
            self._turn_open = False
            self._emit("user_transcript", text=text, final=True)
            self._emit("user_speech_stopped")
            self._release(reply)
            return
        if reply is not None:
            self._discard(reply)
        self._turn_open = False
        self._emit("user_transcript", text=text, final=True)
        self._emit("user_speech_stopped")
        self._start_reply()

    # ---- agent output: reply lifecycle ------------------------------------
    def _contents(self, extra_user: str | None) -> list[types.Content]:
        contents = (
            [c for c in self.agent.chat_ctx.to_gemini_contents() if c.parts]
            if self.agent
            else []
        )
        if extra_user:
            contents.append(
                types.Content(role="user", parts=[types.Part(text=extra_user)])
            )
        if not contents:
            contents.append(types.Content(role="user", parts=[types.Part(text=".")]))
        return contents

    def _config(self, reply: _Reply) -> types.GenerateContentConfig:
        agent = self.agent
        system = agent.instructions if agent is not None else ""
        if reply.system:
            system = f"{system}\n\n{reply.system}" if system else reply.system
        tools = []
        if reply.tools_enabled and agent is not None:
            tools = function_declarations(agent.tools)
        return types.GenerateContentConfig(
            system_instruction=system or None, tools=tools or None
        )

    def _client_obj(self) -> Any:
        if self._client is None:
            from google import genai

            self._client = genai.Client(http_options=_http_options())
        return self._client

    def _start_reply(
        self,
        *,
        hold: bool = False,
        system: str | None = None,
        tools_enabled: bool = True,
        extra_user: str | None = None,
    ) -> _Reply:
        reply = _Reply(
            contents=self._contents(extra_user),
            system=system,
            tools_enabled=tools_enabled,
            hold=hold,
        )
        self._reply = reply
        reply.producer = self._spawn(self._produce(reply))
        if not hold:
            self._start_speaker(reply)
        return reply

    def _start_speaker(self, reply: _Reply) -> None:
        if reply.speaker is None and not reply.dead:
            reply.speaker = self._spawn(self._speak(reply))

    def _suspend(self, reply: _Reply, *, interrupted: bool) -> None:
        """Stop emitting for ``reply`` and cancel any render in flight.

        The LLM keeps streaming into the held buffer and unspoken sentences
        stay queued, so a false interruption can resume without loss.
        """
        reply.hold = True
        speaker, reply.speaker = reply.speaker, None
        self._cancel_task(speaker)
        if interrupted:
            reply.interrupted = True
            if self._speaking:
                self._speaking = False
                self._emit("interrupted")

    def _release(self, reply: _Reply) -> None:
        reply.hold = False
        reply.interrupted = False
        if reply.held:
            text = "".join(reply.held)
            reply.held.clear()
            self._emit("agent_text", text=text)
        self._start_speaker(reply)

    def _discard(self, reply: _Reply) -> None:
        reply.dead = True
        reply.hold = True
        reply.held.clear()
        reply.sentences.clear()
        self._cancel_task(reply.producer)
        self._cancel_task(reply.speaker)
        reply.producer = None
        reply.speaker = None
        if self._reply is reply:
            self._reply = None
            self._speaking = False

    def _maybe_finish(self, reply: _Reply) -> None:
        if (
            reply.dead
            or reply.finished
            or reply.hold
            or reply.speaker is not None
            or not reply.producer_done
            or reply.sentences
        ):
            return
        reply.finished = True
        if reply.tool_calls:
            self._emit("tool_calls", calls=list(reply.tool_calls))
        self._emit("turn_complete")
        if self._reply is reply:
            self._reply = None
            self._speaking = False

    def _text(self, reply: _Reply, delta: str) -> None:
        if reply.dead:
            return
        if reply.hold:
            reply.held.append(delta)
        else:
            self._emit("agent_text", text=delta)
        reply.sentences.extend(reply.splitter.feed(delta))
        reply.wake.set()

    # ---- LLM -------------------------------------------------------------
    async def _produce(self, reply: _Reply) -> None:
        last_error: BaseException | None = None
        tried: set[str] = set()
        try:
            for _ in range(len(self.chain.models)):
                model = self.chain.pick()
                if model in tried:
                    break
                tried.add(model)
                produced = False
                try:
                    stream = (
                        await self._client_obj().aio.models.generate_content_stream(
                            model=model,
                            contents=reply.contents,
                            config=self._config(reply),
                        )
                    )
                    async for chunk in stream:
                        for part in _chunk_parts(chunk):
                            if getattr(part, "thought", None):
                                continue
                            call = getattr(part, "function_call", None)
                            if call is not None and call.name:
                                reply.tool_calls.append(
                                    FunctionCall(
                                        call_id=call.id or f"fnc-{time.monotonic_ns()}",
                                        name=call.name,
                                        arguments=json.dumps(call.args or {}),
                                    )
                                )
                                produced = True
                            elif part.text:
                                produced = True
                                self._text(reply, part.text)
                    last_error = None
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.chain.fail(model)
                    log.warning("LLM %s failed: %s", model, exc)
                    last_error = exc
                    if produced:
                        break  # output already spoken: cannot retry without repeating it
            if last_error is not None:
                self._emit("error", error=last_error)
            for sentence in reply.splitter.flush():
                reply.sentences.append(sentence)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("reply generation failed")
            self._emit("error", error=exc)
        reply.producer_done = True
        reply.wake.set()
        self._maybe_finish(reply)

    # ---- TTS -------------------------------------------------------------
    async def _speak(self, reply: _Reply) -> None:
        try:
            while not reply.dead:
                if reply.sentences:
                    sentence = reply.sentences.popleft()
                elif reply.producer_done:
                    break
                else:
                    reply.wake.clear()
                    await reply.wake.wait()
                    continue
                try:
                    pcm = await self._render(sentence)
                except asyncio.CancelledError:
                    # Cut mid-render: the sentence is replayed on resume.
                    reply.sentences.appendleft(sentence)
                    raise
                except Exception as exc:
                    log.warning("TTS failed for %r: %s", sentence, exc)
                    self._emit("error", error=exc)
                    continue
                if reply.dead or reply.hold:
                    reply.sentences.appendleft(sentence)
                    return
                if pcm:
                    self._speaking = True
                    self._emit("agent_audio", pcm24k=pcm)
        finally:
            if reply.speaker is asyncio.current_task():
                reply.speaker = None
        self._maybe_finish(reply)

    async def _render(self, sentence: str) -> bytes:
        result = self.tts.synthesize(sentence)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, (bytes, bytearray, memoryview)):
            rate = getattr(self.tts, "sample_rate", OUTPUT_RATE) or OUTPUT_RATE
            return _to_output_rate(bytes(result), rate)
        chunks: list[bytes] = []
        rate = 0
        try:
            async for audio in result:
                frame = getattr(audio, "frame", audio)
                chunks.append(bytes(frame.data))
                rate = int(getattr(frame, "sample_rate", 0) or rate)
        finally:
            if hasattr(result, "aclose"):
                with contextlib.suppress(Exception):
                    await result.aclose()
        rate = rate or int(getattr(self.tts, "sample_rate", OUTPUT_RATE) or OUTPUT_RATE)
        return _to_output_rate(b"".join(chunks), rate)
