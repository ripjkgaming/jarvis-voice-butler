"""Local ears for the cloud-free `direct` pipeline (JARVIS_PIPELINE=direct).

``FasterWhisperSTT`` is a batch (non-streaming) LiveKit STT plugin around a
local faster-whisper model (Systran weights, already in the HF cache from
the Phase 3 audit: tiny/small/base/medium + large-v3-turbo). The voice
session endpoint-segments audio with the local Silero VAD and calls
``recognize()`` per utterance — no network, no inference credits, no keys.

Model selection: ``JARVIS_WHISPER_MODEL`` (default ``base`` — best
accuracy/latency trade on CPU; ``tiny`` for weak boxes, ``small``/``medium``
for accuracy). ``JARVIS_WHISPER_DEVICE`` (default ``cpu``) and
``JARVIS_WHISPER_COMPUTE`` (default ``int8``) are honored verbatim.
The model loads lazily on first recognize so imports stay light and the
voice model choice never blocks worker startup.
"""

from __future__ import annotations

import asyncio
import logging
import os

import numpy as np
from livekit.agents import stt, utils
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)

logger = logging.getLogger("jarvis-local-stt")

DEFAULT_MODEL = "base"
TARGET_RATE = 16000


def whisper_model_name() -> str:
    """Configured faster-whisper size. Pure (env only)."""
    return (
        os.environ.get("JARVIS_WHISPER_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    )


def frames_to_mono16k(buffer: utils.AudioBuffer) -> np.ndarray:
    """Normalize LiveKit frames to mono float32 16kHz for whisper.

    Pure DSP (no model): concat int16 frames, downmix to mono, resample to
    16k. Integer-ratio rates decimate directly (48k→16k is the hot path);
    anything else goes through rtc.AudioResampler.
    """
    from livekit import rtc

    frames = buffer if isinstance(buffer, list) else [buffer]
    if not frames:
        return np.zeros(0, dtype=np.float32)
    rate = frames[0].sample_rate
    chunks: list[np.ndarray] = []
    for f in frames:
        pcm = np.frombuffer(f.data, dtype=np.int16)
        if f.num_channels > 1:
            pcm = pcm.reshape(-1, f.num_channels).mean(axis=1).astype(np.int16)
        chunks.append(pcm)
    mono = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    if rate == TARGET_RATE:
        out = mono
    elif rate % TARGET_RATE == 0:
        out = mono[:: rate // TARGET_RATE]
    else:
        resampler = rtc.AudioResampler(
            input_rate=rate, output_rate=TARGET_RATE, num_channels=1
        )
        out_frames = resampler.push(
            rtc.AudioFrame(
                data=mono.tobytes(),
                sample_rate=rate,
                num_channels=1,
                samples_per_channel=len(mono),
            )
        )
        out = np.frombuffer(b"".join(f.data for f in out_frames), dtype=np.int16)
    return out.astype(np.float32) / 32768.0


class FasterWhisperSTT(stt.STT):
    """Batch STT backed by a local faster-whisper model. No network."""

    def __init__(
        self,
        model_size: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        self._model_size = model_size or whisper_model_name()
        self._device = device or os.environ.get("JARVIS_WHISPER_DEVICE", "cpu")
        self._compute_type = compute_type or os.environ.get(
            "JARVIS_WHISPER_COMPUTE", "int8"
        )
        self._model = None

    @property
    def model(self) -> str:
        return f"faster-whisper-{self._model_size}"

    @property
    def provider(self) -> str:
        return "faster-whisper-local"

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            logger.info(
                "loading local whisper model=%s device=%s compute=%s",
                self._model_size,
                self._device,
                self._compute_type,
            )
            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
            )
        return self._model

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        audio = frames_to_mono16k(buffer)
        if audio.size < TARGET_RATE // 4:  # <250ms: nothing to transcribe
            return stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                alternatives=[stt.SpeechData(language="en", text="")],
            )
        lang = language if utils.is_given(language) else "en"
        model = await asyncio.to_thread(self._load)
        segments, _info = await asyncio.to_thread(
            model.transcribe, audio, language=lang, beam_size=1, vad_filter=False
        )
        texts: list[str] = []
        logprobs: list[float] = []
        for seg in segments:
            texts.append(seg.text.strip())
            logprobs.append(seg.avg_logprob)
        text = " ".join(t for t in texts if t).strip()
        confidence = 0.0
        if logprobs:
            import math

            confidence = min(1.0, max(0.0, math.exp(sum(logprobs) / len(logprobs))))
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                stt.SpeechData(language=lang, text=text, confidence=confidence)
            ],
        )
