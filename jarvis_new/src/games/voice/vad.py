"""Voice activity detection and utterance endpointing (local, CPU, no LiveKit).

Silero VAD (ONNX) when the model file exists; a simple energy gate otherwise.
The endpointer turns a stream of 512-sample frames into whole utterances:
speech start at 0.5, end after ``end_silence_ms`` of quiet, max 8 s.
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np

RATE = 16000
FRAME = 512  # silero's frame at 16 kHz (32 ms)
FRAME_MS = FRAME * 1000 / RATE


def silero_model_path() -> Path | None:
    home = os.environ.get("JARVIS_HOME", "").strip()
    bases = ([Path(home)] if home else []) + [Path.home() / ".jarvis"]
    for base in bases:
        cand = base / "models" / "silero_vad.onnx"
        if cand.is_file():
            return cand
    return None


class SileroVAD:
    """Stateful per-stream speech probability. Supports v4 (h/c) and v5 (state)."""

    def __init__(self, path: Path):
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.inter_op_num_threads = 1
        so.intra_op_num_threads = 1
        self._sess = ort.InferenceSession(
            str(path), sess_options=so, providers=["CPUExecutionProvider"]
        )
        names = {i.name for i in self._sess.get_inputs()}
        self._v5 = "state" in names
        self.reset()

    def reset(self) -> None:
        if self._v5:
            self._state = np.zeros((2, 1, 128), dtype=np.float32)
        else:
            self._h = np.zeros((2, 1, 64), dtype=np.float32)
            self._c = np.zeros((2, 1, 64), dtype=np.float32)
        self._ctx = np.zeros((1, 64), dtype=np.float32)

    def prob(self, frame: np.ndarray) -> float:
        x = frame.astype(np.float32).reshape(1, -1)
        if self._v5:
            xin = np.concatenate([self._ctx, x], axis=1)
            out, self._state = self._sess.run(
                None,
                {"x": xin, "state": self._state, "sr": np.array(RATE, dtype=np.int64)},
            )
            self._ctx = x[:, -64:]
        else:
            out, self._h, self._c = self._sess.run(
                None, {"x": x, "h": self._h, "c": self._c}
            )
        return float(out.reshape(-1)[0])


class EnergyVAD:
    """Fallback gate: RMS against an adaptive noise floor."""

    def __init__(self, floor: float = 0.01):
        self.floor = floor
        self._noise = floor

    def reset(self) -> None:
        self._noise = self.floor

    def prob(self, frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(np.square(frame.astype(np.float32))) + 1e-12))
        if rms < self._noise * 1.5:
            self._noise = 0.95 * self._noise + 0.05 * max(rms, 1e-4)
        ratio = rms / max(self._noise, 1e-4)
        return float(min(1.0, max(0.0, (ratio - 1.5) / 4.0)))


def make_vad():
    p = silero_model_path()
    if p:
        try:
            return SileroVAD(p)
        except Exception:
            pass
    return EnergyVAD()


@dataclass
class Utterance:
    audio: np.ndarray  # mono float32 16 kHz
    start: float  # monotonic seconds of first speech frame
    end: float  # monotonic seconds of last speech frame
    truncated: bool = False

    @property
    def duration(self) -> float:
        return self.audio.size / RATE


class Endpointer:
    """Frame-by-frame endpointing. ``feed`` returns an Utterance when one ends."""

    def __init__(
        self,
        vad,
        *,
        start_prob: float = 0.5,
        end_silence_ms: int = 350,
        max_s: float = 8.0,
        preroll_ms: int = 200,
        min_speech_ms: int = 120,
    ):
        self.vad = vad
        self.start_prob = start_prob
        self.end_frames = max(1, round(end_silence_ms / FRAME_MS))
        self.max_frames = int(max_s * RATE / FRAME)
        self.min_frames = max(1, round(min_speech_ms / FRAME_MS))
        self._pre: deque[np.ndarray] = deque(maxlen=max(1, int(preroll_ms / FRAME_MS)))
        self.reset()

    def reset(self) -> None:
        self.vad.reset()
        self._pre.clear()
        self._buf: list[np.ndarray] = []
        self._active = False
        self._quiet = 0
        self._speech_frames = 0
        self._t0 = 0.0
        self._t_last = 0.0

    @property
    def in_speech(self) -> bool:
        return self._active

    def feed(self, frame: np.ndarray, t: float) -> Utterance | None:
        p = self.vad.prob(frame)
        if not self._active:
            self._pre.append(frame)
            if p >= self.start_prob:
                self._active = True
                self._buf = list(self._pre)
                self._pre.clear()
                self._quiet = 0
                self._speech_frames = 1
                self._t0 = t - len(self._buf) * FRAME / RATE
                self._t_last = t
            return None
        self._buf.append(frame)
        if p >= self.start_prob * 0.7:
            self._quiet = 0
            self._speech_frames += 1
            self._t_last = t
        else:
            self._quiet += 1
        if self._quiet >= self.end_frames or len(self._buf) >= self.max_frames:
            truncated = self._quiet < self.end_frames
            audio = np.concatenate(self._buf)
            enough = self._speech_frames >= self.min_frames
            t0, t1 = self._t0, self._t_last
            self.reset()
            if not enough:
                return None
            return Utterance(audio, t0, t1, truncated)
        return None

    def drop(self) -> None:
        """Throw away the utterance in progress (wake / pause)."""
        self.reset()
