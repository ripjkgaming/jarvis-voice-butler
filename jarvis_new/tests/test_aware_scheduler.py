"""Always Aware scheduler: interval bounds, jitter, power, load, budget, lock."""

import random

import pytest

from aware import scheduler as sch


def sig(**kw):
    base = {
        "on_ac": True,
        "battery_pct": 100.0,
        "load_per_cpu": 0.2,
        "mem_available_gib": 8.0,
    }
    base.update(kw)
    return sch.Signals(**base)


def test_base_interval_is_180_with_jitter_inside_bounds():
    s = sch.Scheduler(rng=random.Random(1))
    seen = [s.next_delay(sig()) for _ in range(500)]
    assert all(120 <= d <= 300 for d in seen)
    assert min(seen) < 170 < 190 < max(seen)  # jitter actually varies
    assert all(180 * 0.8 - 1e-9 <= d <= 180 * 1.2 + 1e-9 for d in seen)


def test_change_shortens_unchanged_lengthens_and_clamps():
    s = sch.Scheduler(rng=random.Random(0), jitter=0.0)
    s.after_tick(changed=True, importance=0, opportunity=False)
    assert s.base == 150
    for _ in range(10):
        s.after_tick(changed=True, importance=3, opportunity=True)
    assert s.base == 120
    for _ in range(10):
        s.after_tick(changed=False, importance=0, opportunity=False)
    assert s.base == 300
    assert s.next_delay(sig()) == 1800  # long unchanged streak: top of ladder


def test_importance_two_or_opportunity_counts_as_change():
    s = sch.Scheduler(jitter=0.0)
    s.after_tick(changed=False, importance=2, opportunity=False)
    assert s.base == 150
    s.after_tick(changed=False, importance=1, opportunity=True)
    assert s.base == 120


def test_seen_low_importance_without_change_lengthens():
    s = sch.Scheduler(jitter=0.0)
    s.after_tick(changed=True, importance=0, opportunity=False)  # 150
    s.after_tick(changed=False, importance=1, opportunity=False)
    assert s.base == 210


def test_battery_floor_and_low_battery_stop():
    s = sch.Scheduler(jitter=0.0)
    s.base = 120
    assert s.next_delay(sig(on_ac=False, battery_pct=80)) == 240
    assert s.next_delay(sig(on_ac=True, battery_pct=80)) == 120
    assert sch.guard(sig(on_ac=False, battery_pct=20)) == "battery_low"
    assert sch.guard(sig(on_ac=True, battery_pct=20)) is None


@pytest.mark.parametrize(
    "kw,reason",
    [
        ({"load_per_cpu": 0.86}, "load_high"),
        ({"mem_available_gib": 1.4}, "memory_low"),
        ({"load_per_cpu": 0.85}, None),
        ({"mem_available_gib": 1.5}, None),
    ],
)
def test_load_guard(kw, reason):
    assert sch.guard(sig(**kw)) == reason


def test_backoff_429_is_30_minutes_and_5xx_10_minutes():
    s = sch.Scheduler(jitter=0.0)
    s.note_error("quota", now=1000.0)
    assert s.blocked_until == 1000.0 + 1800
    assert s.backoff_reason(now=1500.0) == "backoff_quota"
    assert s.backoff_reason(now=2801.0) is None
    s.note_error("outage", now=5000.0)
    assert s.blocked_until == 5600.0


def test_capture_failures_three_in_a_row_pause_an_hour():
    s = sch.Scheduler()
    for _ in range(2):
        s.note_error("capture", now=10.0)
    assert s.backoff_reason(now=11.0) is None
    s.note_error("capture", now=10.0)
    assert s.backoff_reason(now=11.0) == "backoff_capture"
    assert s.blocked_until == 3610.0
    s2 = sch.Scheduler()
    s2.note_error("capture", now=0)
    s2.note_error("capture", now=0)
    s2.after_tick(changed=True, importance=0, opportunity=False)  # success resets
    s2.note_error("capture", now=0)
    assert s2.backoff_reason(now=1) is None


def test_floor_enforced_between_ticks():
    s = sch.Scheduler()
    s.last_tick = 1000.0
    assert not s.may_tick(now=1000.0 + 119)
    assert s.may_tick(now=1000.0 + 120)


def test_tick_lock_prevents_overlap(tmp_path):
    lock = tmp_path / "tick.lock"
    with sch.tick_lock(lock) as got:
        assert got
        with sch.tick_lock(lock) as again:
            assert not again
    with sch.tick_lock(lock) as later:
        assert later


def test_read_signals_parses_proc_and_sys(tmp_path):
    (tmp_path / "loadavg").write_text("6.00 5.0 4.0 1/100 99\n")
    (tmp_path / "meminfo").write_text("MemTotal: 1 kB\nMemAvailable: 2097152 kB\n")
    ps = tmp_path / "ps"
    (ps / "ACAD").mkdir(parents=True)
    (ps / "ACAD" / "online").write_text("0\n")
    (ps / "BAT0").mkdir()
    (ps / "BAT0" / "capacity").write_text("55\n")
    s = sch.read_signals(
        loadavg=tmp_path / "loadavg",
        meminfo=tmp_path / "meminfo",
        power=ps,
        nproc=12,
    )
    assert s.on_ac is False
    assert s.battery_pct == 55
    assert s.load_per_cpu == pytest.approx(0.5)
    assert s.mem_available_gib == pytest.approx(2.0)


def test_read_signals_missing_files_assume_ac_and_ok(tmp_path):
    s = sch.read_signals(
        loadavg=tmp_path / "x", meminfo=tmp_path / "y", power=tmp_path / "z", nproc=4
    )
    assert s.on_ac is True
    assert sch.guard(s) is None


# ------------------------------------------------------- activity-aware pacing
def test_unchanged_streak_climbs_ladder_and_change_resets():
    s = sch.Scheduler(jitter=0.0)
    s.base = sch.MAX_S
    got = []
    for _ in range(5):
        s.after_tick(changed=False, importance=0, opportunity=False)
        got.append(s.next_delay(sig()))
    assert got == [300, 600, 1200, 1800, 1800]
    s.after_tick(changed=True, importance=1, opportunity=False, activity="writing")
    assert s.quiet_streak == 0 and s.next_delay(sig()) == 270


def test_media_counts_as_quiet_even_when_frames_change():
    s = sch.Scheduler(jitter=0.0)
    s.base = sch.MAX_S
    s.after_tick(changed=True, importance=0, opportunity=False, activity="media")
    s.after_tick(changed=True, importance=1, opportunity=False, activity="media")
    assert s.next_delay(sig()) == 600
    s.after_tick(changed=True, importance=2, opportunity=False, activity="media")
    assert s.quiet_streak == 0


def test_study_looks_again_in_a_minute_and_may_tick_allows_it():
    s = sch.Scheduler(jitter=0.0)
    s.last_tick = 1000.0
    s.after_tick(changed=True, importance=1, opportunity=True, study=True)
    assert s.next_delay(sig()) == sch.STUDY_S
    assert s.may_tick(1000.0 + sch.STUDY_S)
    s.after_tick(changed=True, importance=1, opportunity=False)
    assert not s.may_tick(1000.0 + sch.STUDY_S)


def test_resume_starts_fast_again():
    s = sch.Scheduler(jitter=0.0)
    s.base, s.quiet_streak, s.study = sch.MAX_S, 4, True
    s.resume()
    assert (s.base, s.quiet_streak, s.study) == (sch.BASE_S, 0, False)
    assert s.next_delay(sig()) == sch.BASE_S
