# Derived from livekit-agents (Apache-2.0), Copyright 2023 LiveKit, Inc.
# Sources: livekit/agents/stt/stt.py, livekit/agents/types.py,
# livekit/agents/utils/audio.py. Licensed under the Apache License, Version 2.0
# (http://www.apache.org/licenses/LICENSE-2.0). Trimmed to the surface the
# in-repo runtime and the local whisper/piper plugins use; no LiveKit import.
"""LiveKit-free STT contract and shared audio primitives for ``jarvis_rt``.

``AudioFrame`` mirrors the parts of ``rtc.AudioFrame`` that Jarvis reads:
``data`` (byte memoryview of little-endian int16 PCM), ``sample_rate``,
``num_channels`` and ``samples_per_channel``. ``AudioResampler`` is a
stateful windowed-sinc resampler (numpy only). ``Chan`` is a small closable
async FIFO used by the stream classes here and in ``tts.py``.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, Literal, TypeVar

import numpy as np

_T = TypeVar("_T")


class NotGiven:
    """Sentinel for "argument not supplied" (same semantics as LiveKit's)."""

    __slots__ = ()

    def __bool__(self) -> Literal[False]:
        return False

    def __repr__(self) -> str:
        return "NOT_GIVEN"


NOT_GIVEN = NotGiven()
NotGivenOr = _T | NotGiven


def is_given(value: object) -> bool:
    return value is not NOT_GIVEN


def shortuuid(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:12]


class APIError(Exception):
    """Retryable provider error (retried by ``STT.recognize``)."""

    def __init__(self, message: str = "", *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class APIConnectionError(APIError):
    pass


@dataclass
class APIConnectOptions:
    max_retry: int = 3
    retry_interval: float = 2.0
    timeout: float = 10.0

    def __post_init__(self) -> None:
        if self.max_retry < 0:
            raise ValueError("max_retry must be greater than or equal to 0")
        if self.retry_interval < 0:
            raise ValueError("retry_interval must be greater than or equal to 0")
        if self.timeout < 0:
            raise ValueError("timeout must be greater than or equal to 0")

    def _interval_for_retry(self, num_retries: int) -> float:
        """First retry is near-immediate; later retries wait ``retry_interval``."""
        if num_retries == 0:
            return 0.1
        return self.retry_interval


DEFAULT_API_CONNECT_OPTIONS = APIConnectOptions()


@dataclass
class AudioFrame:
    """Interleaved int16 PCM block, API-compatible with ``rtc.AudioFrame``.

    ``data`` is a read-only byte ``memoryview``; use ``np.frombuffer(f.data,
    np.int16)`` for samples and ``.tobytes()`` for raw bytes. Pass ``bytes``,
    ``bytearray``, ``memoryview`` or an int16 ``np.ndarray``.
    """

    data: memoryview | bytes | bytearray | np.ndarray
    sample_rate: int
    num_channels: int
    samples_per_channel: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.data, np.ndarray):
            raw = np.ascontiguousarray(self.data, dtype=np.int16).tobytes()
        else:
            raw = bytes(self.data)
        self.data = memoryview(raw)
        if self.num_channels <= 0:
            raise ValueError("num_channels must be positive")
        if self.samples_per_channel <= 0:
            self.samples_per_channel = len(raw) // (2 * self.num_channels)


AudioBuffer = AudioFrame | list[AudioFrame]


def combine_frames(frames: AudioBuffer) -> AudioFrame:
    """Concatenate frames sharing rate and channel count into one frame."""
    items = frames if isinstance(frames, list) else [frames]
    if not items:
        raise ValueError("no frames to combine")
    first = items[0]
    for f in items:
        if f.sample_rate != first.sample_rate or f.num_channels != first.num_channels:
            raise ValueError("cannot combine frames with different formats")
    raw = b"".join(bytes(f.data) for f in items)
    return AudioFrame(
        data=raw,
        sample_rate=first.sample_rate,
        num_channels=first.num_channels,
        samples_per_channel=len(raw) // (2 * first.num_channels),
    )


def _windowed_sinc(t: np.ndarray, cutoff: float, half: int) -> np.ndarray:
    """Blackman-windowed sinc, cutoff in cycles/sample, support |t| <= half."""
    window = 0.42 + 0.5 * np.cos(np.pi * t / half) + 0.08 * np.cos(2 * np.pi * t / half)
    window = np.where(np.abs(t) <= half, window, 0.0)
    return 2.0 * cutoff * np.sinc(2.0 * cutoff * t) * window


class AudioResampler:
    """Stateful int16 resampler (windowed sinc, numpy only).

    ``push`` returns the output frames that are fully determined by the input
    seen so far; ``flush`` drains the tail (zero-padded). Each channel is
    filtered independently. Cutoff tracks the lower of the two Nyquist rates,
    so downsampling is anti-aliased.
    """

    def __init__(
        self,
        input_rate: int,
        output_rate: int,
        *,
        num_channels: int = 1,
        quality: str = "high",
    ) -> None:
        if input_rate <= 0 or output_rate <= 0:
            raise ValueError("sample rates must be positive")
        if num_channels <= 0:
            raise ValueError("num_channels must be positive")
        self._in_rate = int(input_rate)
        self._out_rate = int(output_rate)
        self._channels = int(num_channels)
        ratio = self._in_rate / self._out_rate
        # Half-width in input samples: wider for decimation, longer for quality.
        per_side = 16 if quality == "high" else 6
        self._half = max(per_side, math.ceil(per_side * max(1.0, ratio)))
        # Cutoff relative to the input rate (cycles/sample); 0.95 margin.
        self._cutoff = 0.5 * min(1.0, self._out_rate / self._in_rate) * 0.95
        self._hist = np.zeros((0, self._channels), dtype=np.float64)
        self._base = 0  # absolute input index of _hist[0]
        self._next_out = 0  # next absolute output index to emit
        self._finished = False

    @property
    def input_rate(self) -> int:
        return self._in_rate

    @property
    def output_rate(self) -> int:
        return self._out_rate

    def _emit(self, end: int, final: bool) -> np.ndarray:
        """Output samples whose kernels lie before absolute input index ``end``."""
        if final:
            # Every output whose center precedes the end of input.
            count_end = (end * self._out_rate + self._in_rate - 1) // self._in_rate
        else:
            # Kernel fully inside the seen input: k*in/out + half < end.
            count_end = ((end - self._half - 1) * self._out_rate) // self._in_rate + 1
        ks = np.arange(self._next_out, max(self._next_out, count_end), dtype=np.int64)
        if ks.size == 0:
            return np.zeros((0, self._channels), dtype=np.float64)
        # Exact position of output k in input units: k * in / out.
        centers = ks * self._in_rate / self._out_rate
        i0 = np.floor(centers).astype(np.int64)
        offsets = np.arange(-self._half + 1, self._half + 1, dtype=np.int64)
        idx = i0[:, None] + offsets[None, :]  # (K, 2*half)
        t = idx - centers[:, None]
        weights = _windowed_sinc(t, self._cutoff, self._half)
        weights /= weights.sum(axis=1, keepdims=True)
        local = idx - self._base
        valid = (local >= 0) & (local < len(self._hist))
        if len(self._hist) == 0:
            return np.zeros((len(ks), self._channels), dtype=np.float64)
        gathered = np.where(
            valid[..., None], self._hist[np.clip(local, 0, len(self._hist) - 1)], 0.0
        )
        out = np.einsum("kj,kjc->kc", weights, gathered)
        self._next_out = int(ks[-1]) + 1
        return out

    def _trim(self) -> None:
        keep_from = (self._next_out * self._in_rate) // self._out_rate - self._half
        drop = max(0, keep_from - self._base)
        if drop:
            self._hist = self._hist[drop:]
            self._base += drop

    def _to_frames(self, samples: np.ndarray) -> list[AudioFrame]:
        if samples.shape[0] == 0:
            return []
        pcm = np.clip(np.rint(samples), -32768, 32767).astype(np.int16)
        return [
            AudioFrame(
                data=pcm.tobytes(),
                sample_rate=self._out_rate,
                num_channels=self._channels,
                samples_per_channel=pcm.shape[0],
            )
        ]

    def push(self, frame: AudioFrame) -> list[AudioFrame]:
        if self._finished:
            raise RuntimeError("resampler already flushed")
        if frame.sample_rate != self._in_rate or frame.num_channels != self._channels:
            raise ValueError("frame format does not match resampler configuration")
        pcm = np.frombuffer(frame.data, dtype=np.int16).astype(np.float64)
        block = pcm.reshape(-1, self._channels)
        self._hist = np.concatenate([self._hist, block], axis=0)
        end = self._base + len(self._hist)
        out = self._emit(end, final=False)
        self._trim()
        return self._to_frames(out)

    def flush(self) -> list[AudioFrame]:
        if self._finished:
            return []
        self._finished = True
        total = self._base + len(self._hist)
        # Zero-pad past the end so the last kernels see silence, not a cliff.
        pad = np.zeros((self._half, self._channels), dtype=np.float64)
        self._hist = np.concatenate([self._hist, pad], axis=0)
        out = self._emit(total, final=True)
        return self._to_frames(out)


class Chan(Generic[_T]):
    """Closable async FIFO; iteration ends after ``close()`` and a drain."""

    _CLOSED: Any = object()

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def send_nowait(self, item: _T) -> None:
        if self._closed:
            raise RuntimeError("channel is closed")
        self._queue.put_nowait(item)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._queue.put_nowait(self._CLOSED)

    def __aiter__(self) -> AsyncIterator[_T]:
        return self

    async def __anext__(self) -> _T:
        item = await self._queue.get()
        if item is self._CLOSED:
            self._queue.put_nowait(self._CLOSED)  # keep later readers terminated
            raise StopAsyncIteration
        return item


class SpeechEventType(str, Enum):
    START_OF_SPEECH = "start_of_speech"
    INTERIM_TRANSCRIPT = "interim_transcript"
    PREFLIGHT_TRANSCRIPT = "preflight_transcript"
    FINAL_TRANSCRIPT = "final_transcript"
    RECOGNITION_USAGE = "recognition_usage"
    END_OF_SPEECH = "end_of_speech"


@dataclass
class SpeechData:
    language: str
    text: str
    start_time: float = 0.0
    end_time: float = 0.0
    confidence: float = 0.0  # [0, 1]
    speaker_id: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class SpeechEvent:
    type: SpeechEventType
    request_id: str = ""
    alternatives: list[SpeechData] = field(default_factory=list)
    speech_start_time: float | None = None
    speech_end_time: float | None = None


@dataclass
class STTCapabilities:
    streaming: bool
    interim_results: bool
    offline_recognize: bool = True


class STT(ABC):
    """Speech-to-text provider. Subclasses implement ``_recognize_impl``."""

    def __init__(self, *, capabilities: STTCapabilities) -> None:
        self._capabilities = capabilities
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
    def capabilities(self) -> STTCapabilities:
        return self._capabilities

    @abstractmethod
    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> SpeechEvent: ...

    async def recognize(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> SpeechEvent:
        for attempt in range(conn_options.max_retry + 1):
            try:
                return await self._recognize_impl(
                    buffer, language=language, conn_options=conn_options
                )
            except APIError:
                if attempt == conn_options.max_retry:
                    raise
                await asyncio.sleep(conn_options._interval_for_retry(attempt))
        raise RuntimeError("unreachable")

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> RecognizeStream:
        raise NotImplementedError("streaming is not supported by this STT")

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> STT:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


class RecognizeStream(ABC):
    """Push audio in, iterate ``SpeechEvent``s out.

    Subclasses implement ``_run``: read ``self._input_ch`` (frames and
    ``_FlushSentinel`` markers until closed) and emit into ``self._event_ch``.
    """

    class _FlushSentinel:
        """Marks the end of one recognition segment."""

    def __init__(
        self,
        *,
        stt: STT,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        sample_rate: NotGivenOr[int] = NOT_GIVEN,
    ) -> None:
        self._stt = stt
        self._conn_options = conn_options
        self._input_ch: Chan[AudioFrame | RecognizeStream._FlushSentinel] = Chan()
        self._event_ch: Chan[SpeechEvent] = Chan()
        self._needed_sr = sample_rate if is_given(sample_rate) else None
        self._pushed_sr = 0
        self._resampler: AudioResampler | None = None
        self._task = asyncio.create_task(self._main_task())
        self._task.add_done_callback(lambda _: self._event_ch.close())

    @property
    def stt(self) -> STT:
        return self._stt

    @abstractmethod
    async def _run(self) -> None: ...

    async def _main_task(self) -> None:
        await self._run()

    def push_frame(self, frame: AudioFrame) -> None:
        self._check_input_open()
        if self._pushed_sr and self._pushed_sr != frame.sample_rate:
            raise ValueError("the sample rate of the input frames must be consistent")
        self._pushed_sr = frame.sample_rate
        if self._needed_sr and self._needed_sr != frame.sample_rate:
            if self._resampler is None:
                self._resampler = AudioResampler(
                    frame.sample_rate, self._needed_sr, num_channels=frame.num_channels
                )
            frames = self._resampler.push(frame)
        else:
            frames = [frame]
        for out in frames:
            self._input_ch.send_nowait(out)

    def flush(self) -> None:
        self._check_input_open()
        if self._resampler is not None:
            for out in self._resampler.flush():
                self._input_ch.send_nowait(out)
        self._input_ch.send_nowait(self._FlushSentinel())

    def end_input(self) -> None:
        self.flush()
        self._input_ch.close()

    async def aclose(self) -> None:
        self._input_ch.close()
        if not self._task.done():
            self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._event_ch.close()

    def _check_input_open(self) -> None:
        if self._input_ch.closed:
            raise RuntimeError("input already ended or stream closed")

    def __aiter__(self) -> AsyncIterator[SpeechEvent]:
        return self

    async def __anext__(self) -> SpeechEvent:
        try:
            return await self._event_ch.__anext__()
        except StopAsyncIteration:
            if not self._task.cancelled() and (exc := self._task.exception()):
                raise exc from None
            raise

    async def __aenter__(self) -> RecognizeStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
