import numpy as np

import audio_shared
from local_stt import FasterWhisperSTT, frames_to_mono16k  # noqa: F401


def _tone48(hz: float, n: int = 4800) -> np.ndarray:
    t = np.arange(n) / 48000
    return (np.sin(2 * np.pi * hz * t) * 12000).astype(np.int16)


def test_pcm_to_mono16k_48k_is_antialiased() -> None:
    # 10 kHz is above the 8 kHz Nyquist of the target: naive [::3] aliases it
    # to 6 kHz at full amplitude; the low-pass must kill it.
    out = audio_shared.pcm_to_mono16k(_tone48(10000), 48000)
    naive = (_tone48(10000)[::3]).astype(np.float32) / 32768
    assert len(out) == 1600
    assert np.abs(out[200:-200]).max() < 0.1 * np.abs(naive).max()


def test_pcm_to_mono16k_keeps_speech_band() -> None:
    out = audio_shared.pcm_to_mono16k(_tone48(1000), 48000)
    assert np.abs(out[200:-200]).max() > 0.3
