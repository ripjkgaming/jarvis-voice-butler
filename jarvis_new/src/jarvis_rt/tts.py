# Derived from livekit-agents (Apache-2.0), Copyright 2023 LiveKit, Inc.
# Sources: livekit/agents/tts/tts.py (TTS, ChunkedStream, SynthesizeStream,
# AudioEmitter, SynthesizedAudio). Licensed under the Apache License, Version
# 2.0 (http://www.apache.org/licenses/LICENSE-2.0). Trimmed to PCM emission
# without markup, retries, metrics or tracing; no LiveKit import.
"""LiveKit-free TTS contract for ``jarvis_rt``.

A provider subclasses :class:`TTS` and returns either a :class:`ChunkedStream`
from ``synthesize`` (whole text in, PCM out) or a :class:`SynthesizeStream`
from ``stream`` (text tokens in, PCM out per flushed segment). Both implement
``_run(output_emitter)`` and push PCM bytes through the :class:`AudioEmitter`.
Cancelling a stream (``aclose``) cancels ``_run`` at its next ``await``.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

from .stt import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    APIError,
    AudioFrame,
    Chan,
    NotGivenOr,
    combine_frames,
    shortuuid,
)

__all__ = [
    "DEFAULT_API_CONNECT_OPTIONS",
    "NOT_GIVEN",
    "TTS",
    "APIConnectOptions",
    "AudioEmitter",
    "ChunkedStream",
    "NotGivenOr",
    "SynthesizeStream",
    "SynthesizedAudio",
    "TTSCapabilities",
    "shortuuid",
]


@dataclass
class SynthesizedAudio:
    frame: AudioFrame
    request_id: str
    is_final: bool = False
    segment_id: str = ""
    delta_text: str = ""


@dataclass
class TTSCapabilities:
    streaming: bool
    aligned_transcript: bool = False


class TTS(ABC):
    """Text-to-speech provider. Subclasses implement ``synthesize``."""

    def __init__(
        self,
        *,
        capabilities: TTSCapabilities,
        sample_rate: int,
        num_channels: int,
    ) -> None:
        self._capabilities = capabilities
        self._sample_rate = sample_rate
        self._num_channels = num_channels
        self._label = f"{type(self).__module__}.{type(self).__name__}"

    @property
    def label(self) -> str:
        return self._label

    @property
    def model(self) -> str:
        return "unknown"

    @property
    def provider(self) -> str:
        return "unknown"

    @property
    def capabilities(self) -> TTSCapabilities:
        return self._capabilities

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def num_channels(self) -> int:
        return self._num_channels

    @abstractmethod
    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> ChunkedStream: ...

    def stream(
        self,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> SynthesizeStream:
        raise NotImplementedError("streaming is not supported by this TTS")

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> TTS:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


class AudioEmitter:
    """Receives PCM bytes from ``_run`` and forwards ``SynthesizedAudio``.

    Each ``push`` becomes one frame, emitted immediately. Segments only tag
    the frames they carry; ``end_segment`` and ``flush`` mark boundaries.
    """

    def __init__(self, *, dst: Chan[SynthesizedAudio]) -> None:
        self._dst = dst
        self._initialized = False
        self._request_id = ""
        self._sample_rate = 0
        self._num_channels = 0
        self._segment_id = ""
        self._pushed_bytes = 0

    def initialize(
        self,
        *,
        request_id: str,
        sample_rate: int,
        num_channels: int,
        mime_type: str,
        stream: bool = False,
    ) -> None:
        if mime_type != "audio/pcm":
            raise ValueError(f"unsupported mime type: {mime_type}")
        if sample_rate <= 0 or num_channels <= 0:
            raise ValueError("sample_rate and num_channels must be positive")
        self._initialized = True
        self._request_id = request_id
        self._sample_rate = int(sample_rate)
        self._num_channels = int(num_channels)

    def start_segment(self, *, segment_id: str) -> None:
        self._require_initialized()
        self._segment_id = segment_id

    def push(self, data: bytes) -> None:
        self._require_initialized()
        if not data:
            return
        if len(data) % (2 * self._num_channels):
            raise ValueError("PCM data is not whole int16 frames")
        frame = AudioFrame(
            data=bytes(data),
            sample_rate=self._sample_rate,
            num_channels=self._num_channels,
        )
        self._pushed_bytes += len(data)
        self._dst.send_nowait(
            SynthesizedAudio(
                frame=frame,
                request_id=self._request_id,
                segment_id=self._segment_id,
            )
        )

    def end_segment(self) -> None:
        self._require_initialized()
        self._segment_id = ""

    def flush(self) -> None:
        self._require_initialized()

    def pushed_duration(self) -> float:
        if not self._sample_rate:
            return 0.0
        return self._pushed_bytes / (2 * self._num_channels * self._sample_rate)

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("AudioEmitter.initialize() must be called first")


class _AudioStreamBase:
    """Shared consumer side: iterate ``SynthesizedAudio`` from an event channel."""

    _task: asyncio.Task
    _event_ch: Chan[SynthesizedAudio]

    def __aiter__(self) -> AsyncIterator[SynthesizedAudio]:
        return self

    async def __anext__(self) -> SynthesizedAudio:
        try:
            return await self._event_ch.__anext__()
        except StopAsyncIteration:
            if not self._task.cancelled() and (exc := self._task.exception()):
                raise exc from None
            raise

    async def aclose(self) -> None:
        if not self._task.done():
            self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._event_ch.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


class ChunkedStream(_AudioStreamBase, ABC):
    """Whole-text synthesis: ``_run`` renders ``self._input_text``."""

    def __init__(
        self,
        *,
        tts: TTS,
        input_text: str,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> None:
        self._tts = tts
        self._input_text = input_text
        self._conn_options = conn_options
        self._event_ch = Chan[SynthesizedAudio]()
        self._task = asyncio.create_task(self._main_task())
        self._task.add_done_callback(lambda _: self._event_ch.close())

    @property
    def input_text(self) -> str:
        return self._input_text

    def done(self) -> bool:
        return self._task.done()

    @abstractmethod
    async def _run(self, output_emitter: AudioEmitter) -> None: ...

    async def _main_task(self) -> None:
        emitter = AudioEmitter(dst=self._event_ch)
        await self._run(emitter)
        if self._input_text.strip() and emitter.pushed_duration() <= 0.0:
            raise APIError(
                f"no audio frames were pushed for text: {self._input_text}",
                retryable=False,
            )

    async def collect(self) -> AudioFrame:
        frames = [ev.frame async for ev in self]
        return combine_frames(frames)


class SynthesizeStream(_AudioStreamBase, ABC):
    """Incremental synthesis: push text tokens, ``flush`` ends a segment."""

    class _FlushSentinel:
        """Marks the end of one synthesized segment."""

    def __init__(
        self,
        *,
        tts: TTS,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> None:
        self._tts = tts
        self._conn_options = conn_options
        self._input_ch: Chan[str | SynthesizeStream._FlushSentinel] = Chan()
        self._event_ch = Chan[SynthesizedAudio]()
        self._task = asyncio.create_task(self._main_task())
        self._task.add_done_callback(lambda _: self._event_ch.close())

    @abstractmethod
    async def _run(self, output_emitter: AudioEmitter) -> None: ...

    async def _main_task(self) -> None:
        emitter = AudioEmitter(dst=self._event_ch)
        await self._run(emitter)

    def _check_input_open(self) -> None:
        if self._input_ch.closed:
            raise RuntimeError("input already ended or stream closed")

    def push_text(self, token: str) -> None:
        self._check_input_open()
        self._input_ch.send_nowait(token)

    def flush(self) -> None:
        self._check_input_open()
        self._input_ch.send_nowait(self._FlushSentinel())

    def end_input(self) -> None:
        self.flush()
        self._input_ch.close()
