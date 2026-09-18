"""Tests for the cloud-free direct pipeline (local whisper STT)."""

import numpy as np
import pytest
from livekit import rtc

import agent as agent_mod
from agent import _pipeline_name
from local_stt import FasterWhisperSTT, frames_to_mono16k, whisper_model_name


def _frame(data: np.ndarray, rate: int, channels: int = 1) -> rtc.AudioFrame:
    return rtc.AudioFrame(
        data=data.astype(np.int16).tobytes(),
        sample_rate=rate,
        num_channels=channels,
        samples_per_channel=len(data) // channels,
    )


def test_whisper_model_name_default_and_override(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_WHISPER_MODEL", raising=False)
    assert whisper_model_name() == "base"
    monkeypatch.setenv("JARVIS_WHISPER_MODEL", "tiny")
    assert whisper_model_name() == "tiny"


def test_frames_to_mono16k_downmix_and_decimate() -> None:
    left = np.ones(4800, dtype=np.int16) * 1000
    right = np.ones(4800, dtype=np.int16) * 3000
    stereo = np.empty(9600, dtype=np.int16)
    stereo[0::2] = left
    stereo[1::2] = right
    out = frames_to_mono16k(_frame(stereo, 48000, 2))
    assert out.dtype == np.float32
    assert len(out) == 1600  # 4800/3: mono + 48k->16k
    assert abs(float(out.mean()) - 2000 / 32768.0) < 1e-3


def test_frames_to_mono16k_passthrough_and_empty() -> None:
    mono16 = np.zeros(1600, dtype=np.int16)
    out = frames_to_mono16k(_frame(mono16, 16000))
    assert len(out) == 1600
    assert frames_to_mono16k([]).size == 0


def test_stt_is_lazy_and_labeled() -> None:
    stt = FasterWhisperSTT(model_size="tiny")
    assert stt._model is None  # no weights at construct time
    assert stt.model == "faster-whisper-tiny"
    assert stt.provider == "faster-whisper-local"
    assert stt.capabilities.streaming is False


@pytest.mark.asyncio
async def test_stt_silence_transcribes_empty() -> None:
    stt = FasterWhisperSTT(model_size="tiny")  # cached in HF hub
    silence = _frame(np.zeros(16000, dtype=np.int16), 16000)
    event = await stt.recognize([silence])
    assert event.type.name == "FINAL_TRANSCRIPT"
    assert len(event.alternatives) == 1
    assert event.alternatives[0].text == ""


@pytest.mark.asyncio
async def test_stt_tone_returns_wellformed_event() -> None:
    """Positive plumbing control: real audio in, valid event out."""
    stt = FasterWhisperSTT(model_size="tiny")
    t = np.arange(16000) / 16000.0
    tone = (np.sin(2 * np.pi * 440 * t) * 10000).astype(np.int16)
    event = await stt.recognize([_frame(tone, 16000)])
    assert event.type.name == "FINAL_TRANSCRIPT"
    assert len(event.alternatives) == 1
    assert isinstance(event.alternatives[0].text, str)


def test_pipeline_name_accepts_direct(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_PIPELINE", "direct")
    assert _pipeline_name() == "direct"


def test_direct_sub_agents_get_gemini_direct_brains(monkeypatch) -> None:
    """Regression: sub-agents must not inherit Cloud Inference in direct."""
    monkeypatch.setenv("JARVIS_PIPELINE", "direct")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(agent_mod, "_shared_local_llm", None)
    llm = agent_mod._default_agent_llm()
    assert type(llm).__module__.startswith("livekit.plugins.google")
    assert getattr(llm, "model", "") == "gemini-2.5-flash"


async def test_direct_pipeline_builds_offline_session(monkeypatch) -> None:
    """The direct branch constructs without touching the network.

    Silero VAD loads its ~2MB ONNX once (cached after); whisper stays
    lazy; Gemini LLM inits key-only. No session.start, no inference.
    """
    from livekit.agents import TurnHandlingOptions

    from agent import IDLE_HANGUP_SECONDS, _session_for_pipeline

    monkeypatch.setenv("JARVIS_PIPELINE", "direct")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(agent_mod, "_shared_local_llm", None)
    session = _session_for_pipeline(TurnHandlingOptions())
    assert session._opts.user_away_timeout == IDLE_HANGUP_SECONDS
    assert session._stt is not None and session._stt.provider == "faster-whisper-local"
