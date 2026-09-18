import datetime

import pytest

from context.telemetry import SimTelemetryFeed
from proactive.monitors import (
    drain_rate_per_min,
    predict_depletion_mins,
    should_alert,
    urgency_for,
    warning_line,
)
from proactive.policy import (
    ProactivePolicy,
    in_quiet_hours,
    max_per_day,
    parse_quiet_hours,
)
from proactive.watcher import ProactiveWatcher, backoff_sleep


def test_drain_rate_needs_two_samples() -> None:
    assert drain_rate_per_min([]) == 0.0
    assert drain_rate_per_min([(0.0, 90.0)]) == 0.0


def test_drain_rate_math() -> None:
    # 10 points over 5 minutes -> 2 pct/min.
    assert drain_rate_per_min([(0.0, 100.0), (300.0, 90.0)]) == pytest.approx(2.0)


def test_drain_rate_ignores_charging() -> None:
    assert drain_rate_per_min([(0.0, 50.0), (60.0, 55.0)]) == 0.0


def test_predict_none_when_idle() -> None:
    assert predict_depletion_mins(80.0, 0.0) is None
    assert predict_depletion_mins(80.0, 2.0) == pytest.approx(40.0)


def test_urgency_tiers() -> None:
    assert urgency_for(60.0, 90) == "ok"
    assert urgency_for(20.0, 90) == "info"
    assert urgency_for(8.0, 90) == "urgent"
    assert urgency_for(3.0, 90) == "critical"
    assert urgency_for(None, 4) == "critical"


def test_alert_only_on_escalation() -> None:
    assert should_alert("ok", "info", cooldown_s=60, last_alert_ts=None, now=0)
    assert not should_alert("info", "info", cooldown_s=60, last_alert_ts=None, now=0)
    assert not should_alert("urgent", "info", cooldown_s=60, last_alert_ts=None, now=0)
    assert not should_alert(
        "ok", "urgent", cooldown_s=120, last_alert_ts=100, now=150
    )  # cooldown


def test_warning_lines_have_pushback() -> None:
    assert "insist" in warning_line("critical", 3.0, 8)
    assert "caution" in warning_line("urgent", 8.0, 15).lower()


@pytest.mark.asyncio
async def test_watcher_alerts_once_on_climb() -> None:
    feed = SimTelemetryFeed.orbital_climb(start_pct=30, drain_per_step=8, steps=4)
    spoken: list[str] = []

    async def speak(line: str) -> None:
        spoken.append(line)

    # Frozen midday clock + silent log: the date-agnostic old base sat at
    # ~21:46 local (box TZ), one code change away from the quiet window.
    base = datetime.datetime(2026, 9, 21, 12, 0).timestamp()
    watcher = ProactiveWatcher(
        lambda: feed.step(),
        speak,
        poll_s=9999,
        cooldown_s=9999,
        now_fn=lambda: base,
        log_fn=lambda *a: None,
    )
    # Feed history manually: 30 -> 22 -> 14 -> 6 over 3 simulated minutes.
    for i in range(4):
        await watcher._tick(base + i * 60)
    assert len(spoken) >= 1  # escalation fired during the drain
    assert all("Sir" in line for line in spoken)


# --- Proactive delivery policy (Phase 5 slice 5.2) ---

DAY = datetime.datetime(2026, 9, 21)
NOON = DAY.replace(hour=12).timestamp()
NIGHT = DAY.replace(hour=23, minute=30).timestamp()
MORNING = DAY.replace(hour=7, minute=30).timestamp()


def _policy(**kwargs):
    logged: list[str] = []
    kwargs.setdefault("window", (22 * 60, 7 * 60))
    kwargs.setdefault("cap", 3)
    kwargs.setdefault("log_fn", lambda c, d: logged.append(f"{c} {d}"))
    policy = ProactivePolicy(**kwargs)
    return policy, logged


def test_quiet_parse_and_overnight_wrap(monkeypatch) -> None:
    assert parse_quiet_hours("22:00-07:00") == (1320, 420)
    assert parse_quiet_hours("garbage") == (1320, 420)
    assert parse_quiet_hours("25:00-07:00") == (1320, 420)
    window = (1320, 420)
    assert in_quiet_hours(DAY.replace(hour=23), window)
    assert in_quiet_hours(DAY.replace(hour=6, minute=59), window)
    assert not in_quiet_hours(DAY.replace(hour=7), window)
    assert not in_quiet_hours(DAY.replace(hour=21, minute=59), window)
    assert not in_quiet_hours(DAY.replace(hour=12), window)
    assert in_quiet_hours(DAY.replace(hour=12), (540, 1020))
    assert not in_quiet_hours(DAY.replace(hour=18), (540, 1020))
    monkeypatch.setenv("JARVIS_QUIET_HOURS", "09:00-17:00")
    from proactive.policy import quiet_hours

    assert quiet_hours() == (540, 1020)


def test_max_per_day_env(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_PROACTIVE_MAX_PER_DAY", raising=False)
    assert max_per_day() == 3
    monkeypatch.setenv("JARVIS_PROACTIVE_MAX_PER_DAY", "1")
    assert max_per_day() == 1
    monkeypatch.setenv("JARVIS_PROACTIVE_MAX_PER_DAY", "many")
    assert max_per_day() == 3


def test_quiet_queues_every_urgency_never_speaks() -> None:
    policy, logged = _policy()
    for urgency in ("info", "urgent", "critical"):
        decision = policy.consider(f"note {urgency}", urgency=urgency, now=NIGHT)
        assert decision.action == "defer", urgency
        assert decision.reason == "queued-quiet-hours"
        assert decision.texts == []
    assert policy.queued_count == 3
    # Still quiet half an hour later: nothing flushes.
    assert policy.drain_queue(NIGHT + 1800).texts == []
    # Morning: the whole queue speaks as one delivery.
    flushed = policy.drain_queue(MORNING)
    assert flushed.action == "speak" and len(flushed.texts) == 3
    assert any("queued-quiet-hours" in line for line in logged)
    assert any("quiet-flush" in line for line in logged)


def test_cap_three_per_day_then_critical_bypass() -> None:
    policy, logged = _policy()
    for i in range(3):
        decision = policy.consider(f"urgent {i}", urgency="urgent", now=NOON + i)
        assert decision.action == "speak"
    fourth = policy.consider("urgent 3", urgency="urgent", now=NOON + 30)
    assert (fourth.action, fourth.reason) == ("drop", "capped")
    bypass = policy.consider("battery critical", urgency="critical", now=NOON + 60)
    assert bypass.action == "speak" and bypass.reason == "urgent-bypass"
    assert any("urgent-bypass" in line for line in logged)
    assert any("capped" in line for line in logged)
    # Tomorrow the cap resets.
    morrow = NOON + 86400
    assert policy.consider("urgent x", urgency="urgent", now=morrow).action == "speak"


def test_dedupe_one_hour() -> None:
    policy, _ = _policy()
    first = policy.consider("note", urgency="urgent", now=NOON)
    assert first.action == "speak"
    dup = policy.consider("note", urgency="urgent", now=NOON + 600)
    assert (dup.action, dup.reason) == ("drop", "deduped")
    later = policy.consider("note", urgency="urgent", now=NOON + 3700)
    assert later.action == "speak"


def test_dedupe_holds_inside_batch_and_queue() -> None:
    policy, _ = _policy()
    assert policy.consider("routine", urgency="info", now=NOON).action == "defer"
    dup = policy.consider("routine", urgency="info", now=NOON + 60)
    assert (dup.action, dup.reason) == ("drop", "deduped")
    assert policy.consider("late", urgency="info", now=NIGHT).action == "defer"
    assert policy.consider("late", urgency="info", now=NIGHT + 60).reason == "deduped"


def test_batching_30_min_window() -> None:
    policy, logged = _policy()
    assert policy.consider("a", urgency="info", now=NOON).reason == "batched-waiting"
    assert (
        policy.consider("b", urgency="info", now=NOON + 600).reason == "batched-waiting"
    )
    # Urgent skips the batch and speaks at once.
    urgent = policy.consider("u", urgency="urgent", now=NOON + 900)
    assert urgent.action == "speak" and urgent.texts == ["u"]
    flushed = policy.consider("c", urgency="info", now=NOON + 1900)
    assert flushed.reason == "batched-flush"
    assert flushed.texts == ["a", "b", "c"]
    assert any("batched-flush" in line for line in logged)


def test_seven_day_sim() -> None:
    """Frozen 7-day run: <=3/day, zero dups, zero quiet-hour deliveries."""
    logged: list[str] = []
    policy = ProactivePolicy(
        window=(22 * 60, 7 * 60), cap=3, log_fn=lambda c, d: logged.append(f"{c} {d}")
    )
    spoken: list[tuple[str, float, str]] = []  # (text, ts, day)
    deliveries: list[tuple[str, float]] = []  # (day, ts) per speak decision

    def at(day: int, h: int, m: int = 0) -> float:
        return (
            (DAY + datetime.timedelta(days=day)).replace(hour=h, minute=m).timestamp()
        )

    def offer(text: str, urgency: str, ts: float) -> None:
        day = datetime.datetime.fromtimestamp(ts).date().isoformat()
        for decision in (
            policy.consider(text, urgency=urgency, now=ts),
            policy.drain_queue(ts) if urgency != "critical" else None,
        ):
            if decision is not None and decision.action == "speak":
                deliveries.append((day, ts))
                spoken.extend((t, ts, day) for t in decision.texts)

    for day in range(7):
        offer("yesterday-late", "info", at(day, 7))  # flush prior queue
        offer(f"info-a-{day}", "info", at(day, 8))
        offer(f"info-a-{day}", "info", at(day, 8, 5))  # duplicate
        offer(f"info-b-{day}", "info", at(day, 8, 10))
        offer(f"info-c-{day}", "info", at(day, 8, 40))  # batch flush
        offer(f"urgent-{day}", "urgent", at(day, 9))
        offer(f"extra-{day}", "urgent", at(day, 10))  # capped
        if day == 2:
            offer("battery critical now", "critical", at(day, 13))  # bypass
        offer("late-note", "info", at(day, 23, 30))  # queued, never spoken
    offer("final", "info", at(7, 7))  # flush the last late note

    per_day: dict[str, int] = {}
    for day, _ in deliveries:
        per_day[day] = per_day.get(day, 0) + 1
    for day, count in per_day.items():
        limit = 4 if day == (DAY + datetime.timedelta(days=2)).date().isoformat() else 3
        assert count <= limit, f"{day}: {count} deliveries"
    for text, ts, _ in spoken:
        hour = datetime.datetime.fromtimestamp(ts).hour
        assert not (hour >= 22 or hour < 7), f"quiet-hour speech: {text} @ {hour}"
    seen: dict[str, float] = {}
    for text, ts, _ in spoken:
        if text in seen:
            assert ts - seen[text] >= 3600, f"duplicate within 1h: {text}"
        seen[text] = ts
    assert any("urgent-bypass" in line for line in logged)
    assert any("capped" in line for line in logged)
    assert any("deduped" in line for line in logged)
    assert any("queued-quiet-hours" in line for line in logged)
    assert sum(1 for t, _, _ in spoken if t == "late-note") == 7


def test_backoff_sleep_unit() -> None:
    assert backoff_sleep(15.0, 0, 300.0) == 15.0
    assert backoff_sleep(15.0, 1, 300.0) == 30.0
    assert backoff_sleep(15.0, 2, 300.0) == 60.0
    assert backoff_sleep(15.0, 99, 300.0) == 300.0


@pytest.mark.asyncio
async def test_watcher_policy_cap_holds_urgent_not_critical() -> None:
    """Zero cap: an urgent escalation is dropped with a reason, while a
    critical (safety) escalation still speaks via the logged bypass."""
    from context.telemetry import TelemetrySnapshot

    spoken: list[str] = []
    logged: list[str] = []

    async def speak(line: str) -> None:
        spoken.append(line)

    policy = ProactivePolicy(
        window=(22 * 60, 7 * 60), cap=0, log_fn=lambda c, d: logged.append(d)
    )
    snaps = iter(
        [TelemetrySnapshot(battery_pct=24.0), TelemetrySnapshot(battery_pct=4.0)]
    )
    watcher = ProactiveWatcher(
        lambda: next(snaps),
        speak,
        poll_s=9999,
        cooldown_s=0,
        policy=policy,
        now_fn=lambda: NOON,
        log_fn=lambda *a: None,
    )
    # Seed a drain that lands on urgent (7.5 min left), then on critical.
    watcher._history = [(NOON - 300, 40.0), (NOON - 240, 32.0)]
    watcher._prev_urgency = "info"
    watcher._last_ok = NOON - 240
    await watcher._tick(NOON)  # 24% @ 3.2/min -> urgent -> capped
    assert spoken == []
    assert any("capped" in line for line in logged)
    watcher._history = [(NOON - 120, 30.0), (NOON - 60, 12.0)]
    await watcher._tick(NOON + 60)  # 4% -> critical -> bypass speaks
    assert len(spoken) == 1 and "critical" in spoken[0].lower()
    assert any("urgent-bypass" in line for line in logged)


@pytest.mark.asyncio
async def test_watcher_overlap_skip_and_stale_takeover() -> None:
    feed = SimTelemetryFeed.orbital_climb(start_pct=90, drain_per_step=0, steps=2)
    logged: list[str] = []
    watcher = ProactiveWatcher(
        lambda: feed.step(),
        lambda line: _noop_coro(),
        poll_s=15.0,
        cooldown_s=9999,
        now_fn=lambda: NOON,
        log_fn=lambda c, d: logged.append(d),
    )
    watcher._in_tick = True
    await watcher._tick(NOON)
    assert any("overlap-skip" in line for line in logged)
    watcher._in_tick = False
    # Stale sensors: dark for an hour, then one good read takes over fresh.
    watcher._history = [(NOON - 5000, 80.0), (NOON - 4900, 70.0)]
    watcher._prev_urgency = "urgent"
    watcher._last_ok = NOON - 3600
    await watcher._tick(NOON)
    assert any("stale-takeover" in line for line in logged)
    assert watcher._prev_urgency in ("ok", "info", "urgent", "critical")


@pytest.mark.asyncio
async def test_watcher_failure_backoff() -> None:
    def _boom():
        raise RuntimeError("sensor dark")

    async def _speak(line: str) -> None:
        raise AssertionError("must not speak on sensor failure")

    watcher = ProactiveWatcher(
        _boom, _speak, poll_s=15.0, log_fn=lambda *a: None, now_fn=lambda: NOON
    )
    assert watcher.sleep_for() == 15.0
    await watcher._tick(NOON)
    await watcher._tick(NOON + 15)
    assert watcher._failures == 2
    assert watcher.sleep_for() == 60.0


async def _noop_coro() -> None:
    return None
