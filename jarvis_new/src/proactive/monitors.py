"""Proactive & predictive dialogue: pure drain math + urgency thresholds.

All functions are pure (history in, numbers out) so they unit-test without
hardware. The async watcher loop lives in watcher.py.
"""

from __future__ import annotations

INFO_MINUTES = 30.0
URGENT_MINUTES = 10.0
CRITICAL_MINUTES = 5.0


def drain_rate_per_min(history: list[tuple[float, float]]) -> float:
    """Battery drain in pct-points per minute from [(ts, pct)] samples. Pure.

    Returns 0.0 when there are fewer than 2 samples, time runs backwards,
    or the battery is charging (pct rising).
    """
    if len(history) < 2:
        return 0.0
    (t0, p0), (t1, p1) = history[0], history[-1]
    dt_min = (t1 - t0) / 60.0
    if dt_min <= 0:
        return 0.0
    return max(0.0, (p0 - p1) / dt_min)


def predict_depletion_mins(current_pct: float, drain_per_min: float) -> float | None:
    """Minutes until empty at the current drain. None when not draining. Pure."""
    if drain_per_min <= 0:
        return None
    return max(0.0, current_pct / drain_per_min)


def urgency_for(mins_left: float | None, pct: float) -> str:
    """Map prediction to ok/info/urgent/critical. Pure."""
    if mins_left is None:
        return "critical" if pct <= 5 else "ok"
    if mins_left <= CRITICAL_MINUTES or pct <= 5:
        return "critical"
    if mins_left <= URGENT_MINUTES or pct <= 12:
        return "urgent"
    if mins_left <= INFO_MINUTES or pct <= 25:
        return "info"
    return "ok"


_RANK = {"ok": 0, "info": 1, "urgent": 2, "critical": 3}


def should_alert(
    prev: str,
    new: str,
    *,
    cooldown_s: float,
    last_alert_ts: float | None,
    now: float,
) -> bool:
    """Edge-triggered alert gate: only on escalation past cooldown. Pure."""
    if _RANK.get(new, 0) <= _RANK.get(prev, 0) or _RANK.get(new, 0) == 0:
        return False
    if last_alert_ts is None:
        return True
    return (now - last_alert_ts) >= cooldown_s


def warning_line(urgency: str, mins_left: float | None, pct: float) -> str:
    """One-line spoke warning for the given urgency. Pure."""
    when = f"about {mins_left:.0f} minutes" if mins_left is not None else "imminently"
    if urgency == "critical":
        return (
            f"Power critical, Sir: {pct:.0f} percent, depletion {when}. "
            "I must insist we land or charge now."
        )
    if urgency == "urgent":
        return (
            f"A word of caution, Sir: {pct:.0f} percent, depletion {when}. "
            "Might I suggest easing the climb?"
        )
    return (
        f"For awareness, Sir: {pct:.0f} percent, depletion {when} at the current drain."
    )
