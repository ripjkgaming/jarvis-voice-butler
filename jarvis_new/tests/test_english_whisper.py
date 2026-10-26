"""English-only weights and decoding across local voice entry points."""

import io
import struct
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from local_stt import whisper_model_name


@pytest.mark.parametrize(
    "value,expected",
    [
        ("", "base.en"),
        ("tiny", "tiny.en"),
        ("base", "base.en"),
        ("small", "small.en"),
        ("medium", "medium.en"),
        ("small.en", "small.en"),
        ("large-v3-turbo", "base.en"),
        ("auto", "base.en"),
    ],
)
def test_models_are_english_only(monkeypatch, value, expected):
    monkeypatch.setenv("JARVIS_WHISPER_MODEL", value)
    assert whisper_model_name() == expected


def test_wake_worker_explicit_english_transcription(monkeypatch):
    import stt_worker

    calls = []

    class Model:
        def __init__(self, name, **kwargs):
            calls.append((name, kwargs))

        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            return iter([SimpleNamespace(text="Jarvis")]), None

    monkeypatch.setitem(
        sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model)
    )
    monkeypatch.setenv("JARVIS_SPOT_MODEL", "base")
    monkeypatch.setattr(stt_worker.os, "nice", lambda value: None)
    pcm = np.zeros(1600, np.int16).tobytes()
    output = io.BytesIO()
    monkeypatch.setattr(
        sys,
        "stdin",
        SimpleNamespace(buffer=io.BytesIO(struct.pack("<I", len(pcm)) + pcm)),
    )
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=output))
    assert stt_worker.main() == 0
    assert calls[0][0] == "base.en"
    assert calls[1]["language"] == "en"
    assert calls[1]["task"] == "transcribe"
    assert output.getvalue() == b'{"text": "Jarvis"}\n'


def test_phone_whisper_uses_english_weights_and_task(monkeypatch):
    import base64

    import bridge

    calls = []

    class Model:
        def __init__(self, name, **kwargs):
            calls.append(name)

        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            return iter([]), None

    monkeypatch.setitem(
        sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model)
    )
    monkeypatch.setattr(bridge, "_PHONE_WHISPER", None)
    monkeypatch.setenv("JARVIS_PHONE_WHISPER", "tiny")
    code, result = bridge.handle_talk(
        {"audio_b64": base64.b64encode(np.zeros(1600, np.int16).tobytes()).decode()}
    )
    assert code == 200 and result["transcript"] == ""
    assert calls == [
        "tiny.en",
        {"language": "en", "task": "transcribe", "beam_size": 1},
    ]


def test_telegram_whisper_forces_transcribe_not_translate(monkeypatch):
    import telegram_bot

    calls = []

    class Model:
        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            return iter([]), None

    monkeypatch.setattr(
        telegram_bot,
        "decode_16k_mono",
        lambda *args: np.zeros(1600, np.int16).tobytes(),
    )
    assert telegram_bot.transcribe_ogg(b"synthetic", model=Model()) == ""
    assert calls == [{"language": "en", "task": "transcribe", "beam_size": 1}]
