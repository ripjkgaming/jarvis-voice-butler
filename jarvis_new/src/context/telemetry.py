"""Continuous multimodal grounding: HUD telemetry + laptop state snapshots.

Sim-first: SimTelemetryFeed scripts scenarios (e.g. orbital climb draining
the battery) for demos and tests. RealTelemetryFeed polls best-effort local
state (battery via upower, active window via kdotool fallback) and never
raises — missing sensors degrade to None so the agent reads context first
instead of asking the user.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TelemetrySnapshot:
    """One compact grounding frame. All sensor fields optional (None = unknown)."""

    ts: float = field(default_factory=time.time)
    # Laptop power (real).
    battery_pct: float | None = None
    power_state: str | None = None  # "discharging" | "charging" | None
    # HUD (sim now, frontend data messages later).
    hud_mode: str = "idle"
    focus_target: str | None = None
    # Flight dynamics (sim now, real feeds later).
    altitude_m: float | None = None
    climb_rate_ms: float | None = None
    # Biometrics (sim now, wearables later).
    heart_rate: int | None = None
    # Voice/session state (filled by the session manager).
    mic_level: float | None = None
    room_name: str | None = None
    active_window: str | None = None


class SimTelemetryFeed:
    """Scripted feed: each step() advances the scenario one frame. Pure-ish."""

    def __init__(self, frames: list[TelemetrySnapshot] | None = None) -> None:
        self._frames = list(frames or [TelemetrySnapshot(hud_mode="idle")])
        self._index = 0

    def step(self) -> TelemetrySnapshot:
        """Return the current frame and advance (sticks on the last frame)."""
        frame = self._frames[min(self._index, len(self._frames) - 1)]
        if self._index < len(self._frames) - 1:
            self._index += 1
        return frame

    def reset(self) -> None:
        self._index = 0

    @staticmethod
    def orbital_climb(
        start_pct: float = 100.0, drain_per_step: float = 8.0, steps: int = 10
    ) -> SimTelemetryFeed:
        """Battery-draining climb scenario for proactive-warning rehearsals."""
        frames = [
            TelemetrySnapshot(
                hud_mode="climb",
                focus_target="orbital ascent corridor",
                battery_pct=max(0.0, start_pct - i * drain_per_step),
                power_state="discharging",
                altitude_m=1000.0 * i,
                climb_rate_ms=120.0,
                heart_rate=96 + i,
            )
            for i in range(steps)
        ]
        return SimTelemetryFeed(frames)


def read_real_snapshot() -> TelemetrySnapshot:
    """Best-effort local snapshot. Never raises; unknowns stay None."""
    snap = TelemetrySnapshot(hud_mode="desktop")
    try:
        import subprocess

        out = subprocess.run(
            ["upower", "-i", "/org/freedesktop/UPower/devices/battery_BAT0"],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("percentage:"):
                snap.battery_pct = float(line.split(":")[1].strip().rstrip("%"))
            elif line.startswith("state:"):
                snap.power_state = line.split(":")[1].strip()
    except Exception:
        pass
    return snap
