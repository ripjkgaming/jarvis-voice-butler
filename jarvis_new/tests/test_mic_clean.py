from types import SimpleNamespace

import numpy as np

import mic_clean


def _passthrough(lag16: int = 0):
    """Fake streaming model: delays its input by lag16 samples, like DPDFNet."""
    state = {"buf": np.zeros(lag16, dtype=np.float32)}

    def run(samples, rate):
        buf = np.concatenate([state["buf"], np.asarray(samples, dtype=np.float32)])
        out, state["buf"] = buf[: len(samples)], buf[len(samples) :]
        return SimpleNamespace(samples=out)

    return run


def _speechlike(n: int, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    w = rng.standard_normal(n)
    spec = np.fft.rfft(w)
    freqs = np.fft.rfftfreq(n, 1 / 48000)
    spec[(freqs < 200) | (freqs > 5000)] = 0
    x = np.fft.irfft(spec, n)
    return (x / np.std(x) * 4000).astype(np.int16)


def _run(cleaner, x: np.ndarray, step: int = 1536) -> np.ndarray:
    out = []
    for i in range(0, len(x), step):
        out += cleaner.process(x[i : i + step].tobytes())
    return np.frombuffer(b"".join(out), dtype=np.int16).astype(np.float64)


def test_choice_and_dry_gain_env(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_DENOISE", raising=False)
    assert mic_clean.denoise_choice() == mic_clean.DEFAULT_MODEL
    monkeypatch.setenv("JARVIS_DENOISE", "off")
    assert mic_clean.denoise_choice() is None
    monkeypatch.setenv("JARVIS_DENOISE", "gtcrn")
    assert mic_clean.denoise_choice() == "gtcrn"
    monkeypatch.setenv("JARVIS_DENOISE_ATTEN", "20")
    assert abs(mic_clean.dry_gain() - 0.1) < 1e-9
    monkeypatch.setenv("JARVIS_DENOISE_ATTEN", "100")
    assert mic_clean.dry_gain() == 0.0


def test_blocks_are_rechunked_to_listener_size() -> None:
    cleaner = mic_clean.MicCleaner("gtcrn", block=1536, denoiser=_passthrough())
    sizes = []
    for _ in range(20):
        sizes += [len(b) // 2 for b in cleaner.process(np.zeros(1536, np.int16).tobytes())]
    assert sizes and set(sizes) == {1536}


def test_dry_mix_is_time_aligned_with_lagging_model() -> None:
    """With a model that delays like DPDFNet, wet + dry must add coherently
    (no comb filter): equal wet and dry halves rebuild the input level."""
    x = _speechlike(48000 * 2)
    lag16 = mic_clean.MODELS["dpdfnet"][3] // 3
    cleaner = mic_clean.MicCleaner("dpdfnet", block=1536, dry=1.0, denoiser=_passthrough(lag16))
    y = _run(cleaner, x)[6000:-6000]
    # Coherent sum doubles amplitude (+6 dB); a misaligned sum would not.
    ratio = np.std(y) / np.std(x.astype(np.float64))
    assert 1.9 < ratio < 2.1


def test_make_cleaner_falls_back_to_raw_when_model_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(mic_clean, "MODEL_DIR", tmp_path)
    monkeypatch.delenv("JARVIS_DENOISE", raising=False)
    assert mic_clean.make_cleaner(1536) is None
