"""Small PCM helpers: software AGC and RMS."""

from __future__ import annotations

import numpy as np


def rms(pcm: bytes) -> float:
    a = np.frombuffer(pcm, dtype=np.int16)
    return float(np.sqrt(np.mean(a.astype(np.float32) ** 2))) if a.size else 0.0


class AutoGain:
    """Slow adaptive digital gain toward a target RMS (replaces WebRTC AGC).

    Gain rises slowly in quiet speech, falls fast on loud input, is clamped,
    and never amplifies near-silence so room noise is not pumped up.
    """

    def __init__(
        self,
        target_rms: float = 3000.0,
        max_gain: float = 6.0,
        floor_rms: float = 150.0,
        attack: float = 0.5,
        release: float = 0.02,
    ) -> None:
        self.target, self.max_gain, self.floor = target_rms, max_gain, floor_rms
        self.attack, self.release = attack, release
        self.gain = 1.0

    def process(self, pcm: bytes) -> bytes:
        a = np.frombuffer(pcm, dtype=np.int16)
        if a.size == 0:
            return pcm
        level = float(np.sqrt(np.mean(a.astype(np.float32) ** 2)))
        if level >= self.floor:
            want = min(self.max_gain, max(0.25, self.target / level))
            rate = self.attack if want < self.gain else self.release
            self.gain += (want - self.gain) * rate
        if abs(self.gain - 1.0) < 0.01:
            return pcm
        out = np.clip(a.astype(np.float32) * self.gain, -32768, 32767).astype(np.int16)
        return out.tobytes()
