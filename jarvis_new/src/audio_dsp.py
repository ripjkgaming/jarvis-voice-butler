"""Small streaming DSP helpers for the wake client's mic path.

Imported lazily (like numpy elsewhere in wake_client) so the module import
stays cheap. Everything here is pure numpy and allocation-light: one mic
block per call, no model work.
"""

from __future__ import annotations

import numpy as np

#: Odd-length windowed-sinc low-pass. 95 taps at 48 kHz is ~1 ms of group
#: delay and gives > 50 dB stopband above 8 kHz.
DECIMATE_TAPS = 95
#: Passband edge: keeps speech up to ~7.2 kHz, attenuated by 8 kHz (the
#: 16 kHz Nyquist) so room noise up there can't fold into the speech band.
DECIMATE_CUTOFF_HZ = 7200.0


def lowpass_taps(
    rate: int = 48000,
    cutoff_hz: float = DECIMATE_CUTOFF_HZ,
    taps: int = DECIMATE_TAPS,
) -> np.ndarray:
    """Blackman-windowed sinc low-pass with unity DC gain. Pure."""
    fc = cutoff_hz / rate
    n = np.arange(taps) - (taps - 1) / 2
    h = 2 * fc * np.sinc(2 * fc * n) * np.blackman(taps)
    return (h / h.sum()).astype(np.float32)


_TAPS_48K = lowpass_taps()


class Downsampler48to16:
    """Stateful anti-aliased 48 kHz -> 16 kHz decimator for int16 blocks.

    Replaces ``block[::3]``, which folded everything between 8 and 24 kHz
    (cutlery, chatter sibilance, fan whine) back into the band the wake
    model and Whisper listen to. Filter history carries across blocks, so
    a continuous stream decimates exactly like one long signal, and any
    block length works (1536 -> 512, 960 -> 320).
    """

    def __init__(self) -> None:
        self._hist = np.zeros(len(_TAPS_48K) - 1, dtype=np.float32)
        self._phase = 0  # index of the next kept sample within a block

    def process(self, block: np.ndarray) -> np.ndarray:
        if not len(block):
            return np.zeros(0, dtype=np.int16)
        x = np.concatenate([self._hist, block.astype(np.float32)])
        y = np.convolve(x, _TAPS_48K, mode="valid")  # one output per input
        out = y[self._phase :: 3]
        self._phase = (self._phase - len(block)) % 3
        self._hist = x[-(len(_TAPS_48K) - 1) :]
        return np.clip(np.rint(out), -32768, 32767).astype(np.int16)


def downsample_48k_to_16k(pcm: np.ndarray) -> np.ndarray:
    """One-shot anti-aliased decimation of a whole int16 clip. Pure."""
    return Downsampler48to16().process(np.asarray(pcm, dtype=np.int16))


class Upsampler16to48:
    """Stateful 16 kHz -> 48 kHz interpolator (zero-stuff + the same low-pass).

    Pairs with :class:`Downsampler48to16` so a 16 kHz model can sit inside
    the 48 kHz capture stream. Output is exactly 3x the input length.
    """

    def __init__(self) -> None:
        self._hist = np.zeros(len(_TAPS_48K) - 1, dtype=np.float32)
        self._taps = _TAPS_48K * 3  # restore level lost to zero-stuffing

    def process(self, block: np.ndarray) -> np.ndarray:
        """float32 16 kHz samples in, float32 48 kHz samples out."""
        if not len(block):
            return np.zeros(0, dtype=np.float32)
        stuffed = np.zeros(len(block) * 3, dtype=np.float32)
        stuffed[::3] = block
        x = np.concatenate([self._hist, stuffed])
        y = np.convolve(x, self._taps, mode="valid")
        self._hist = x[-(len(_TAPS_48K) - 1) :]
        return y.astype(np.float32)


#: Group delay of one decimate or interpolate pass, in 48 kHz samples.
FILTER_DELAY_48K = (DECIMATE_TAPS - 1) // 2
