import numpy as np

from audio_dsp import Downsampler48to16, downsample_48k_to_16k


def _tone(hz: float, n: int, amp: float = 10000.0) -> np.ndarray:
    t = np.arange(n) / 48000
    return (amp * np.sin(2 * np.pi * hz * t)).astype(np.int16)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))


def test_block_sizes_map_exactly() -> None:
    down = Downsampler48to16()
    assert len(down.process(np.zeros(1536, dtype=np.int16))) == 512
    assert len(down.process(np.zeros(960, dtype=np.int16))) == 320
    assert len(down.process(np.zeros(0, dtype=np.int16))) == 0


def test_speech_band_passes() -> None:
    out = downsample_48k_to_16k(_tone(1000, 48000))[200:]
    assert 0.95 < _rms(out) / (10000 / np.sqrt(2)) < 1.05


def test_high_frequency_noise_does_not_alias() -> None:
    # 13 kHz folds onto 3 kHz under plain [::3] decimation.
    tone = _tone(13000, 48000)
    naive = tone[::3]
    filtered = downsample_48k_to_16k(tone)[200:]
    assert _rms(naive) > 6000
    assert _rms(filtered) < 30  # > 50 dB down


def test_streaming_matches_one_shot_for_any_block_sizes() -> None:
    rng = np.random.default_rng(7)
    sig = rng.integers(-8000, 8000, 48000 * 2 + 7).astype(np.int16)
    whole = downsample_48k_to_16k(sig)
    down = Downsampler48to16()
    parts, i = [], 0
    for size in [1536, 960, 1000, 7, 2048] * 40:
        if i >= len(sig):
            break
        parts.append(down.process(sig[i : i + size]))
        i += size
    assert np.array_equal(np.concatenate(parts), whole)
