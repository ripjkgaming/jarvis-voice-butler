import os
import wave
from pathlib import Path

import numpy as np
import pytest

from clap_detect import (
    MARGIN_CAP_DB,
    PEAK_DB,
    CalibrationWatcher,
    ClapDetector,
    SleepStateReader,
    analyze_recording,
    load_calibration,
    should_wake,
    write_calibration,
    write_wav,
)

SR = 16000
DUR = 3.0  # seconds of audio per synthetic clip
NOISE_AMP = 0.003  # room noise floor


def _background(seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(DUR * SR)) * NOISE_AMP).astype(np.float32)


def _add(sig: np.ndarray, t0: float, burst: np.ndarray) -> None:
    i = round(t0 * SR)
    end = min(sig.size, i + burst.size)
    sig[i:end] += burst[: end - i]


def _clap(amp: float = 0.6, seed: int = 7) -> np.ndarray:
    """Broadband impulse: 4 ms decaying white noise, ~15 ms long."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(0.03 * SR)) / SR
    burst = rng.standard_normal(t.size) * np.exp(-t / 0.004)
    return (amp * burst / np.max(np.abs(burst))).astype(np.float32)


def _clicks(times: list[float], amp: float = 0.6, seed: int = 7) -> np.ndarray:
    sig = _background()
    for k, t0 in enumerate(times):
        amp_k = amp[k] if isinstance(amp, list) else amp
        _add(sig, t0, _clap(amp_k, seed + k))
    return sig


def _run(sig: np.ndarray, chunk: int = 512, **kw) -> list[int]:
    """Feed in blocks; return the sample index of each block that fired."""
    det = ClapDetector(SR, **kw)
    fired = []
    for start in range(0, sig.size, chunk):
        if det.feed(sig[start : start + chunk]):
            fired.append(start)
    return fired


def test_two_claps_half_second_apart_fire_once() -> None:
    fired = _run(_clicks([1.5, 2.0]))
    assert len(fired) == 1
    assert fired[0] + 512 >= 2.0 * SR and fired[0] <= 2.0 * SR + 1024


def test_double_clap_fires_for_any_block_size() -> None:
    sig = _clicks([1.5, 2.0])
    for chunk in (160, 333, 1536):
        assert len(_run(sig, chunk)) == 1, chunk


def test_single_clap_does_not_fire() -> None:
    assert _run(_clicks([1.5])) == []


def test_claps_too_far_apart_do_not_fire() -> None:
    assert _run(_clicks([1.0, 2.5])) == []


def test_claps_too_close_together_do_not_fire() -> None:
    assert _run(_clicks([1.5, 1.55])) == []


def test_mismatched_loudness_does_not_fire() -> None:
    # Second clap ~9.5 dB quieter: still a clap on its own, but not the pair.
    fired_on_second = _run(_clicks([1.5, 2.0], amp=[0.6, 0.2]))
    assert fired_on_second == []
    assert len(_run(_clicks([1.5, 2.0], amp=[0.6, 0.45]))) == 1


def test_steady_tone_does_not_fire() -> None:
    t = np.arange(int(DUR * SR)) / SR
    sig = _background() + (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    assert _run(sig) == []


def test_long_noise_bursts_rejected_by_duration() -> None:
    rng = np.random.default_rng(3)
    burst = (rng.standard_normal(int(0.6 * SR)) * 0.2).astype(np.float32)
    sig = _background()
    _add(sig, 1.5, burst)
    _add(sig, 2.2, burst)
    assert _run(sig) == []


def test_speech_like_syllables_rejected() -> None:
    # 140 Hz harmonic buzz with a 180 ms syllable envelope, twice.
    t = np.arange(int(0.18 * SR)) / SR
    env = np.sin(np.pi * t / 0.18) ** 2
    buzz = sum(np.sin(2 * np.pi * 140 * k * t) / k for k in range(1, 12))
    syllable = (0.3 * env * buzz).astype(np.float32)
    sig = _background()
    _add(sig, 1.5, syllable)
    _add(sig, 2.0, syllable)
    assert _run(sig) == []


def test_silence_does_not_fire() -> None:
    assert _run(np.zeros(int(DUR * SR), dtype=np.float32)) == []


def test_sleep_state_missing_file_is_awake(tmp_path) -> None:
    reader = SleepStateReader(tmp_path / "sleep_state.json", clock=lambda: 0.0)
    assert reader.is_asleep() is False


def test_sleep_state_reads_asleep_and_caches(tmp_path) -> None:
    path = tmp_path / "sleep_state.json"
    path.write_text('{"state": "asleep"}')
    (tmp_path / "sleep_screensaver.pid").write_text(str(os.getpid()))
    now = [0.0]
    reader = SleepStateReader(path, ttl_s=1.0, clock=lambda: now[0])
    assert reader.is_asleep() is True
    path.write_text('{"state": "awake"}')
    now[0] = 0.5
    assert reader.is_asleep() is True  # cached inside the 1 s window
    now[0] = 1.2
    assert reader.is_asleep() is False


def test_sleep_state_bad_json_is_awake(tmp_path) -> None:
    path = tmp_path / "sleep_state.json"
    path.write_text("{not json")
    reader = SleepStateReader(path, clock=lambda: 0.0)
    assert reader.is_asleep() is False


# -- weak, clipped, narrow-band mic; calibration; wake gating ----------------


def _narrow_clap(lp_hz: float, amp: float = 0.6, seed: int = 11) -> np.ndarray:
    """Broadband clap band-limited to lp_hz, as a narrow-band mic would hear it."""
    clap = _clap(amp, seed).astype(np.float64)
    spec = np.fft.rfft(clap, 4 * clap.size)
    freqs = np.fft.rfftfreq(4 * clap.size, 1.0 / SR)
    spec[freqs > lp_hz] = 0
    out = np.fft.irfft(spec, 4 * clap.size)[: clap.size]
    return (amp * out / np.max(np.abs(out))).astype(np.float32)


def test_int16_input_fires_like_float() -> None:
    sig = _clicks([1.5, 2.0])
    pcm = np.clip(np.rint(sig * 32768.0), -32768, 32767).astype(np.int16)
    assert len(_run(pcm)) == 1


def test_clipped_claps_fire() -> None:
    sig = np.clip(_clicks([1.5, 2.0], amp=3.0), -1.0, 1.0).astype(np.float32)
    assert len(_run(sig)) == 1


def test_narrow_band_claps_fire() -> None:
    # A mic with almost nothing above 1.5 kHz: no high-frequency gate applies.
    sig = _background()
    _add(sig, 1.5, _narrow_clap(1500.0))
    _add(sig, 2.0, _narrow_clap(1500.0, seed=12))
    assert len(_run(sig)) == 1


def test_weak_claps_fire_with_calibrated_margin() -> None:
    # Louder room (noise ~ -34 dBFS rms), quiet claps: a 0.14 pair stands
    # ~9 dB above the floor. The calibrated margin (5 dB here) catches it,
    # while a 0.12 pair is below that margin and does not.
    rng = np.random.default_rng(2)
    bg = (rng.standard_normal(int(DUR * SR)) * 0.02).astype(np.float32)

    def pair(amp: float) -> np.ndarray:
        sig = bg.copy()
        _add(sig, 1.5, _clap(amp, 7))
        _add(sig, 2.0, _clap(amp, 8))
        return sig

    assert _run(pair(0.12)) == []
    assert len(_run(pair(0.14), peak_db=5.0)) == 1


def test_dc_offset_and_hum_are_tolerated() -> None:
    t = np.arange(int(DUR * SR)) / SR
    bias = 0.2 + 0.05 * np.sin(2 * np.pi * 60 * t)
    sig = _clicks([1.5, 2.0], amp=0.9) + bias.astype(np.float32)
    assert len(_run(sig)) == 1


def test_on_candidate_reports_reasons() -> None:
    seen: list[dict] = []
    det = ClapDetector(SR, on_candidate=seen.append)
    sig = _clicks([1.5, 2.0])
    for start in range(0, sig.size, 512):
        det.feed(sig[start : start + 512])
    reasons = [d["reason"] for d in seen]
    assert "clap" in reasons and "double" in reasons
    assert all("margin_db" in d and "floor_db" in d and "peak_db" in d for d in seen)


def test_should_wake_only_while_asleep() -> None:
    assert should_wake(False, True) is False  # awake: a clap does nothing
    assert should_wake(True, False) is False
    assert should_wake(True, True) is True


def test_analyze_recording_derives_margin_from_two_claps() -> None:
    sig = _clicks([1.5, 2.0], amp=[0.3, 0.3])
    cal = analyze_recording(sig, SR)
    assert cal["gap_ok"] is True
    assert 4.0 <= cal["peak_margin_db"] <= 20.0
    assert cal["peak1_dbfs"] > cal["floor_dbfs"] + 6.0


def test_analyze_recording_needs_two_claps() -> None:
    import pytest

    with pytest.raises(ValueError):
        analyze_recording(_clicks([1.5]), SR)


def test_calibration_round_trip_and_bad_files(tmp_path) -> None:
    path = tmp_path / "clap_threshold.json"
    assert load_calibration(path) is None  # missing
    path.write_text("{not json")
    assert load_calibration(path) is None
    write_calibration(path, {"peak_margin_db": 99.0, "floor_dbfs": -30.0})
    cal = load_calibration(path)
    assert cal is not None and cal["peak_margin_db"] == 20.0  # clamped


def test_calibration_watcher_applies_and_reverts(tmp_path) -> None:
    path = tmp_path / "clap_threshold.json"
    now = [0.0]
    watch = CalibrationWatcher(path, ttl_s=2.0, clock=lambda: now[0])
    det = ClapDetector(SR)
    assert watch.poll(det) is None
    assert det.peak_db == MARGIN_CAP_DB
    write_calibration(path, {"peak_margin_db": 5.5})
    now[0] = 2.5
    assert watch.poll(det) is not None
    assert det.peak_db == 5.5
    path.unlink()
    now[0] = 5.0
    watch.poll(det)
    assert det.peak_db == MARGIN_CAP_DB


def test_calibration_watcher_reports_each_change(tmp_path) -> None:
    path = tmp_path / "clap_threshold.json"
    now = [0.0]
    seen: list[tuple] = []
    watch = CalibrationWatcher(
        path,
        ttl_s=2.0,
        clock=lambda: now[0],
        on_change=lambda cal, det: seen.append(
            (cal and cal["peak_margin_db"], det.peak_db)
        ),
    )
    det = ClapDetector(SR)
    watch.poll(det)
    assert seen == []  # no file, no change
    write_calibration(path, {"peak_margin_db": 16.17})
    now[0] = 2.5
    watch.poll(det)
    assert seen == [
        (16.17, MARGIN_CAP_DB)
    ]  # calibrated 16.17 dB, effective margin capped
    path.unlink()
    now[0] = 5.0
    watch.poll(det)
    assert seen[-1] == (None, PEAK_DB)


def test_write_wav_round_trip(tmp_path) -> None:
    pcm = np.arange(-500, 500, dtype=np.int16)
    out = write_wav(tmp_path / "clap_last.wav", pcm, 48000)
    with wave.open(str(out), "rb") as wf:
        assert wf.getframerate() == 48000
        assert wf.getnframes() == pcm.size
        assert np.array_equal(np.frombuffer(wf.readframes(pcm.size), np.int16), pcm)


# -- observed mic behaviour: periodic room noise, gain, cap ------------------


def test_periodic_singles_three_seconds_apart_do_not_fire() -> None:
    # Field log: clap-like onsets (margin 8-21 dB) about every 3.05 s, with
    # no partner 0.2-0.8 s away. Singles never make a pair.
    times = [1.0 + 3.05 * k for k in range(6)]
    amps = [0.03, 0.05, 0.1, 0.06, 0.04, 0.08]
    sig = _background()
    for t0, amp in zip(times, amps):
        _add(sig, t0, _clap(amp, 20 + round(t0 * 10)))
    assert _run(sig, peak_db=10.0) == []


def test_pair_0p45s_apart_at_10db_margin_fires() -> None:
    # A real pair, half a second apart, at a 10 dB margin (about 11.6 dB
    # measured) must fire. Covers the observed gap and level range.
    sig = _background()
    _add(sig, 1.5, _clap(0.03, 7))
    _add(sig, 1.95, _clap(0.03, 8))
    assert len(_run(sig, peak_db=10.0)) == 1


def test_pair_fires_at_any_mic_gain() -> None:
    # Gain changes scale noise and claps alike: margins, not dBFS, decide.
    for gain in (0.1, 1.0, 3.0):
        sig = _clicks([1.5, 2.0], amp=0.6 * gain)
        sig = (sig * gain).astype(np.float32) if gain != 1.0 else sig
        assert len(_run(sig)) == 1, gain


def test_calibrated_margin_is_capped_at_observed_noise_level() -> None:
    from clap_detect import effective_margin

    assert effective_margin(23.0) == MARGIN_CAP_DB == 14.0
    assert effective_margin(16.17) == MARGIN_CAP_DB
    assert effective_margin(6.0) == 6.0
    assert ClapDetector(SR, peak_db=23.0).peak_db == MARGIN_CAP_DB


def test_quiet_bump_below_margin_does_not_block_pair() -> None:
    # A 9 dB bump before the first clap sits below the 12 dB margin, so it
    # is not a clap-level onset and does not make the pattern busy.
    sig = _background()
    _add(sig, 1.1, _clap(0.012, 5))
    _add(sig, 1.5, _clap(0.06, 7))
    _add(sig, 2.0, _clap(0.06, 8))
    assert len(_run(sig)) == 1


def test_sub_margin_bump_between_claps_does_not_block_pair() -> None:
    # The same spot as the busy-pattern test, but a 9 dB bump: below the
    # margin, so it is not clap-level and the pair still fires.
    sig = _background()
    _add(sig, 1.5, _clap(0.06, 7))
    _add(sig, 1.95, _clap(0.012, 5))
    _add(sig, 2.0, _clap(0.06, 8))
    assert len(_run(sig)) == 1


# -- real recordings (raw 48 kHz mic, as wake_client feeds the detector) -----

DATA = Path(__file__).resolve().parent / "data"


def _mic_16k(name: str) -> np.ndarray:
    """Load a 48 kHz mono int16 WAV and decimate it like the wake loop does."""
    from audio_dsp import downsample_48k_to_16k

    with wave.open(str(DATA / name), "rb") as wf:
        assert wf.getframerate() == 48000 and wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    return downsample_48k_to_16k(pcm)


def _fire_times(x16: np.ndarray, chunk: int = 512) -> list[float]:
    det = ClapDetector(SR)
    times = []
    for start in range(0, x16.size, chunk):
        if det.feed(x16[start : start + chunk]):
            times.append(start / SR)
    return times


def _boost_claps(
    x: np.ndarray, src: np.ndarray, pairs: list[tuple[float, float]], gain_db: float
) -> np.ndarray:
    """Add each src clap (peak sample found near t_src) at t_dst, boosted.

    Only the clap itself is boosted (peak -5 ms .. +40 ms, with a short
    taper), so the room background is untouched.
    """
    x = x.astype(np.float64) / 32768.0  # int16 PCM in, float out
    src = src.astype(np.float64) / 32768.0
    out = x.copy()
    g = 10 ** (gain_db / 20.0)
    pre, post, tap = int(0.005 * SR), int(0.04 * SR), int(0.002 * SR)
    for t_dst, t_src in pairs:
        a0, a1 = int((t_src - 0.04) * SR), int((t_src + 0.08) * SR)
        pk = a0 + int(np.argmax(np.abs(src[a0:a1])))
        seg = src[pk - pre : pk + post]
        win = np.ones(seg.size)
        win[:tap] = np.linspace(0, 1, tap)
        win[-tap:] = np.linspace(1, 0, tap)
        d = int(t_dst * SR) - pre
        out[d : d + seg.size] += (
            (g - 1.0) * seg * win if t_dst == t_src else g * seg * win
        )
    return np.clip(out, -1.0, 1.0).astype(np.float32)


def _loud_pair(gain_db: float) -> np.ndarray:
    """The user's real clap pair, each clap boosted by gain_db over the room."""
    x = _mic_16k("clap_real_20s_48k.wav")
    return _boost_claps(x, x, [(5.5, 5.5), (6.07, 6.07)], gain_db)


def test_real_soft_pair_is_not_accepted() -> None:
    # The user's real pair stands only 8.5 dB over the 2 s background: the
    # same level as room noise (see false_wake_ring.wav), so it is refused.
    assert _fire_times(_mic_16k("clap_real_20s_48k.wav")) == []


def test_real_pair_boosted_12_and_15db_fires_once() -> None:
    for gain in (12.0, 15.0):
        fired = _fire_times(_loud_pair(gain))
        assert len(fired) == 1, (gain, fired)
        assert 6.0 <= fired[0] <= 6.2, (gain, fired)


def test_false_wake_ring_never_fires() -> None:
    # 20 s raw mic of the room right after `sleep` (screensaver, window
    # effects, fans), no user clap: it once woke the machine at 8-9 dB. It
    # holds thumps up to 14 dB (and a 0 dBFS bump) but no pair.
    assert _fire_times(_mic_16k("false_wake_ring.wav")) == []


def test_false_wake_ring_boosted_pair_only_fires_for_the_user() -> None:
    x = _mic_16k("false_wake_ring.wav")
    ref = _mic_16k("clap_real_20s_48k.wav")
    out = _boost_claps(x, ref, [(2.0, 5.5), (2.57, 6.07)], 12.0)
    times = _fire_times(out)
    assert len(times) == 1 and 2.5 <= times[0] <= 2.8, times


def test_clap_after_noise_burst_is_not_isolated() -> None:
    # Loud pair, but an 11-13 dB bump 0.15 s before the first clap: the first
    # clap did not come out of quiet.
    sig = _background()
    _add(sig, 1.35, _clap(0.045, 3))
    _add(sig, 1.5, _clap(0.5, 7))
    _add(sig, 2.0, _clap(0.5, 8))
    assert _run(sig) == []
    clean = _background()
    _add(clean, 1.5, _clap(0.5, 7))
    _add(clean, 2.0, _clap(0.5, 8))
    assert len(_run(clean)) == 1


def test_rate_guard_raises_margin_after_three_pairs_in_30s() -> None:
    now = [0.0]
    det = ClapDetector(SR, clock=lambda: now[0])
    assert det.margin() == 14.0
    for _ in range(3):
        assert det._rate_guard()
        now[0] += 5.0
    assert det.margin() == 17.0
    now[0] += 121.0
    assert det.margin() == 14.0


def test_grace_ignores_claps_until_grace_over(caplog) -> None:
    from clap_detect import ClapGrace

    g = ClapGrace(8.0)
    assert not g.armed(False, 100.0, 101.0)
    assert not g.armed(True, 100.0, 101.0)
    assert not g.armed(True, 100.0, 107.9)
    with caplog.at_level("WARNING", logger="jarvis-wake"):
        assert g.armed(True, 100.0, 108.0)
        assert g.armed(True, 100.0, 120.0)
    assert caplog.text.count("clap grace over, armed") == 1
    # a new sleep restarts it; a missing/future `since` starts at first sight
    assert not g.armed(False, None, 130.0)
    assert not g.armed(True, None, 131.0)
    assert g.armed(True, None, 139.0)


def test_sleep_state_reader_exposes_since(tmp_path) -> None:
    f = tmp_path / "sleep_state.json"
    f.write_text('{"state": "asleep", "since": 1234.5}', encoding="utf-8")
    (tmp_path / "sleep_screensaver.pid").write_text(str(os.getpid()))
    r = SleepStateReader(f)
    assert r.is_asleep() and r.since() == 1234.5
    assert SleepStateReader(tmp_path / "none.json").since() is None


def test_quiet_room_recording_never_fires() -> None:
    assert _fire_times(_mic_16k("room_quiet_8s_48k.wav")) == []


def _ringing_clap(amp: float, seed: int, tau: float = 0.03) -> np.ndarray:
    """Loud clap with a room-ring tail: stays above the noise for ~150 ms."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(0.3 * SR)) / SR
    burst = rng.standard_normal(t.size) * np.exp(-t / tau)
    return (amp * burst / np.max(np.abs(burst))).astype(np.float32)


def _pair(amp1: float, amp2: float, *, ring: bool) -> tuple[list[int], list[dict]]:
    make = _ringing_clap if ring else _clap
    sig = _background()
    _add(sig, 1.5, make(amp1, 7))
    _add(sig, 2.0, make(amp2, 8))
    seen: list[dict] = []
    det = ClapDetector(SR, on_candidate=seen.append)
    fired = [s for s in range(0, sig.size, 512) if det.feed(sig[s : s + 512])]
    return fired, seen


def test_loud_pair_20db_over_background_fires() -> None:
    # Calibration-level claps (~20 dB over the room) with a ring tail.
    fired, seen = _pair(0.5, 0.5, ring=True)
    assert max(c["margin_db"] for c in seen) >= 18.0
    assert len(fired) == 1


def test_loud_pair_with_6db_level_mismatch_fires() -> None:
    fired, _ = _pair(0.5, 0.25, ring=True)
    assert len(fired) == 1


def test_pair_at_9db_over_background_is_ignored_like_room_noise() -> None:
    # Room noise reaches 8-14 dB over the background; such pairs are refused.
    fired, seen = _pair(0.025, 0.025, ring=False)
    assert all(c["margin_db"] < 14.0 for c in seen), seen
    assert fired == []


def test_pair_at_19db_over_background_fires() -> None:
    fired, seen = _pair(0.09, 0.09, ring=False)
    assert min(c["margin_db"] for c in seen) >= 14.0, seen
    assert len(fired) == 1


# -- production floors (ratio AND absolute peak), from the user's calibration --

USER_CAL = {
    "floor_dbfs": -25.06,
    "peak1_dbfs": -1.96,
    "peak2_dbfs": -1.81,
    "peak_margin_db": 16.17,
}


def _prod_detector(seen: list | None = None) -> ClapDetector:
    from clap_detect import derive_floors

    det = ClapDetector(
        SR,
        peak_db=USER_CAL["peak_margin_db"],
        on_candidate=(seen.append if seen is not None else None),
    )
    det.set_floors(*derive_floors(USER_CAL))
    return det


def _prod_fires(x16: np.ndarray, seen: list | None = None) -> list[float]:
    det = _prod_detector(seen)
    return [s / SR for s in range(0, x16.size, 512) if det.feed(x16[s : s + 512])]


def test_derive_floors_from_calibration() -> None:
    from clap_detect import (
        DEFAULT_PEAK_FLOOR_DBFS,
        DEFAULT_RATIO_FLOOR_DB,
        derive_floors,
    )

    ratio, peak = derive_floors(USER_CAL)
    # >= 3 dB under the calibration claps, >= 3 dB over the loudest false pair (-9.3)
    assert -8.0 <= peak <= -4.9 and peak >= -9.3 + 3.0
    assert 17.0 <= ratio <= 18.0
    assert derive_floors(None) == (DEFAULT_RATIO_FLOOR_DB, DEFAULT_PEAK_FLOOR_DBFS)
    # a much quieter calibration is clamped, never below the false-pair margin
    assert derive_floors(dict(USER_CAL, peak1_dbfs=-20.0, peak2_dbfs=-20.0))[1] == -8.0


def test_watcher_applies_floors(tmp_path) -> None:
    path = tmp_path / "clap_threshold.json"
    det = ClapDetector(SR)
    watch = CalibrationWatcher(path, ttl_s=0.0)
    watch.poll(det)
    assert det.peak_floor_dbfs == -6.0 and det.ratio_floor_db == 18.0
    write_calibration(path, USER_CAL)
    watch.poll(det)
    assert det.peak_floor_dbfs == pytest.approx(-5.96, abs=0.01)
    assert det.margin() == pytest.approx(0.78 * 23.1, abs=0.1)


@pytest.mark.parametrize(
    "name",
    [
        "false_wake_overnight.wav",
        "false_wake_ring.wav",
        "room_quiet_8s_48k.wav",
        "clap_real_20s_48k.wav",
    ],
)
def test_production_floors_never_fire_on_recorded_room(name) -> None:
    assert _prod_fires(_mic_16k(name)) == []


def test_production_floors_fire_once_on_calibration_level_pair() -> None:
    # Claps scaled to the user's calibration level (-1.5 / -3 dBFS, 21-23 dB).
    x = _mic_16k("clap_real_20s_48k.wav")
    out = _boost_claps(x, x, [(5.5, 5.5), (6.07, 6.07)], 19.0)
    seen: list[dict] = []
    fired = _prod_fires(out, seen)
    assert len(fired) == 1 and 6.0 <= fired[0] <= 6.2, (fired, seen)


def test_production_floors_refuse_pair_just_under_the_floors() -> None:
    # The same claps at -5.5 / -6.5 dBFS, 17.5-18.7 dB: the overnight false
    # pairs' level class (-9.3..-13 dBFS, 15.7-19.7 dB) sits well below.
    x = _mic_16k("clap_real_20s_48k.wav")
    out = _boost_claps(x, x, [(5.5, 5.5), (6.07, 6.07)], 13.5)
    assert _prod_fires(out) == []


def test_absolute_peak_floor_rejects_quiet_pair_with_good_ratio() -> None:
    sig = _background()
    _add(sig, 1.5, _ringing_clap(0.5, 7))
    _add(sig, 2.0, _ringing_clap(0.5, 8))  # peaks about -15 dBFS, 35 dB over room
    seen: list[dict] = []
    det = ClapDetector(SR, on_candidate=seen.append)
    det.set_floors(None, -10.0)
    assert not any(det.feed(sig[s : s + 512]) for s in range(0, sig.size, 512))
    assert any(c["reason"] == "soft" for c in seen), seen
    det = ClapDetector(SR)
    det.set_floors(None, -20.0)
    assert sum(det.feed(sig[s : s + 512]) for s in range(0, sig.size, 512)) == 1


def test_pair_levels_must_be_within_4db_and_gap_0p25_to_0p7() -> None:
    def pair(a1: float, a2: float, gap: float) -> int:
        sig = _background()
        _add(sig, 1.5, _ringing_clap(a1, 7))
        _add(sig, 1.5 + gap, _ringing_clap(a2, 8))
        det = ClapDetector(SR)
        return sum(det.feed(sig[s : s + 512]) for s in range(0, sig.size, 512))

    assert pair(0.5, 0.5, 0.45) == 1
    assert pair(0.5, 0.4, 0.45) == 1  # about 2 dB apart
    assert pair(0.5, 0.15, 0.45) == 0  # about 8 dB apart
    assert pair(0.5, 0.5, 0.22) == 0
    assert pair(0.5, 0.5, 0.75) == 0


def test_asleep_without_screensaver_is_not_clap_armed(tmp_path) -> None:
    f = tmp_path / "sleep_state.json"
    f.write_text('{"state": "asleep"}')
    assert SleepStateReader(f).is_asleep() is False
