"""Pure-numpy voice effects on int16 mono PCM."""

from __future__ import annotations

import numpy as np

_FULL = 32768.0
_EFFECTS = ("ringmod_light", "reverb_short", "warm_eq")


def _f(pcm: np.ndarray) -> np.ndarray:
    return np.asarray(pcm, dtype=np.float64) / _FULL


def _i16(x: np.ndarray) -> np.ndarray:
    return np.clip(np.round(x * _FULL), -32768, 32767).astype(np.int16)


def _soft_limit(x: np.ndarray, knee: float = 0.85) -> np.ndarray:
    """Transparent below knee, tanh-compressed above; bounded to <1."""
    a = np.abs(x)
    over = a > knee
    out = x.copy()
    room = 1.0 - knee
    out[over] = np.sign(x[over]) * (knee + room * np.tanh((a[over] - knee) / room))
    return out


def _resample(x: np.ndarray, ratio: float) -> np.ndarray:
    """Linear-interpolation resample; output length = len(x) / ratio."""
    n_out = max(1, round(len(x) / ratio))
    pos = np.arange(n_out) * ratio
    return np.interp(pos, np.arange(len(x)), x)


def _wsola(x: np.ndarray, rate: float, sr: int) -> np.ndarray:
    """Time-stretch so output length ~= len(x) / rate, pitch preserved."""
    win = max(64, int(sr * 0.03))
    hop_out = win // 2
    hop_in = hop_out * rate
    n_out = round(len(x) / rate)
    if len(x) < win * 2 or n_out < win:
        return _resample(x, 1.0 / rate) if len(x) else x
    tol = win // 4
    window = np.hanning(win)
    pad = np.concatenate([x, np.zeros(win + tol * 2)])
    out = np.zeros(n_out + win)
    norm = np.zeros(n_out + win)
    prev_tail = pad[hop_out : hop_out + win]
    for k in range(0, (n_out - 1) // hop_out + 1):
        o = k * hop_out
        c = round(k * hop_in)
        best = c
        if k > 0:
            lo, hi = max(0, c - tol), c + tol + 1
            seg = pad[lo : hi + win]
            corr = np.correlate(seg, prev_tail, mode="valid")[: hi - lo]
            best = lo + int(np.argmax(corr))
        frame = pad[best : best + win]
        out[o : o + win] += frame * window
        norm[o : o + win] += window
        prev_tail = pad[best + hop_out : best + hop_out + win]
    out /= np.maximum(norm, 1e-3)
    return out[:n_out]


def _fit(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) >= n:
        return x[:n]
    return np.concatenate([x, np.zeros(n - len(x))])


def pitch_shift(pcm: np.ndarray, sr: int, semitones: float) -> np.ndarray:
    """Shift pitch by semitones, preserving duration (resample + WSOLA)."""
    if len(pcm) == 0 or semitones == 0:
        return np.asarray(pcm, dtype=np.int16).copy()
    factor = 2.0 ** (semitones / 12.0)
    x = _f(pcm)
    shifted = _resample(x, factor)  # length n/factor, pitch * factor
    stretched = _wsola(shifted, 1.0 / factor, sr)  # back to ~n samples
    return _i16(_fit(stretched, len(x)))


def _ringmod(x: np.ndarray, sr: int) -> np.ndarray:
    t = np.arange(len(x)) / sr
    return 0.75 * x + 0.25 * x * np.sin(2 * np.pi * 55.0 * t)


def _reverb(x: np.ndarray, sr: int) -> np.ndarray:
    wet = np.zeros_like(x)
    for ms, g in ((29.7, 0.77), (37.1, 0.73), (41.1, 0.70), (43.7, 0.68)):
        d = int(sr * ms / 1000)
        buf = x.copy()
        for i in range(d, len(buf)):
            buf[i] += g * buf[i - d]
        wet += buf / 4
    for ms, g in ((5.0, 0.7), (1.7, 0.7)):
        d = int(sr * ms / 1000)
        y = wet.copy()
        for i in range(d, len(y)):
            y[i] = -g * wet[i] + wet[i - d] + g * y[i - d]
        wet = y
    wet *= 0.25
    return 0.85 * x + 0.15 * wet


def _warm_eq(x: np.ndarray, sr: int) -> np.ndarray:
    a = np.exp(-2 * np.pi * 250.0 / sr)
    low = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = (1 - a) * v + a * acc
        low[i] = acc
    return x + 0.35 * low


def apply_effects(pcm: np.ndarray, sr: int, effects: list[str]) -> np.ndarray:
    """Apply named effects in order; unknown name raises ValueError."""
    for name in effects:
        if name not in _EFFECTS:
            raise ValueError(f"unknown effect: {name!r}")
    x = _f(pcm)
    for name in effects:
        if name == "ringmod_light":
            x = _ringmod(x, sr)
        elif name == "reverb_short":
            x = _reverb(x, sr)
        else:
            x = _warm_eq(x, sr)
    return _i16(_soft_limit(x))


def fade_in(pcm: np.ndarray, sr: int, ms: float) -> np.ndarray:
    n = min(len(pcm), int(sr * ms / 1000))
    x = _f(pcm)
    if n > 0:
        x[:n] *= np.linspace(0.0, 1.0, n)
    return _i16(x)


def fade_out(pcm: np.ndarray, sr: int, ms: float) -> np.ndarray:
    n = min(len(pcm), int(sr * ms / 1000))
    x = _f(pcm)
    if n > 0:
        x[len(x) - n :] *= np.linspace(1.0, 0.0, n)
    return _i16(x)


def _holo(n: int, sr: int, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(n) / sr
    dur = n / sr
    f = 300 + 1500 * (t / dur) ** 2
    sine = np.sin(2 * np.pi * np.cumsum(f) / sr)
    noise = rng.standard_normal(n)
    lp = np.convolve(noise, np.ones(8) / 8, mode="same")
    env = np.sin(np.pi * t / dur)
    return (0.6 * sine + 0.4 * lp * 2) * env


def _glitch(n: int, sr: int, rng: np.random.Generator) -> np.ndarray:
    out = np.zeros(n)
    max_bursts = max(1, min(3, int(3 * n / sr)))
    burst = int(sr * 0.04)
    slots = max(1, n // max(burst, 1))
    starts = rng.choice(slots, size=min(max_bursts, slots), replace=False)
    for s in starts:
        a = int(s) * burst
        seg = rng.uniform(-1, 1, min(burst, n - a))
        out[a : a + len(seg)] = np.round(seg * 4) / 4  # ~3-bit crush
    return out


def _shimmer(n: int, sr: int, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(n) / sr
    dur = n / sr
    out = np.zeros(n)
    for k in range(6):
        f0 = 2000 + 700 * k + 3000 * (t / dur)
        ph = rng.uniform(0, 2 * np.pi)
        out += np.sin(ph + 2 * np.pi * np.cumsum(f0) / sr) / 6
    trem = 0.6 + 0.4 * np.sin(2 * np.pi * 12 * t)
    return out * trem * (t / dur) ** 0.5 * np.sin(np.pi * t / dur) ** 0.3


def synth_whoosh(kind: str, sr: int, dur_ms: float, seed: int | None = 0) -> np.ndarray:
    """Synthesize a short transition sound; peak <= 0.5 FS, 10 ms fades."""
    makers = {"holo": _holo, "glitch": _glitch, "shimmer": _shimmer}
    if kind not in makers:
        raise ValueError(f"unknown whoosh kind: {kind!r}")
    n = max(1, int(sr * dur_ms / 1000))
    x = makers[kind](n, sr, np.random.default_rng(seed))
    peak = float(np.max(np.abs(x))) if n else 0.0
    if peak > 0:
        x = x / peak * 0.5
    out = _i16(x)
    return fade_out(fade_in(out, sr, 10), sr, 10)


def crossfade(a: np.ndarray, b: np.ndarray, sr: int, ms: float) -> np.ndarray:
    """Equal-power crossfade of a's tail into b's head."""
    n = min(int(sr * ms / 1000), len(a), len(b))
    if n <= 0:
        return np.concatenate([a, b]).astype(np.int16)
    th = np.linspace(0, np.pi / 2, n)
    mid = _f(a[-n:]) * np.cos(th) + _f(b[:n]) * np.sin(th)
    return np.concatenate([a[:-n], _i16(mid), b[n:]]).astype(np.int16)


def mix_under(voice: np.ndarray, sfx: np.ndarray, gain_db: float) -> np.ndarray:
    """Mix sfx (scaled by gain_db) under voice; length = voice; soft-limited."""
    v = _f(voice)
    s = _fit(_f(sfx), len(v)) * 10.0 ** (gain_db / 20.0)
    return _i16(_soft_limit(v + s))
