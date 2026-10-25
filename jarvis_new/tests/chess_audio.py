"""Speech fixtures for the chess voice tests: real Piper audio, cached on disk."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import numpy as np

import audio_shared

CACHE = Path.home() / ".cache" / "jarvis-regressions" / "chess"
RATE = 16000


def piper_available() -> bool:
    return audio_shared.resolve_voice_path().is_file()


@lru_cache(maxsize=1)
def _voice():
    return audio_shared.load_piper_voice(audio_shared.resolve_voice_path())


def speech(text: str, length_scale: float = 0.9) -> np.ndarray:
    """Mono float32 16 kHz speech for ``text`` (cached under ~/.cache)."""
    key = hashlib.sha1(f"{text}|{length_scale}|alan".encode()).hexdigest()[:16]
    path = CACHE / f"{key}.npy"
    if path.is_file():
        return np.load(path)
    voice = _voice()
    pcm = audio_shared.render_sentence_pcm(voice, text, length_scale=length_scale)
    rate = int(voice.config.sample_rate)
    arr = audio_shared.pcm_to_mono16k(np.frombuffer(pcm, dtype=np.int16), rate)
    CACHE.mkdir(parents=True, exist_ok=True)
    np.save(path, arr)
    return arr


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * RATE), dtype=np.float32)


def with_padding(audio: np.ndarray, lead: float = 0.4, tail: float = 0.8) -> np.ndarray:
    return np.concatenate([silence(lead), audio, silence(tail)])
