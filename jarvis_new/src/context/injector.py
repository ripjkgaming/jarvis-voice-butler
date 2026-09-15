"""Context injector: compacts a snapshot into a ~2-line LLM context prefix.

Kept tiny on purpose — voice turns are latency-sensitive, so the agent gets
grounding facts, never raw JSON.
"""

from __future__ import annotations

from context.telemetry import TelemetrySnapshot


def compact_context(snap: TelemetrySnapshot) -> str:
    """Render snapshot as a short context line. Pure."""
    bits: list[str] = []
    if snap.battery_pct is not None:
        state = snap.power_state or "unknown drain"
        bits.append(f"battery {snap.battery_pct:.0f}% ({state})")
    if snap.hud_mode and snap.hud_mode != "idle":
        bits.append(f"HUD mode: {snap.hud_mode}")
    if snap.focus_target:
        bits.append(f"focus: {snap.focus_target}")
    if snap.altitude_m is not None:
        bits.append(f"altitude {snap.altitude_m:.0f}m")
    if snap.climb_rate_ms is not None:
        bits.append(f"climb {snap.climb_rate_ms:.0f}m/s")
    if snap.heart_rate is not None:
        bits.append(f"HR {snap.heart_rate}bpm")
    if snap.active_window:
        bits.append(f"window: {snap.active_window}")
    if not bits:
        return "Context: no telemetry (sensors quiet)."
    return "Context: " + "; ".join(bits) + "."


def grounding_instructions(snap: TelemetrySnapshot) -> str:
    """Full prefix for generate_reply: context + read-first rule. Pure."""
    return (
        compact_context(snap)
        + " Read this context first; never ask the user for state it contains."
    )
