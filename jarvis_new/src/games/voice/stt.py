"""Local speech-to-text: faster-whisper base.en int8 on CPU, loaded once and warmed."""

from __future__ import annotations

import asyncio
import threading
import time

import numpy as np

import audio_shared

# A vocabulary list, not sample phrases: a prompt that spells out whole moves
# makes base.en echo them back (a "knight to f three" clip came out as 40
# repeats and 13 s of fallback re-decodes). Measured 0.5-0.8 s per move.
CHESS_PROMPT = audio_shared.VOCAB_PROMPTS["chess"]
CHESS_HOTWORDS = audio_shared.VOCAB_HOTWORDS["chess"]


class LocalSTT:
    def __init__(
        self, model: str = "base.en", device: str = "cpu", compute_type: str = "int8"
    ):
        self.model_name = model
        self.device = device
        self.compute_type = "float16" if device == "cuda" else compute_type
        self._model = None
        self._lock = threading.Lock()
        self.chess_bias = True

    def load(self) -> float:
        """Load once and warm up with a dummy clip. Returns seconds taken."""
        t0 = time.monotonic()
        with self._lock:
            if self._model is None:
                self._model = audio_shared.load_whisper_model(
                    self.model_name, self.device, self.compute_type, offline_first=True
                )
        self._decode(np.zeros(16000, dtype=np.float32) + 0.001)
        return time.monotonic() - t0

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _decode(self, audio: np.ndarray) -> str:
        with self._lock:
            if self._model is None:
                self._model = audio_shared.load_whisper_model(
                    self.model_name, self.device, self.compute_type, offline_first=True
                )
            segs = audio_shared.transcribe_audio(
                self._model,
                audio,
                beam_size=1 if self.model_name != "small.en" else 2,
                initial_prompt=CHESS_PROMPT if self.chess_bias else None,
                hotwords=CHESS_HOTWORDS if self.chess_bias else None,
                temperature=0.0,
                max_new_tokens=48,
            )
        return " ".join(s.text.strip() for s in segs).strip()

    def transcribe_sync(self, audio: np.ndarray) -> str:
        if audio.size < 16000 // 5:
            return ""
        return self._decode(audio)

    async def transcribe(self, audio: np.ndarray) -> str:
        return await asyncio.to_thread(self.transcribe_sync, audio)
