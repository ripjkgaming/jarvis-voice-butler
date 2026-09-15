from context.injector import compact_context, grounding_instructions
from context.telemetry import SimTelemetryFeed, TelemetrySnapshot, read_real_snapshot


def test_compact_context_empty_sensors() -> None:
    assert "sensors quiet" in compact_context(TelemetrySnapshot())


def test_compact_context_includes_key_facts() -> None:
    snap = TelemetrySnapshot(
        battery_pct=42,
        power_state="discharging",
        hud_mode="climb",
        focus_target="ascent corridor",
        altitude_m=5000,
        heart_rate=99,
    )
    line = compact_context(snap)
    assert "42%" in line and "climb" in line and "ascent corridor" in line
    assert len(line) < 300  # latency guard: stays tiny


def test_grounding_rule_appended() -> None:
    assert "never ask" in grounding_instructions(TelemetrySnapshot()).lower()


def test_sim_feed_steps_and_sticks() -> None:
    feed = SimTelemetryFeed.orbital_climb(start_pct=100, drain_per_step=10, steps=3)
    assert feed.step().battery_pct == 100
    assert feed.step().battery_pct == 90
    assert feed.step().battery_pct == 80
    assert feed.step().battery_pct == 80  # sticks on last frame


def test_real_snapshot_never_raises() -> None:
    snap = read_real_snapshot()
    assert isinstance(snap, TelemetrySnapshot)
