# Derived from livekit-agents / livekit-plugins-silero (Apache-2.0), Copyright 2023 LiveKit, Inc.
# Sources: livekit/plugins/silero/vad.py, onnx_model.py; livekit/agents/vad.py;
# livekit/agents/utils/exp_filter.py.
# Licensed under the Apache License, Version 2.0 (http://www.apache.org/licenses/LICENSE-2.0).
"""Silero voice activity detection on onnxruntime, without LiveKit.

``SileroVAD.load`` takes the same endpointing knobs as the LiveKit plugin
(``min_speech_duration``, ``min_silence_duration``, ``activation_threshold``
...). ``SileroVAD.stream()`` returns a ``SileroVADStream``: push int16
``AudioFrame``s, iterate ``VADEvent``s. The state machine is a line-for-line
port of the LiveKit plugin's ``_main_task`` so endpointing matches it. The
ONNX model ships in ``resources/silero_vad.onnx``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import numpy as np
import onnxruntime

from .stt import NOT_GIVEN, AudioFrame, AudioResampler, Chan, NotGivenOr, is_given

logger = logging.getLogger("jarvis-vad")

DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "resources" / "silero_vad.onnx"
SUPPORTED_SAMPLE_RATES = (8000, 16000)
SLOW_INFERENCE_THRESHOLD = 0.2  # seconds; late by 200ms
_PROBABILITY_ALPHA = 0.35  # ExpFilter smoothing used by the LiveKit plugin


class VADEventType(str, Enum):
    START_OF_SPEECH = "start_of_speech"
    INFERENCE_DONE = "inference_done"
    END_OF_SPEECH = "end_of_speech"


@dataclass
class VADEvent:
    type: VADEventType
    samples_index: int
    timestamp: float
    speech_duration: float
    silence_duration: float
    frames: list[AudioFrame] = field(default_factory=list)
    probability: float = 0.0
    inference_duration: float = 0.0
    speaking: bool = False
    raw_accumulated_silence: float = 0.0
    raw_accumulated_speech: float = 0.0


@dataclass
class VADCapabilities:
    update_interval: float


class VAD(ABC):
    """Voice activity detector. Subclasses create one ``VADStream`` per input."""

    def __init__(self, *, capabilities: VADCapabilities) -> None:
        self._capabilities = capabilities
        self._label = f"{type(self).__module__}.{type(self).__name__}"

    @property
    def label(self) -> str:
        return self._label

    @property
    def capabilities(self) -> VADCapabilities:
        return self._capabilities

    @property
    def model(self) -> str:
        return "unknown"

    @property
    def provider(self) -> str:
        return "unknown"

    @abstractmethod
    def stream(self) -> VADStream: ...

    async def aclose(self) -> None:
        return None


def new_inference_session(
    force_cpu: bool, onnx_file_path: str | Path | None = None
) -> onnxruntime.InferenceSession:
    """Single-threaded, non-spinning ORT session (same options as LiveKit)."""
    path = Path(onnx_file_path) if onnx_file_path else DEFAULT_MODEL_PATH
    if not path.is_file():
        raise FileNotFoundError(f"Silero VAD model file not found: {path}")
    opts = onnxruntime.SessionOptions()
    opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
    opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
    opts.inter_op_num_threads = 1
    opts.intra_op_num_threads = 1
    opts.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
    if force_cpu and "CPUExecutionProvider" in onnxruntime.get_available_providers():
        return onnxruntime.InferenceSession(
            str(path), providers=["CPUExecutionProvider"], sess_options=opts
        )
    return onnxruntime.InferenceSession(str(path), sess_options=opts)


class OnnxModel:
    """Stateful Silero model: one 512-sample (16 kHz) or 256-sample (8 kHz) window per call."""

    def __init__(
        self, *, onnx_session: onnxruntime.InferenceSession, sample_rate: int
    ) -> None:
        if sample_rate not in SUPPORTED_SAMPLE_RATES:
            raise ValueError("Silero VAD only supports 8KHz and 16KHz sample rates")
        self._sess = onnx_session
        self._sample_rate = sample_rate
        if sample_rate == 8000:
            self._window_size_samples = 256
            self._context_size = 32
        else:
            self._window_size_samples = 512
            self._context_size = 64
        self._sample_rate_nd = np.array(sample_rate, dtype=np.int64)
        self._context = np.zeros((1, self._context_size), dtype=np.float32)
        self._rnn_state = np.zeros((2, 1, 128), dtype=np.float32)
        self._input_buffer = np.zeros(
            (1, self._context_size + self._window_size_samples), dtype=np.float32
        )

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def window_size_samples(self) -> int:
        return self._window_size_samples

    def reset(self) -> None:
        self._context.fill(0)
        self._rnn_state.fill(0)
        self._input_buffer.fill(0)

    def __call__(self, x: np.ndarray) -> float:
        self._input_buffer[:, : self._context_size] = self._context
        self._input_buffer[:, self._context_size :] = x
        out, self._rnn_state = self._sess.run(
            None,
            {
                "input": self._input_buffer,
                "state": self._rnn_state,
                "sr": self._sample_rate_nd,
            },
        )
        self._context = self._input_buffer[:, -self._context_size :].copy()
        return float(out.item())


class _ExpFilter:
    """Exponential smoothing; the first sample seeds the filter."""

    def __init__(self, alpha: float) -> None:
        self._alpha = alpha
        self._filtered: float | None = None

    def apply(self, sample: float) -> float:
        if self._filtered is None:
            self._filtered = sample
        else:
            self._filtered = self._alpha * self._filtered + (1 - self._alpha) * sample
        return self._filtered


@dataclass
class _VADOptions:
    min_speech_duration: float
    min_silence_duration: float
    prefix_padding_duration: float
    max_buffered_speech: float
    activation_threshold: float
    deactivation_threshold: float
    sample_rate: int


class VADStream(ABC):
    """Push frames in, iterate ``VADEvent``s out. Subclasses implement ``_run``."""

    class _FlushSentinel:
        """Resets the detector state (start of a new utterance context)."""

    def __init__(self, vad: VAD) -> None:
        self._vad = vad
        self._input_ch: Chan[AudioFrame | VADStream._FlushSentinel] = Chan()
        self._event_ch: Chan[VADEvent] = Chan()
        self._task = asyncio.create_task(self._run())
        self._task.add_done_callback(lambda _: self._event_ch.close())

    @abstractmethod
    async def _run(self) -> None: ...

    def push_frame(self, frame: AudioFrame) -> None:
        self._check_input_open()
        self._input_ch.send_nowait(frame)

    def flush(self) -> None:
        self._check_input_open()
        self._input_ch.send_nowait(self._FlushSentinel())

    def end_input(self) -> None:
        self._input_ch.close()

    async def aclose(self) -> None:
        self._input_ch.close()
        if not self._task.done():
            self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._event_ch.close()

    def _check_input_open(self) -> None:
        if self._input_ch.closed:
            raise RuntimeError("VAD input already ended or stream closed")

    def __aiter__(self) -> AsyncIterator[VADEvent]:
        return self

    async def __anext__(self) -> VADEvent:
        try:
            return await self._event_ch.__anext__()
        except StopAsyncIteration:
            if not self._task.cancelled() and (exc := self._task.exception()):
                raise exc from None
            raise

    async def __aenter__(self) -> VADStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


class SileroVAD(VAD):
    """Silero VAD over onnxruntime. Build with :meth:`load`."""

    def __init__(
        self, *, session: onnxruntime.InferenceSession, opts: _VADOptions
    ) -> None:
        super().__init__(capabilities=VADCapabilities(update_interval=0.032))
        self._onnx_session = session
        self._opts = opts

    @classmethod
    def load(
        cls,
        *,
        min_speech_duration: float = 0.05,
        min_silence_duration: float = 0.55,
        prefix_padding_duration: float = 0.5,
        max_buffered_speech: float = 60.0,
        activation_threshold: float = 0.5,
        sample_rate: int = 16000,
        force_cpu: bool = True,
        onnx_file_path: NotGivenOr[str | Path] = NOT_GIVEN,
        deactivation_threshold: NotGivenOr[float] = NOT_GIVEN,
    ) -> SileroVAD:
        """Load the ONNX model (blocking, ~tens of ms once the file is cached)."""
        if sample_rate not in SUPPORTED_SAMPLE_RATES:
            raise ValueError("Silero VAD only supports 8KHz and 16KHz sample rates")
        if is_given(deactivation_threshold) and deactivation_threshold <= 0:
            raise ValueError("deactivation_threshold must be greater than 0")
        session = new_inference_session(
            force_cpu, onnx_file_path if is_given(onnx_file_path) else None
        )
        opts = _VADOptions(
            min_speech_duration=min_speech_duration,
            min_silence_duration=min_silence_duration,
            prefix_padding_duration=prefix_padding_duration,
            max_buffered_speech=max_buffered_speech,
            activation_threshold=activation_threshold,
            deactivation_threshold=(
                deactivation_threshold
                if is_given(deactivation_threshold)
                else max(activation_threshold - 0.15, 0.01)
            ),
            sample_rate=sample_rate,
        )
        return cls(session=session, opts=opts)

    @property
    def model(self) -> str:
        return "silero"

    @property
    def provider(self) -> str:
        return "ONNX"

    @property
    def min_silence_duration(self) -> float:
        return self._opts.min_silence_duration

    def stream(self) -> SileroVADStream:
        model = OnnxModel(
            onnx_session=self._onnx_session, sample_rate=self._opts.sample_rate
        )
        return SileroVADStream(self, self._opts, model)


class SileroVADStream(VADStream):
    """Streaming endpointing. Port of the LiveKit plugin's ``VADStream._main_task``."""

    def __init__(self, vad: VAD, opts: _VADOptions, model: OnnxModel) -> None:
        self._opts = opts
        self._model = model
        self._input_sample_rate = 0
        self._speech_buffer: np.ndarray | None = None
        self._speech_buffer_index = 0
        self._speech_buffer_max_reached = False
        self._prefix_padding_samples = 0
        self._resampler: AudioResampler | None = None
        self._exp_filter = _ExpFilter(_PROBABILITY_ALPHA)
        self._reset_pub_state()
        super().__init__(vad)

    def _reset_pub_state(self) -> None:
        self.pub_speaking = False
        self.pub_speech_duration = 0.0
        self.pub_silence_duration = 0.0
        self.pub_current_sample = 0
        self.pub_timestamp = 0.0
        self.speech_threshold_duration = 0.0
        self.silence_threshold_duration = 0.0
        self.input_copy_remaining_fract = 0.0
        self.extra_inference_time = 0.0

    def _reset_state(self) -> None:
        self._model.reset()
        self._exp_filter = _ExpFilter(_PROBABILITY_ALPHA)
        self._speech_buffer_index = 0
        self._speech_buffer_max_reached = False
        if self._speech_buffer is not None:
            self._speech_buffer.fill(0)
        self._reset_pub_state()
        self._input_pcm = np.zeros(0, dtype=np.int16)
        self._inference_pcm = np.zeros(0, dtype=np.int16)
        if (
            self._input_sample_rate
            and self._input_sample_rate != self._opts.sample_rate
        ):
            self._resampler = AudioResampler(
                self._input_sample_rate,
                self._opts.sample_rate,
                num_channels=1,
                quality="quick",
            )
        else:
            self._resampler = None

    def _reset_write_cursor(self) -> None:
        assert self._speech_buffer is not None
        if self._speech_buffer_index <= self._prefix_padding_samples:
            return
        start = self._speech_buffer_index - self._prefix_padding_samples
        padding = self._speech_buffer[start : self._speech_buffer_index].copy()
        self._speech_buffer_max_reached = False
        self._speech_buffer[: self._prefix_padding_samples] = padding
        self._speech_buffer_index = self._prefix_padding_samples

    def _copy_speech_buffer(self) -> AudioFrame:
        assert self._speech_buffer is not None
        return AudioFrame(
            data=self._speech_buffer[: self._speech_buffer_index].tobytes(),
            sample_rate=self._input_sample_rate,
            num_channels=1,
            samples_per_channel=self._speech_buffer_index,
        )

    def _send(self, event: VADEvent) -> None:
        self._event_ch.send_nowait(event)

    async def _run(self) -> None:
        opts = self._opts
        model = self._model
        window = model.window_size_samples
        loop = asyncio.get_running_loop()
        self._input_pcm = np.zeros(0, dtype=np.int16)
        self._inference_pcm = np.zeros(0, dtype=np.int16)
        inference_f32 = np.empty(window, dtype=np.float32)

        async for item in self._input_ch:
            if isinstance(item, VADStream._FlushSentinel):
                self._reset_state()
                continue
            if not isinstance(item, AudioFrame):
                continue

            if not self._input_sample_rate:
                self._input_sample_rate = item.sample_rate
                self._prefix_padding_samples = int(
                    opts.prefix_padding_duration * self._input_sample_rate
                )
                self._speech_buffer = np.empty(
                    int(opts.max_buffered_speech * self._input_sample_rate)
                    + self._prefix_padding_samples,
                    dtype=np.int16,
                )
                if self._input_sample_rate != opts.sample_rate:
                    self._resampler = AudioResampler(
                        self._input_sample_rate,
                        opts.sample_rate,
                        num_channels=1,
                        quality="quick",
                    )
            elif self._input_sample_rate != item.sample_rate:
                logger.error("a frame with another sample rate was already pushed")
                continue

            assert self._speech_buffer is not None
            pcm = np.frombuffer(item.data, dtype=np.int16)
            self._input_pcm = np.concatenate([self._input_pcm, pcm])
            if self._resampler is not None:
                resampled = self._resampler.push(item)
                if resampled:
                    self._inference_pcm = np.concatenate(
                        [self._inference_pcm]
                        + [np.frombuffer(f.data, dtype=np.int16) for f in resampled]
                    )
            else:
                self._inference_pcm = np.concatenate([self._inference_pcm, pcm])

            while self._inference_pcm.size >= window:
                start_time = time.perf_counter()
                np.divide(
                    self._inference_pcm[:window],
                    np.iinfo(np.int16).max,
                    out=inference_f32,
                    dtype=np.float32,
                )
                p = await loop.run_in_executor(None, model, inference_f32)
                p = self._exp_filter.apply(p)

                window_duration = window / model.sample_rate
                self.pub_current_sample += window
                self.pub_timestamp += window_duration

                resampling_ratio = self._input_sample_rate / model.sample_rate
                to_copy = window * resampling_ratio + self.input_copy_remaining_fract
                to_copy_int = int(to_copy)
                self.input_copy_remaining_fract = to_copy - to_copy_int

                available_space = len(self._speech_buffer) - self._speech_buffer_index
                to_copy_buffer = min(to_copy_int, available_space)
                if to_copy_buffer > 0:
                    chunk = self._input_pcm[:to_copy_buffer]
                    end = self._speech_buffer_index + len(chunk)
                    self._speech_buffer[self._speech_buffer_index : end] = chunk
                    self._speech_buffer_index = end
                elif not self._speech_buffer_max_reached:
                    self._speech_buffer_max_reached = True
                    logger.warning(
                        "max_buffered_speech reached, ignoring further data for the current speech input"
                    )

                inference_duration = time.perf_counter() - start_time
                self.extra_inference_time = max(
                    0.0,
                    self.extra_inference_time + inference_duration - window_duration,
                )
                if inference_duration > SLOW_INFERENCE_THRESHOLD:
                    logger.warning(
                        "VAD inference is slower than realtime",
                        extra={"delay": self.extra_inference_time},
                    )

                if self.pub_speaking:
                    self.pub_speech_duration += window_duration
                else:
                    self.pub_silence_duration += window_duration

                self._send(
                    VADEvent(
                        type=VADEventType.INFERENCE_DONE,
                        samples_index=self.pub_current_sample,
                        timestamp=self.pub_timestamp,
                        silence_duration=self.pub_silence_duration,
                        speech_duration=self.pub_speech_duration,
                        probability=p,
                        inference_duration=inference_duration,
                        frames=[
                            AudioFrame(
                                data=self._input_pcm[:to_copy_int].tobytes(),
                                sample_rate=self._input_sample_rate,
                                num_channels=1,
                                samples_per_channel=min(
                                    to_copy_int, len(self._input_pcm)
                                ),
                            )
                        ],
                        speaking=self.pub_speaking,
                        raw_accumulated_silence=self.silence_threshold_duration,
                        raw_accumulated_speech=self.speech_threshold_duration,
                    )
                )

                if p >= opts.activation_threshold or (
                    self.pub_speaking and p > opts.deactivation_threshold
                ):
                    self.speech_threshold_duration += window_duration
                    self.silence_threshold_duration = 0.0
                    if not self.pub_speaking and (
                        self.speech_threshold_duration >= opts.min_speech_duration
                    ):
                        self.pub_speaking = True
                        self.pub_silence_duration = 0.0
                        self.pub_speech_duration = self.speech_threshold_duration
                        self._send(
                            VADEvent(
                                type=VADEventType.START_OF_SPEECH,
                                samples_index=self.pub_current_sample,
                                timestamp=self.pub_timestamp,
                                silence_duration=self.pub_silence_duration,
                                speech_duration=self.pub_speech_duration,
                                frames=[self._copy_speech_buffer()],
                                speaking=True,
                            )
                        )
                else:
                    self.silence_threshold_duration += window_duration
                    self.speech_threshold_duration = 0.0
                    if not self.pub_speaking:
                        self._reset_write_cursor()
                    if (
                        self.pub_speaking
                        and self.silence_threshold_duration >= opts.min_silence_duration
                    ):
                        self.pub_speaking = False
                        self.pub_silence_duration = self.silence_threshold_duration
                        self._send(
                            VADEvent(
                                type=VADEventType.END_OF_SPEECH,
                                samples_index=self.pub_current_sample,
                                timestamp=self.pub_timestamp,
                                silence_duration=self.pub_silence_duration,
                                speech_duration=max(
                                    0.0,
                                    self.pub_speech_duration
                                    - self.silence_threshold_duration,
                                ),
                                frames=[self._copy_speech_buffer()],
                                speaking=False,
                            )
                        )
                        self.pub_speech_duration = 0.0
                        self._reset_write_cursor()

                # Consume the samples this window used; keep the remainder.
                self._input_pcm = self._input_pcm[to_copy_int:]
                self._inference_pcm = self._inference_pcm[window:]
