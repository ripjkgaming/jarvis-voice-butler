import pytest

from context.telemetry import SimTelemetryFeed
from proactive.monitors import (
    drain_rate_per_min,
    predict_depletion_mins,
    should_alert,
    urgency_for,
    warning_line,
)
from proactive.watcher import ProactiveWatcher


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

    watcher = ProactiveWatcher(lambda: feed.step(), speak, poll_s=9999, cooldown_s=9999)
    # Feed history manually: 30 -> 22 -> 14 -> 6 over 3 simulated minutes.
    base = 1_000_000.0
    for i in range(4):
        await watcher._tick(base + i * 60)
    assert len(spoken) >= 1  # escalation fired during the drain
    assert all("Sir" in line for line in spoken)
