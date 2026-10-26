from __future__ import annotations

import numpy as np
import pytest

import voice_fx as fx

SR = 16000


def _sine(freq: float, dur: float = 1.0, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(SR * dur)) / SR
    return (np.sin(2 * np.pi * freq * t) * amp * 32767).astype(np.int16)


def _peak_hz(pcm: np.ndarray) -> float:
    x = pcm.astype(np.float64) * np.hanning(len(pcm))
    spec = np.abs(np.fft.rfft(x, n=len(x) * 4))
    return float(np.argmax(spec) * SR / (len(x) * 4))


@pytest.mark.parametrize("semi", [-5, -2, 3, 7])
def test_pitch_shift_ratio_and_duration(semi: int) -> None:
    src = _sine(220)
    out = fx.pitch_shift(src, SR, semi)
    assert out.dtype == np.int16
    assert abs(len(out) - len(src)) <= 0.02 * len(src)
    ratio = _peak_hz(out) / 220.0
    assert ratio == pytest.approx(2 ** (semi / 12), rel=0.04)


def test_effects_duration_and_no_clipping() -> None:
    loud = _sine(300, 0.5, amp=0.99)
    out = fx.apply_effects(loud, SR, ["ringmod_light", "reverb_short", "warm_eq"])
    assert len(out) == len(loud)
    assert out.dtype == np.int16
    assert np.max(np.abs(out.astype(np.int32))) <= 32767


def test_unknown_effect_raises() -> None:
    with pytest.raises(ValueError):
        fx.apply_effects(_sine(200, 0.1), SR, ["nope"])


@pytest.mark.parametrize("kind", ["holo", "glitch", "shimmer"])
def test_whoosh_edges_and_peak(kind: str) -> None:
    w = fx.synth_whoosh(kind, SR, 800)
    assert w.dtype == np.int16
    assert len(w) == int(SR * 0.8)
    assert np.max(np.abs(w.astype(np.int32))) <= 0.5 * 32768 + 1
    assert abs(int(w[0])) < 200 and abs(int(w[-1])) < 200


def test_glitch_burst_rate() -> None:
    dur = 2.0
    w = fx.synth_whoosh("glitch", SR, dur * 1000).astype(np.int32)
    active = np.abs(w) > 0
    bursts = int(np.sum(active[1:] & ~active[:-1])) + int(active[0])
    # zero-crossing of random noise splits runs; count via gaps > 10 ms
    idx = np.flatnonzero(active)
    groups = 1 + int(np.sum(np.diff(idx) > int(SR * 0.01))) if len(idx) else 0
    assert bursts >= 1
    assert groups / dur <= 3.0


def test_determinism_with_seed() -> None:
    a = fx.synth_whoosh("shimmer", SR, 300, seed=5)
    b = fx.synth_whoosh("shimmer", SR, 300, seed=5)
    c = fx.synth_whoosh("shimmer", SR, 300, seed=6)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)


def test_crossfade_length_and_equal_power() -> None:
    a = np.full(SR, 10000, dtype=np.int16)
    b = np.full(SR, 10000, dtype=np.int16)
    out = fx.crossfade(a, b, SR, 100)
    n = int(SR * 0.1)
    assert len(out) == 2 * SR - n
    mid = out[SR - n // 2]
    assert mid > 10000  # equal-power on correlated signals bumps up


def test_mix_under_gain_and_limit() -> None:
    voice = np.zeros(1000, dtype=np.int16)
    sfx = np.full(1000, 10000, dtype=np.int16)
    out = fx.mix_under(voice, sfx, -6.0)
    assert out[500] == pytest.approx(10000 * 10 ** (-6 / 20), rel=0.02)
    hot = fx.mix_under(np.full(100, 32767, np.int16), np.full(100, 32767, np.int16), 0)
    assert hot.max() <= 32767 and hot.min() >= -32768


def test_fades() -> None:
    x = np.full(SR, 20000, dtype=np.int16)
    assert fx.fade_in(x, SR, 10)[0] == 0
    assert fx.fade_out(x, SR, 10)[-1] == 0
    assert fx.fade_in(x, SR, 10)[-1] == 20000
