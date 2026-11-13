"""Double-clap detector: the wake path while the laptop is asleep.

ClapDetector.feed() takes mono audio at 16 kHz (any block length, float in
[-1, 1] or int16) and returns True on the block where a second clap lands
0.25-0.7 s after the first. Pure numpy: no device access, no I/O beyond the
optional sleep state reader and calibration file, so it is easy to test.

Feed it the RAW microphone (before noise suppression): the denoiser is a
speech model and treats clap transients as noise, so a detector behind it
hears a different signal than the one the user made.

Background: the median of the last 2 s of 5 ms frame levels. A median is
robust: claps and bursts cover well under half the frames, so they do not
lift it. (The old minimum-tracking floor sat 12+ dB under ordinary room
noise, so every noise bump looked like an onset.)

An onset starts a run when a 5 ms frame is:
  - ONSET_DB above the background median;
  - a level jump of ATTACK_DB from the previous frame;
  - LOCAL_DB above the median of the previous 100 ms.
A run ends when the level falls back under ONSET_DB. It is a clap when:
  - short: 5-100 ms long (speech, music and sustained noise run longer);
  - loud: its peak is at least `peak_db` above the background median;
  - the largest transient: its peak is within PEAK_WINDOW_DB of the loudest
    frame in the 1 s before it (continuous noise bursts are not).
Shape checks (tone, low-frequency share, crest factor) are not used. The
real mic's clap energy is almost all below 300 Hz, so those checks rejected
real claps and passed noise.

Pairing: a second clap 0.25-0.7 s after the first, with a margin within
PAIR_LEVEL_DB of it. Another clap-level onset in the 0.6 s before the first
or between the two blocks the pair ("busy" pattern: typing, speech).
After an accepted clap, a 120 ms refractory period stops its own ring from
counting twice; onsets in the 400 ms after a clap do not make a pair "busy".

The margin (peak above background) comes from a calibration file
(`python -m src.clap_detect calibrate`), capped at MARGIN_CAP_DB. A
calibration can record claps far louder than the mic hears them later, so
the cap keeps the margin at what real claps clear. The file is hot-reloaded.
DC offset is removed with a slow running mean.

Known limit: two keystrokes 0.2-0.8 s apart after a quiet spell look like a
clap pair to the timing rules. Only the level and spectral checks separate
them, so typing is a best-effort guard, not a guarantee.
"""

from __future__ import annotations

import bisect
import json
import logging
import math
import os
import sys
import time
import wave
from collections import deque
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
FRAME_S = 0.005  # envelope resolution: 5 ms frames
WARMUP_S = 1.0  # no onsets until the background median has filled
BG_S = 2.0  # background: median of this much recent frame level
LOCAL_S = 0.1  # local level just before an onset: median of this much
PEAK_WINDOW_S = 1.0  # a clap is the loudest transient in this window
ONSET_DB = 4.0  # a frame this far above the background starts or holds a run
ATTACK_DB = 6.0  # level jump from the previous frame: starts a candidate run
LOCAL_DB = 3.0  # onset frame this far above the local (previous 100 ms) level
PEAK_WINDOW_DB = 6.0  # run peak within this of the loudest frame in PEAK_WINDOW_S
PEAK_DB = 14.0  # default run peak above the background; calibration replaces it
PEAK_DB_RANGE = (4.0, 20.0)  # bounds for a calibrated peak_db
MARGIN_CAP_DB = 14.0  # effective margin never exceeds this (see module doc)
# Data (false_wake_ring.wav, the room while asleep): single non-clap thumps
# reach 14.1 dB over the 2 s median, pairs-by-timing reach 11.1 dB. The old
# 8 dB sat inside that noise. 14 dB clears the pair-level noise (11.1 dB, 2.9
# dB headroom; the isolated 14.1 dB single cannot pair); a hard, close, cupped
# clap is 20+ dB (the user's calibration: 23 dB), a soft clap (8.5 dB) is not
# heard.
# Overnight data (false_wake_overnight.wav, wake_overnight.log): the room's
# transients that paired up while asleep peaked -9.3..-13.2 dBFS (15.7-19.7 dB
# over the background); spectrum and rise time do not separate them from claps
# on this mic (both are ~70% below 150 Hz with a ~11 ms rise). Only loudness
# does: the user's calibration claps peak -1.9 dBFS (23 dB). So a clap must
# ALSO reach an absolute level (set_floors), derived from the calibration:
# its weakest clap peak minus FLOOR_HEADROOM_DB, kept in PEAK_FLOOR_RANGE_DBFS
# (3.3 dB above the loudest false pair, 4 dB below the calibration claps).
FLOOR_HEADROOM_DB = 4.0
PEAK_FLOOR_RANGE_DBFS = (-8.0, -4.0)
DEFAULT_PEAK_FLOOR_DBFS = -6.0
RATIO_FRACTION = 0.78  # ratio floor = this x the calibration's weakest clap ratio
RATIO_FLOOR_RANGE_DB = (14.0, 18.0)
DEFAULT_RATIO_FLOOR_DB = 18.0
ISOLATION_GAP_S = 0.05  # ... measured up to this long before the onset (its own attack)
ISOLATION_S = 0.3  # the first clap needs this much background-level quiet before it
ISOLATION_DB = 10.0  # "quiet" = no frame more than this over the background
RATE_WINDOW_S = 30.0  # more than RATE_MAX double-clap events in this window ...
RATE_MAX = 2
RATE_BOOST_DB = 3.0  # ... raises the margin by this much ...
RATE_BOOST_S = 120.0  # ... for this long
GRACE_ENV = "JARVIS_CLAP_GRACE_S"
GRACE_DEFAULT_S = 8.0  # claps are ignored this long after the sleep state began
MIN_PEAK = 0.002  # absolute peak (full scale 1.0): about -54 dBFS
MIN_RUN_S = 0.005
MAX_RUN_S = 0.100
REFRACTORY_S = 0.12  # after an accepted clap: its own ring cannot count again
RING_S = 0.40  # onsets this soon after a clap are its ring, not a busy pattern
PAIR_MIN_S = 0.25
PAIR_MAX_S = 0.70
DECAY_DB = 20.0  # a run ends once its level is this far under its own peak
PAIR_LEVEL_DB = 4.0  # the two claps must be of similar loudness
QUIET_BEFORE_S = 0.6
ONSET_KEEP_S = 3.0
HIST_S = 0.5  # raw audio kept for the quiet-peak check
DC_TAU_S = 1.0  # time constant of the DC offset tracker

logger = logging.getLogger("jarvis-wake")

CALIBRATION_NAME = "clap_threshold.json"
CAL_SAFETY = 0.7  # calibrated margin = 0.7 x the weakest spike
CAL_MIN_SPIKE_DB = 6.0  # a calibration spike must stand this far above the floor
CAL_MIN_SPIKES = 2
CAL_SPIKE_GAP_S = 0.20


class _Run:
    """One active stretch of frames that began with a sharp attack."""

    __slots__ = ("floor", "n", "peak_db", "peak_idx", "pre_db", "ref_db", "start")

    def __init__(
        self, start: int, floor: float, ref_db: float, pre_db: float = -math.inf
    ) -> None:
        self.pre_db = pre_db  # loudest frame in the ISOLATION_S before it
        self.start = start  # frame index of the first active frame
        self.floor = floor  # background median (dB) when the run began
        self.ref_db = ref_db  # loudest frame in the PEAK_WINDOW_S before it
        self.n = 0  # active frames so far
        self.peak_db = -math.inf
        self.peak_idx = 0  # frame offset of the loudest frame in the run

    def add(self, level: float) -> None:
        if level > self.peak_db:
            self.peak_db = level
            self.peak_idx = self.n
        self.n += 1


class _RollingMedian:
    """Exact median and max of the last `size` values (small windows only)."""

    def __init__(self, size: int) -> None:
        self._q: deque[float] = deque(maxlen=size)
        self._sorted: list[float] = []

    def __len__(self) -> int:
        return len(self._q)

    def add(self, value: float) -> None:
        if len(self._q) == self._q.maxlen:
            old = self._q[0]
            del self._sorted[bisect.bisect_left(self._sorted, old)]
        self._q.append(value)
        bisect.insort(self._sorted, value)

    def median(self) -> float:
        s = self._sorted
        m = len(s)
        if m % 2:
            return s[m // 2]
        return 0.5 * (s[m // 2 - 1] + s[m // 2])

    def max(self) -> float:
        return self._sorted[-1]


def _frame_levels(x: np.ndarray, hop: int) -> np.ndarray:
    """Per-frame RMS level in dB (full scale = 0 dB) over whole frames of x."""
    n = x.size // hop
    frames = x[: n * hop].reshape(n, hop).astype(np.float64)
    return 10.0 * np.log10(np.mean(frames * frames, axis=1) + 1e-12)


class ClapDetector:
    """Streaming double-clap detector. Call feed() with consecutive blocks."""

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        *,
        peak_db: float = PEAK_DB,
        on_candidate=None,
        clock=time.monotonic,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        self.sample_rate = int(sample_rate)
        self._hop = max(1, round(self.sample_rate * FRAME_S))
        self._hist_keep = int(HIST_S * self.sample_rate)
        self.peak_db = effective_margin(peak_db)
        self.ratio_floor_db: float | None = None  # extra ratio floor (set_floors)
        self.peak_floor_dbfs: float | None = None  # absolute run peak floor
        # Called with a dict for every finished candidate run (accepted or
        # not), for diagnostics. Not called from feed() when None.
        self.on_candidate = on_candidate
        self._clock = clock
        self._events: deque[float] = deque()  # wall times of double-clap events
        self._boost_until = 0.0
        self.reset()

    def set_peak_db(self, peak_db: float) -> None:
        self.peak_db = effective_margin(peak_db)

    def set_floors(self, ratio_db: float | None, peak_dbfs: float | None) -> None:
        """Extra acceptance floors: ratio over background and absolute dBFS."""
        self.ratio_floor_db = ratio_db
        self.peak_floor_dbfs = peak_dbfs

    def reset(self) -> None:
        """Forget all state (for example after the sleep state changes)."""
        self._pending = np.zeros(0, dtype=np.float32)  # samples short of a frame
        self._hist = np.zeros(0, dtype=np.float32)  # recent audio (DC removed)
        self._hist_end = 0  # absolute sample index just past _hist
        self._frames = 0  # frames consumed so far
        self._dc: float | None = None  # running DC offset estimate
        self._bg = _RollingMedian(round(BG_S / FRAME_S))  # background
        self._local = _RollingMedian(round(LOCAL_S / FRAME_S))
        self._recent = _RollingMedian(round(PEAK_WINDOW_S / FRAME_S))
        self._pre: deque[float] = deque(maxlen=round(ISOLATION_S / FRAME_S))
        self._gap = round(ISOLATION_GAP_S / FRAME_S)
        self._prev_level: float | None = None
        self._run: _Run | None = None
        self._refractory_until = 0.0
        self._onsets: deque[float] = deque()  # times of sharp onsets
        self._prev_clap: tuple[float, float, float] | None = None  # (t, dB, pre dB)
        self._first_info: dict = {}  # info of the latest accepted clap
        self.last_pair: tuple[dict, dict] | None = None  # clap infos of the last double

    def feed(self, block) -> bool:
        """Consume one block; True when a double clap completes inside it."""
        x = np.asarray(block)
        if x.dtype.kind in "iu":
            x = x.astype(np.float32) / 32768.0  # raw int16 PCM
        else:
            x = x.astype(np.float32, copy=False)
        x = x.reshape(-1)
        if x.size:
            # DC offset: a slow running mean of block means, removed before
            # any level is measured, so a mic bias never reads as sound. It
            # starts at the first block's mean: starting from zero would
            # show up as a level step and a burst of false onsets.
            block_mean = float(np.mean(x))
            if self._dc is None:
                self._dc = block_mean
            else:
                alpha = min(1.0, x.size / (DC_TAU_S * self.sample_rate))
                self._dc += alpha * (block_mean - self._dc)
            x = x - np.float32(self._dc)
        if self._pending.size:
            x = np.concatenate([self._pending, x])
        n = x.size // self._hop
        used = n * self._hop
        self._pending = x[used:].copy()
        if n == 0:
            return False
        frames = x[:used].reshape(n, self._hop).astype(np.float64)
        levels = 10.0 * np.log10(np.mean(frames * frames, axis=1) + 1e-12)
        self._hist = np.concatenate([self._hist, x[:used]])
        self._hist_end = (self._frames + n) * self._hop
        fired = False
        for level in levels:
            done = self._step(float(level))
            if done is None:
                continue
            clap, reason, info = self._judge(done)
            if clap is None:
                self._report(reason, info)
                continue
            paired = self._register(*clap)
            if paired:
                self.last_pair = (self._first_info, dict(info))
            self._first_info = dict(info)
            if paired:
                paired = self._rate_guard()
            # Only an accepted clap starts the refractory period, so a
            # rejected ring-down cannot hide the real second clap.
            self._refractory_until = clap[0] + REFRACTORY_S
            self._report("double" if paired else "clap", info)
            if paired:
                fired = True
        if self._hist.size > self._hist_keep:
            self._hist = self._hist[-self._hist_keep :].copy()
        return fired

    def _report(self, reason: str, info: dict) -> None:
        if self.on_candidate is not None:
            self.on_candidate(dict(info, reason=reason, peak_db_cfg=self.margin()))

    # -- envelope and onset tracking -------------------------------------

    def _step(self, level: float) -> _Run | None:
        """Advance one frame; returns the finished run when one just ended."""
        frame = self._frames
        self._frames += 1
        t = frame * self._hop / self.sample_rate
        warm = t < WARMUP_S
        # Everything below is measured from history before this frame.
        bg = self._bg.median() if len(self._bg) else level
        local = self._local.median() if len(self._local) else level
        ref = self._recent.max() if len(self._recent) else level
        excess = level - bg
        prev = self._prev_level
        self._prev_level = level
        older = list(self._pre)[: max(0, len(self._pre) - self._gap)]
        pre_db = max(older) - bg if older else -math.inf
        self._pre.append(level)
        self._bg.add(level)
        self._local.add(level)
        self._recent.add(level)

        if self._run is not None:
            if excess >= ONSET_DB and level >= self._run.peak_db - DECAY_DB:
                self._run.add(level)
                return None
            run, self._run = self._run, None
            return run

        if (
            excess >= ONSET_DB
            and prev is not None
            and level - prev >= ATTACK_DB
            and level - local >= LOCAL_DB
            and not warm
            and t >= self._refractory_until
        ):
            run = _Run(frame, bg, ref, pre_db)
            run.add(level)
            self._run = run
        return None

    def _note_onset(self, t: float) -> None:
        self._onsets.append(t)
        while self._onsets and t - self._onsets[0] > ONSET_KEEP_S:
            self._onsets.popleft()

    def _slice(self, a: int, b: int) -> np.ndarray:
        """Audio samples [a, b) in absolute indices, clipped to the history."""
        start = self._hist_end - self._hist.size
        a = max(a, start)
        b = min(b, self._hist_end)
        if b <= a:
            return np.zeros(0, dtype=np.float32)
        return self._hist[a - start : b - start]

    def _judge(self, run: _Run):
        """Classify a finished run: (clap time, peak dB) or None, reason, info."""
        sr = self.sample_rate
        dur = run.n * self._hop / sr
        margin = run.peak_db - run.floor
        info = {
            "t": round(run.start * self._hop / sr, 3),
            "dur_ms": round(dur * 1000.0, 1),
            "peak_db": round(run.peak_db, 1),
            "floor_db": round(run.floor, 1),
            "margin_db": round(margin, 1),
        }
        if dur < MIN_RUN_S:
            return None, "short", info
        if dur > MAX_RUN_S:
            return None, "long", info
        info["pre_db"] = round(run.pre_db, 1)
        if margin < self.margin():
            return None, "weak", info
        if self.peak_floor_dbfs is not None and run.peak_db < self.peak_floor_dbfs:
            return None, "soft", info  # loud over the background, quiet in absolute
        # Clap-level onset: counts toward a "busy" pattern even if the checks
        # below reject it (typing, speech, a hit next to the clap).
        self._note_onset(run.start * self._hop / sr)
        info["ref_db"] = round(run.ref_db, 1)
        if run.peak_db < run.ref_db - PEAK_WINDOW_DB:
            return None, "not_max", info  # a louder transient just before it
        a = run.start * self._hop
        body = self._slice(a, a + run.n * self._hop).astype(np.float64)
        if body.size == 0:
            return None, "no_audio", info
        peak = float(np.max(np.abs(body)))
        info["peak"] = round(peak, 4)
        if peak < MIN_PEAK:
            return None, "quiet", info
        return (a / sr, margin, run.pre_db), "clap", info

    # -- pairing ---------------------------------------------------------

    def _onset_in(self, lo: float, hi: float) -> bool:
        return any(lo < o < hi for o in self._onsets)

    def margin(self) -> float:
        """Margin in force now: the configured one plus any rate-guard boost."""
        extra = RATE_BOOST_DB if self._clock() < self._boost_until else 0.0
        base = self.peak_db
        if self.ratio_floor_db is not None:
            base = max(base, self.ratio_floor_db)
        return base + extra

    def _rate_guard(self) -> bool:
        """Count a would-be double clap; too many too fast raise the margin."""
        now = self._clock()
        self._events.append(now)
        while self._events and now - self._events[0] > RATE_WINDOW_S:
            self._events.popleft()
        if len(self._events) > RATE_MAX and now >= self._boost_until:
            self._boost_until = now + RATE_BOOST_S
            logger.warning(
                "clap rate guard: %d double claps in %.0f s, margin +%.0f dB "
                "(now %.1f dB) for %.0f s",
                len(self._events),
                RATE_WINDOW_S,
                RATE_BOOST_DB,
                self.margin(),
                RATE_BOOST_S,
            )
        return True

    def _register(self, t: float, margin: float, pre_db: float = -math.inf) -> bool:
        """Pair an accepted clap with the previous one; True on a double clap."""
        prev = self._prev_clap
        if prev is not None:
            gap = t - prev[0]
            if gap < PAIR_MIN_S:
                return False  # too close to be a second clap; keep the first
            if (
                gap <= PAIR_MAX_S
                and abs(margin - prev[1]) <= PAIR_LEVEL_DB
                and not self._onset_in(prev[0] - QUIET_BEFORE_S, prev[0])
                and not self._onset_in(prev[0] + RING_S, t)
                and prev[2] <= ISOLATION_DB  # first clap came out of quiet
            ):
                self._prev_clap = None
                return True
        self._prev_clap = (t, margin, pre_db)
        return False


def _clamp_peak_db(value: float) -> float:
    lo, hi = PEAK_DB_RANGE
    return float(min(hi, max(lo, value)))


def derive_floors(cal: dict | None) -> tuple[float, float]:
    """(ratio dB, absolute peak dBFS) floors from a calibration dict.

    Ratio = RATIO_FRACTION x the weakest calibration clap over the floor;
    peak = the weakest calibration clap minus FLOOR_HEADROOM_DB. With no
    calibration (or without the dBFS fields) the defaults apply.
    """
    try:
        peaks = (float(cal["peak1_dbfs"]), float(cal["peak2_dbfs"]))
        floor = float(cal["floor_dbfs"])
        weakest = min(peaks)
        if not all(math.isfinite(v) for v in (weakest, floor)):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return DEFAULT_RATIO_FLOOR_DB, DEFAULT_PEAK_FLOOR_DBFS
    lo, hi = RATIO_FLOOR_RANGE_DB
    ratio = min(hi, max(lo, RATIO_FRACTION * (weakest - floor)))
    lo, hi = PEAK_FLOOR_RANGE_DBFS
    peak = min(hi, max(lo, weakest - FLOOR_HEADROOM_DB))
    return round(ratio, 2), round(peak, 2)


def effective_margin(calibrated_db: float) -> float:
    """Margin the detector uses: the calibrated value, capped at MARGIN_CAP_DB."""
    return min(_clamp_peak_db(calibrated_db), MARGIN_CAP_DB)


# -- calibration ------------------------------------------------------------


def default_home() -> Path:
    return Path(os.environ.get("JARVIS_HOME") or (Path.home() / ".jarvis"))


def default_sleep_state_path() -> Path:
    return default_home() / "sleep_state.json"


def default_calibration_path() -> Path:
    return default_home() / CALIBRATION_NAME


def analyze_recording(x: np.ndarray, sample_rate: int = SAMPLE_RATE) -> dict:
    """Find two claps in a recording and derive the detector's peak margin.

    x: mono audio, float in [-1, 1] at sample_rate. The noise floor is the
    median frame level (claps cover well under 1% of frames). The two
    strongest spikes, at least CAL_SPIKE_GAP_S apart, must each stand
    CAL_MIN_SPIKE_DB above that floor. Raises ValueError when they do not.
    """
    hop = max(1, round(sample_rate * FRAME_S))
    levels = _frame_levels(np.asarray(x, dtype=np.float32), hop)
    if levels.size < 8:
        raise ValueError("recording too short")
    floor = float(np.median(levels))
    spikes = []
    for i in range(1, levels.size - 1):
        if levels[i] < floor + CAL_MIN_SPIKE_DB:
            continue
        is_peak = levels[i] >= levels[i - 1] and levels[i] > levels[i + 1]
        if is_peak and levels[i] - levels[i - 1] >= 3.0:  # an attack, not a swell
            spikes.append(i)
    spikes.sort(key=lambda i: levels[i], reverse=True)
    picked: list[int] = []
    gap_frames = CAL_SPIKE_GAP_S * sample_rate / hop
    for i in spikes:
        if all(abs(i - j) >= gap_frames for j in picked):
            picked.append(i)
        if len(picked) == CAL_MIN_SPIKES:
            break
    if len(picked) < CAL_MIN_SPIKES:
        raise ValueError(
            f"found {len(picked)} clap spike(s) above the floor, need {CAL_MIN_SPIKES}"
        )
    picked.sort()
    rel = [float(levels[i]) - floor for i in picked]
    gap_s = (picked[1] - picked[0]) * hop / sample_rate
    margin = _clamp_peak_db(CAL_SAFETY * min(rel))
    return {
        "floor_dbfs": round(floor, 2),
        "peak1_dbfs": round(float(levels[picked[0]]), 2),
        "peak2_dbfs": round(float(levels[picked[1]]), 2),
        "onset_ratio": round(10.0 ** (min(rel) / 20.0), 3),
        "peak_margin_db": round(margin, 2),
        "gap_s": round(gap_s, 3),
        "gap_ok": PAIR_MIN_S <= gap_s <= PAIR_MAX_S,
    }


def load_calibration(path: Path | str) -> dict | None:
    """The calibration dict, or None when the file is missing or unusable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        margin = float(data["peak_margin_db"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(margin):
        return None
    return dict(data, peak_margin_db=_clamp_peak_db(margin))


def write_calibration(path: Path | str, cal: dict) -> Path:
    """Atomically write the calibration file (write temp, then rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cal, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


class CalibrationWatcher:
    """Applies the calibration file to a detector, re-checked at most every ttl_s.

    Hot reload: writing a new file (or deleting it) changes the detector on
    the next check, with no restart. Returns the active calibration or None.
    on_change(cal, detector) runs after each change, cal None when the file
    is gone or unusable.
    """

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        ttl_s: float = 2.0,
        clock=time.monotonic,
        on_change=None,
    ) -> None:
        self._path = Path(path) if path is not None else default_calibration_path()
        self._ttl_s = ttl_s
        self._clock = clock
        self._on_change = on_change
        self._checked: float | None = None
        self._stamp: tuple | None = None
        self.active: dict | None = None
        self._applied = False

    @property
    def path(self) -> Path:
        return self._path

    def poll(self, detector: ClapDetector) -> dict | None:
        now = self._clock()
        if self._checked is not None and now - self._checked < self._ttl_s:
            return self.active
        self._checked = now
        try:
            st = self._path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        if stamp == self._stamp:
            if not self._applied:  # first poll, no file: the default floors
                self._applied = True
                detector.set_floors(*derive_floors(None))
            return self.active
        self._applied = True
        self._stamp = stamp
        cal = load_calibration(self._path) if stamp is not None else None
        self.active = cal
        detector.set_peak_db(cal["peak_margin_db"] if cal else PEAK_DB)
        detector.set_floors(*derive_floors(cal))
        if self._on_change is not None:
            self._on_change(cal, detector)
        return cal


def should_wake(asleep: bool, double_clap: bool) -> bool:
    """A double clap wakes only while the laptop is asleep. Awake: nothing."""
    return bool(asleep and double_clap)


def read_asleep(path: Path) -> bool:
    """True only when the sleep state file says "asleep". Missing or bad = awake."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("state") == "asleep"


def _screensaver_alive(pid_path: Path) -> bool:
    """True when the screensaver pid file names a live process."""
    try:
        pid = int(Path(pid_path).read_text().strip())
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False
    return True


def read_since(path: Path) -> float | None:
    """Epoch seconds the sleep state last changed ("since"), or None."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        since = float(data["since"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return since if math.isfinite(since) else None


def grace_seconds() -> float:
    try:
        v = float(os.environ.get(GRACE_ENV, GRACE_DEFAULT_S))
    except ValueError:
        return GRACE_DEFAULT_S
    return v if math.isfinite(v) and v >= 0 else GRACE_DEFAULT_S


class ClapGrace:
    """Post-sleep grace: claps are ignored until grace_s after the sleep began.

    The screensaver, window effects and fans make noise right after sleep.
    `armed(asleep, since, now)` is True once the grace is over (logs once).
    `since` is epoch seconds from sleep_state.json; when missing or in the
    future the first asleep observation is the start.
    """

    def __init__(self, grace_s: float | None = None) -> None:
        self.grace_s = grace_seconds() if grace_s is None else grace_s
        self._start: float | None = None
        self._logged = False

    def armed(self, asleep: bool, since: float | None, now: float) -> bool:
        if not asleep:
            self._start = None
            self._logged = False
            return False
        if self._start is None:
            self._start = since if since is not None and since <= now else now
        if now - self._start < self.grace_s:
            return False
        if not self._logged:
            self._logged = True
            logger.warning("clap grace over, armed")
        return True


class SleepStateReader:
    """Sleep state read from disk, re-read at most once per ttl_s."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        ttl_s: float = 1.0,
        clock=time.monotonic,
    ) -> None:
        self._path = Path(path) if path is not None else default_sleep_state_path()
        self._ttl_s = ttl_s
        self._clock = clock
        self._checked: float | None = None
        self._asleep = False
        self._since: float | None = None

    def _refresh(self) -> None:
        now = self._clock()
        if self._checked is None or now - self._checked >= self._ttl_s:
            # Double clap only counts while the sleep screensaver is actually
            # playing; asleep with no screensaver = hey-Jarvis stays live.
            self._asleep = read_asleep(self._path) and _screensaver_alive(
                self._path.with_name("sleep_screensaver.pid")
            )
            self._since = read_since(self._path)
            self._checked = now

    def is_asleep(self) -> bool:
        self._refresh()
        return self._asleep

    def since(self) -> float | None:
        """Epoch seconds the current state began (sleep_state.json "since")."""
        self._refresh()
        return self._since


def write_wav(path: Path | str, pcm: np.ndarray, sample_rate: int) -> Path:
    """Write mono int16 PCM as a WAV file (atomic)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    data = np.asarray(pcm, dtype=np.int16)
    with wave.open(str(tmp), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(data.tobytes())
    os.replace(tmp, path)
    return path


# -- calibrate command ------------------------------------------------------


def _record_raw_16k(seconds: float, prompt_at: float) -> np.ndarray:
    """Record the default mic like wake_client does (48 kHz int16, 1536-sample blocks)."""
    import sounddevice as sd

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from audio_dsp import Downsampler48to16

    down = Downsampler48to16()
    out: list[np.ndarray] = []

    def cb(indata, frames, time_info, status) -> None:
        out.append(down.process(np.frombuffer(bytes(indata), dtype=np.int16)))

    with sd.RawInputStream(
        samplerate=48000, channels=1, dtype="int16", blocksize=1536, callback=cb
    ):
        sd.sleep(int(prompt_at * 1000))
        print("clap twice now", flush=True)
        sd.sleep(int((seconds - prompt_at) * 1000))
    pcm = np.concatenate(out) if out else np.zeros(0, dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def _calibrate(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="python -m src.clap_detect calibrate")
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--prompt-at", type=float, default=2.5)
    p.add_argument(
        "--out", default=None, help="default: $JARVIS_HOME/clap_threshold.json"
    )
    args = p.parse_args(argv)
    print(
        f"recording {args.seconds:g} s of the raw mic; wait for the prompt, "
        "then clap twice",
        flush=True,
    )
    x = _record_raw_16k(args.seconds, args.prompt_at)
    try:
        cal = analyze_recording(x, SAMPLE_RATE)
    except ValueError as exc:
        print(f"calibration failed: {exc}. Nothing written.", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else default_calibration_path()
    cal["recorded_s"] = args.seconds
    cal["created"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write_calibration(out, cal)
    print(json.dumps(cal, indent=2))
    if not cal["gap_ok"]:
        print(
            f"warning: clap gap {cal['gap_s']} s is outside 0.25-0.7 s", file=sys.stderr
        )
    print(f"wrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "calibrate":
        return _calibrate(argv[1:])
    print("usage: python -m src.clap_detect calibrate [--seconds N]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
