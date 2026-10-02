"""Neural noise suppression for the wake client's mic stream.

Loud rooms (a school canteen: many voices, cutlery, Jarvis's own reply
leaking past the echo canceller) reach Gemini raw otherwise. ai-coustics
was the old answer but needs LiveKit Cloud; this runs a small local
sherpa-onnx speech-enhancement model on the capture thread instead.

Every consumer reads the cleaned stream (wake word, the join buffer, the
live call), so cleaning happens once, where blocks enter the queue.

A little of the raw signal is mixed back (JARVIS_DENOISE_ATTEN dB below
it): full suppression leaves "underwater" artefacts that cost speech
recognition more than the residual noise does.

Best-effort throughout: a missing package or model, or any runtime error,
falls back to the raw stream and logs once. JARVIS_DENOISE=off disables.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np

from audio_dsp import FILTER_DELAY_48K, Downsampler48to16, Upsampler16to48

logger = logging.getLogger("jarvis.denoise")

MODEL_DIR = Path.home() / ".jarvis" / "models"
#: name -> (file, model sample rate, sherpa model family, model lag in
#: 48 kHz samples). Lag is the model's own look-ahead beyond its streaming
#: output count, measured by cross-correlating cleaned speech with the
#: input (DPDFNet: 4 hops = 40 ms; GTCRN: none).
MODELS = {
    "gtcrn": ("gtcrn_simple.onnx", 16000, "gtcrn", 0),
    "dpdfnet": ("dpdfnet_baseline.onnx", 16000, "dpdfnet", 1920),
    "dpdfnet4": ("dpdfnet4.onnx", 16000, "dpdfnet", 1920),
    "dpdfnet48": ("dpdfnet2_48khz_hr.onnx", 48000, "dpdfnet", 1920),
}
DEFAULT_MODEL = "dpdfnet"
DEFAULT_ATTEN_DB = 24.0
RATE = 48000


def denoise_choice() -> str | None:
    """Model name from JARVIS_DENOISE, or None when disabled. Pure."""
    raw = os.environ.get("JARVIS_DENOISE", "").strip().lower()
    if raw in ("0", "off", "false", "no", "none"):
        return None
    return raw if raw in MODELS else DEFAULT_MODEL


def dry_gain() -> float:
    """Linear gain of the raw signal mixed under the cleaned one. Pure."""
    try:
        atten = float(os.environ.get("JARVIS_DENOISE_ATTEN", "") or DEFAULT_ATTEN_DB)
    except ValueError:
        atten = DEFAULT_ATTEN_DB
    return 0.0 if atten >= 90 else 10.0 ** (-max(atten, 0.0) / 20.0)


def _build_denoiser(name: str):
    import sherpa_onnx as so

    file, _rate, family, _lag = MODELS[name]
    path = MODEL_DIR / file
    if not path.exists():
        raise FileNotFoundError(path)
    model = so.OfflineSpeechDenoiserModelConfig(num_threads=1, provider="cpu")
    if family == "gtcrn":
        model.gtcrn = so.OfflineSpeechDenoiserGtcrnModelConfig(model=str(path))
    else:
        model.dpdfnet = so.OfflineSpeechDenoiserDpdfNetModelConfig(model=str(path))
    return so.OnlineSpeechDenoiser(so.OnlineSpeechDenoiserConfig(model=model))


class MicCleaner:
    """48 kHz int16 blocks in, cleaned 48 kHz int16 blocks of `block` out.

    The model's output arrives in its own hop sizes, so blocks are
    re-chunked to the size the listeners expect (at most one block of
    added latency). The raw signal is delayed by exactly the processed
    path's lag before the dry mix, so the two never comb-filter.
    """

    def __init__(
        self,
        name: str,
        *,
        block: int,
        dry: float | None = None,
        denoiser=None,
    ) -> None:
        self.name = name
        self.block = block
        self.dry = dry_gain() if dry is None else dry
        self._model_rate = MODELS[name][1]
        self._denoiser = denoiser if denoiser is not None else _build_denoiser(name)
        resampled = self._model_rate != RATE
        self._down = Downsampler48to16() if resampled else None
        self._up = Upsampler16to48() if resampled else None
        # Raw path delay = resampler group delays + the model's look-ahead;
        # streaming lag is absorbed by aligning on output counts.
        delay = MODELS[name][3] + (2 * FILTER_DELAY_48K if resampled else 0)
        self._dry_fifo = np.zeros(delay, dtype=np.float32)
        self._out = np.zeros(0, dtype=np.float32)

    def _run_model(self, x48: np.ndarray) -> np.ndarray:
        if self._down is None:
            return np.asarray(self._denoiser(x48, RATE).samples, dtype=np.float32)
        x16 = self._down.process(x48).astype(np.float32) / 32768.0
        y16 = np.asarray(self._denoiser(x16, self._model_rate).samples, dtype=np.float32)
        return self._up.process(y16) * 32768.0

    def process(self, raw: bytes) -> list[bytes]:
        x = np.frombuffer(raw, dtype=np.int16)
        if self._down is None:
            wet = self._run_model(x.astype(np.float32) / 32768.0) * 32768.0
        else:
            wet = self._run_model(x)
        self._dry_fifo = np.concatenate([self._dry_fifo, x.astype(np.float32)])
        dry, self._dry_fifo = self._dry_fifo[: len(wet)], self._dry_fifo[len(wet) :]
        mixed = wet + self.dry * dry if self.dry else wet
        self._out = np.concatenate([self._out, mixed])
        blocks = []
        while len(self._out) >= self.block:
            chunk, self._out = self._out[: self.block], self._out[self.block :]
            blocks.append(np.clip(np.rint(chunk), -32768, 32767).astype(np.int16).tobytes())
        return blocks


def make_cleaner(block: int) -> MicCleaner | None:
    """Configured cleaner, or None (raw stream) when off or unavailable."""
    name = denoise_choice()
    if name is None:
        return None
    try:
        cleaner = MicCleaner(name, block=block)
    except Exception as exc:
        logger.warning("noise suppression unavailable (%s): %s", name, exc)
        return None
    logger.warning("noise suppression on (%s, dry %.3f)", name, cleaner.dry)
    return cleaner
