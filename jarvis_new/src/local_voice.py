"""Local neural voice (Piper): $0 TTS with zero inference burn.

The voice model lives on disk (~/.jarvis/voices/*.onnx, downloaded once)
and synthesis runs on CPU, so speaking costs nothing and touches no
cloud meter. Default voice is en_GB-alan-medium (British male).

Interruption behavior (promised to the user): text is split into
sentences, each rendered in a worker thread. Cancelling the stream
lands between sentences, so the worst case is hearing out the tail of
the current sentence (~1s), same as cloud voices.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from livekit.agents import tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

# Pure, LiveKit-free pieces live in audio_shared (the chess voice engine uses
# them too); the names stay importable from here for existing callers.
from audio_shared import (  # noqa: F401
    DEFAULT_LENGTH_SCALE,
    _ends_with_abbreviation,
    load_piper_voice,
    render_sentence_pcm,
    resolve_length_scale,
    resolve_voice_path,
    sentence_split,
)

logger = logging.getLogger("jarvis-voice")

RENDER_TIMEOUT_S = 15.0

VOICE_DIR = Path.home() / ".jarvis" / "voices"
DEFAULT_VOICE_NAME = "en_GB-alan-medium"
DEFAULT_VOICE_PATH = VOICE_DIR / f"{DEFAULT_VOICE_NAME}.onnx"



class PiperTTS(tts.TTS):
    """LiveKit TTS plugin backed by a local Piper voice."""

    def __init__(
        self,
        *,
        model_path: str | Path | None = None,
        speaker_id: int | None = None,
        length_scale: float | None = None,
        strict_sentence_audio: bool = False,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=22050,
            num_channels=1,
        )
        self._model_path = resolve_voice_path(str(model_path) if model_path else None)
        self._speaker_id = speaker_id
        self._length_scale = resolve_length_scale(length_scale)
        self._voice = None
        # Fixed approval readback must fail if any sentence is unavailable.
        # Ordinary conversational TTS retains its existing streaming behavior.
        self._strict_sentence_audio = strict_sentence_audio
        self._lock = asyncio.Lock()
        # Async cancellation cannot stop a native inference already running.
        # Keep all model loads/renders/releases on one worker so interrupted
        # utterances cannot overlap or exhaust the shared asyncio executor.
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="jarvis-piper"
        )
        self._closed = False
        self._pending_work: set[threading.Event] = set()
        self._close_task: asyncio.Task | None = None

    @property
    def model(self) -> str:
        return self._model_path.stem

    @property
    def provider(self) -> str:
        return "piper-local"

    def _get_voice(self, *, cancelled: threading.Event | None = None):
        if self._closed:
            raise RuntimeError("Piper voice is closed")
        if cancelled is not None and cancelled.is_set():
            return None
        if self._voice is None:
            self._voice = load_piper_voice(self._model_path)
        # Adopt the voice's real sample rate (config-dependent).
        try:
            rate = int(self._voice.config.sample_rate)
        except Exception as exc:
            logger.warning("keeping default sample rate 22050: %s", exc)
        else:
            if rate > 0:
                self._sample_rate = rate
            else:
                logger.warning("keeping default sample rate 22050: bad rate %r", rate)
        return self._voice

    def _render_sentence(
        self, sentence: str, *, cancelled: threading.Event | None = None
    ) -> bytes:
        """Render one sentence to 16-bit PCM bytes (blocking; run in thread)."""
        voice = self._get_voice(cancelled=cancelled)
        if voice is None:
            return b""
        return render_sentence_pcm(
            voice,
            sentence,
            speaker_id=self._speaker_id,
            length_scale=self._length_scale,
            cancelled=cancelled,
            closed=lambda: self._closed,
        )

    async def _run_worker(self, function, *args):
        if self._closed:
            raise RuntimeError("Piper voice is closed")
        cancelled = threading.Event()
        self._pending_work.add(cancelled)
        context = contextvars.copy_context()

        def invoke():
            # A timed-out/cancelled queued utterance must never start inference.
            if self._closed or cancelled.is_set():
                return None
            return function(*args, cancelled=cancelled)

        try:
            return await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    self._executor, context.run, invoke
                ),
                RENDER_TIMEOUT_S,
            )
        except (asyncio.CancelledError, asyncio.TimeoutError):
            cancelled.set()
            raise
        finally:
            self._pending_work.discard(cancelled)

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        return _PiperChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    def stream(
        self,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.SynthesizeStream:
        if self._strict_sentence_audio:
            raise RuntimeError("Complete approval readback requires synthesize, not incremental text")
        return _PiperSynthesizeStream(tts=self, conn_options=conn_options)

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closed = True
            for cancelled in self._pending_work:
                cancelled.set()
            self._close_task = asyncio.create_task(self._close())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            # Model disposal must not race a native call, even if shutdown is
            # cancelled repeatedly. New utterances are already refused.
            while not self._close_task.done():
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(self._close_task)
            self._close_task.result()
            raise

    async def _close(self) -> None:
        def release_voice():
            self._voice = None

        try:
            await asyncio.get_running_loop().run_in_executor(
                self._executor, release_voice
            )
        finally:
            self._executor.shutdown(wait=False, cancel_futures=True)


class _PiperSynthesizeStream(tts.SynthesizeStream):
    """Sentence-by-sentence streaming synthesis.

    Consumes the framework's token channel; each flushed segment is
    rendered sentence by sentence in a worker thread. Cancelling lands
    between sentences, so interruption costs at most a sentence tail.
    """

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        plugin: PiperTTS = self._tts  # type: ignore[assignment]
        # Preload so the emitter below uses the voice's real rate.
        # Missing model: _render_sentence raises helpfully per text.
        with contextlib.suppress(RuntimeError):
            await plugin._run_worker(plugin._get_voice)
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=plugin.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=True,
        )
        buffer = ""
        async with plugin._lock:
            async for item in self._input_ch:
                if isinstance(item, str):
                    buffer += item
                elif isinstance(item, tts.SynthesizeStream._FlushSentinel):
                    if buffer.strip():
                        output_emitter.start_segment(segment_id=utils.shortuuid())
                        for sentence in sentence_split(buffer):
                            pcm = await plugin._run_worker(
                                plugin._render_sentence, sentence
                            )
                            if pcm:
                                output_emitter.push(pcm)
                        output_emitter.end_segment()
                    buffer = ""


class _PiperChunkedStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        plugin: PiperTTS = self._tts  # type: ignore[assignment]
        sentences = sentence_split(self._input_text)
        if sentences:
            await plugin._run_worker(plugin._get_voice)
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=plugin.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
        )
        async with plugin._lock:
            if plugin._strict_sentence_audio:
                if not sentences:
                    raise RuntimeError("Complete readback has no renderable sentences")
                rendered = []
                for sentence in sentences:
                    pcm = await plugin._run_worker(plugin._render_sentence, sentence)
                    if plugin._closed or not pcm or len(pcm) < 2 or len(pcm) % 2:
                        raise RuntimeError("Complete readback sentence audio is unavailable")
                    rendered.append(pcm)
                # Emit only after every sentence completed. A failed/cancelled
                # message cannot leave a recipient/code-only partial review.
                for pcm in rendered:
                    output_emitter.push(pcm)
            else:
                for sentence in sentences:
                    pcm = await plugin._run_worker(plugin._render_sentence, sentence)
                    if pcm:
                        output_emitter.push(pcm)
        output_emitter.flush()
