"""Volume and media keys that report what really happened.

The old voice tools ran ``pactl`` without checking the result and always
said "done": on PipeWire boxes where only ``wpctl`` works, "turn it down"
was announced and nothing changed. Here every change is:
- done with ``wpctl`` first (PipeWire), ``pactl`` as the fallback,
- read back afterwards, and reported as a failure if the level didn't move.

Media keys pick the right player: ``playerctl`` alone drives whichever
MPRIS player it lists first (often a paused Spotify, not the YouTube tab
that is playing), so pause/stop hit every *playing* player, next/previous
hit the playing one, and play resumes a paused one.

Every function takes an injectable ``run`` (subprocess.run signature)
and returns ``(ok, spoken_message)``. Never raises.
"""

from __future__ import annotations

import shutil
import subprocess

import audio_output

STEP_PCT = 10
MAX_PCT = 150


def _run(argv: list[str], run=None) -> subprocess.CompletedProcess | None:
    try:
        return (run or audio_output.run)(
            argv, capture_output=True, text=True, timeout=5.0
        )
    except Exception:
        return None


def _have(binary: str, which=shutil.which) -> bool:
    return which(binary) is not None


def get_volume(run=None) -> dict | None:
    """{"pct", "muted"} or None (no audio stack reachable)."""
    import taskbar

    return taskbar.volume_state(run=run)


def _set_pct(pct: int, run=None, which=shutil.which) -> bool:
    pct = max(0, min(MAX_PCT, int(pct)))
    if _have("wpctl", which):
        proc = _run(
            [
                "wpctl",
                "set-volume",
                "-l",
                "1.5",
                "@DEFAULT_AUDIO_SINK@",
                f"{pct / 100:.2f}",
            ],
            run,
        )
    else:
        proc = _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"], run)
    return proc is not None and proc.returncode == 0


def _set_mute(on: bool, run=None, which=shutil.which) -> bool:
    flag = "1" if on else "0"
    if _have("wpctl", which):
        proc = _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", flag], run)
    else:
        proc = _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", flag], run)
    return proc is not None and proc.returncode == 0


class VolumeReply(tuple):
    """Legacy unpacking with the same content-free receipt as modern callers."""

    def __new__(cls, result, say):
        from action_outcome import parse_outcome

        value = super().__new__(cls, (parse_outcome(result).verified, say))
        value.result = result
        return value


def _strict_volume(run=None, which=shutil.which):
    """Unknown mute is None, never a false unmute observation."""
    import re

    if _have("wpctl", which):
        proc = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], run)
        if proc is not None and proc.returncode == 0:
            match = re.search(r"Volume:\s*([0-9]*\.?[0-9]+)", proc.stdout or "")
            if match:
                return {
                    "pct": max(0, min(MAX_PCT, round(float(match[1]) * 100))),
                    "muted": "muted" in (proc.stdout or "").lower(),
                }
    proc = _run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], run)
    if proc is None or proc.returncode != 0:
        return None
    match = re.search(r"(\d{1,3})\s*%", proc.stdout or "")
    if not match:
        return None
    muted = None
    proc = _run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"], run)
    if proc is not None and proc.returncode == 0:
        match_mute = re.fullmatch(
            r"(?:Mute:\s*)?(yes|no)", (proc.stdout or "").strip(), re.I
        )
        if match_mute:
            muted = match_mute[1].lower() == "yes"
    return {"pct": max(0, min(MAX_PCT, int(match[1]))), "muted": muted}


def volume_result(
    action,
    level=None,
    run=None,
    which=shutil.which,
    *,
    step=STEP_PCT,
    authorize=lambda: None,
    request_id=None,
):
    """Execute once, then read a postcondition. No automatic mutation retry."""
    from action_outcome import ActionOutcome

    action = (action or "").strip().lower()
    effect = (
        "setting.mute"
        if action in {"mute", "unmute"}
        else "setting.volume_status"
        if action == "status"
        else "setting.volume"
    )
    dispatched = False

    def finish(state, check="not_attempted", reason="", evidence=None):
        return ActionOutcome(
            effect,
            state,
            "started" if dispatched else "not_started",
            check,
            "audio_service",
            reason,
            evidence or {},
            request_id=request_id,
        ).result()

    try:
        authorize()
        before = _strict_volume(run, which)
        if before is None:
            return finish("failed", reason="audio_unavailable")
        if action == "status":
            evidence = {"actual_pct": before["pct"]}
            if before["muted"] is not None:
                evidence["actual_muted"] = before["muted"]
            return finish("verified", "matched", evidence=evidence)
        if action in {"mute", "unmute"}:
            want = action == "mute"
            authorize()
            dispatched = True
            acknowledged = _set_mute(want, run, which)
            expected = {"expected_muted": want}
        else:
            if action in {"up", "down"}:
                target = before["pct"] + (step if action == "up" else -step)
            elif (
                action in {"set", "level"} and level is not None and type(level) is int
            ):
                target = level
            else:
                return finish("blocked", reason="invalid_action")
            target = max(0, min(MAX_PCT, target))
            expected = {"before_pct": before["pct"], "expected_pct": target}
            if action in {"up", "set", "level"}:
                if before["muted"] is None:
                    return finish("blocked", reason="mute_unknown")
                expected["expected_muted"] = False
                if before["muted"]:
                    authorize()
                    dispatched = True
                    if not _set_mute(False, run, which):
                        return finish(
                            "dispatched_unverified",
                            "unavailable",
                            "unmute_not_acknowledged",
                        )
            authorize()
            dispatched = True
            acknowledged = _set_pct(target, run, which)
        authorize()
        after = _strict_volume(run, which)
        if after is None:
            return finish(
                "dispatched_unverified", "unavailable", "readback_unavailable"
            )
        evidence = (
            {**expected, "actual_pct": after["pct"]}
            if effect == "setting.volume"
            else dict(expected)
        )
        if after["muted"] is not None:
            evidence["actual_muted"] = after["muted"]
        if "expected_muted" in expected and after["muted"] is None:
            return finish(
                "dispatched_unverified", "unavailable", "mute_unknown", evidence
            )
        matched = (
            effect == "setting.mute"
            or abs(after["pct"] - expected["expected_pct"]) <= 1
        ) and (
            "expected_muted" not in expected
            or after["muted"] is expected["expected_muted"]
        )
        if not matched:
            return finish("failed", "mismatched", "target_mismatch", evidence)
        # Matching read-back supports present state even if command ack was lost.
        return finish(
            "verified", "matched", "" if acknowledged else "ack_unavailable", evidence
        )
    except Exception:
        return finish(
            "dispatched_unverified" if dispatched else "blocked",
            "unavailable" if dispatched else "not_attempted",
            "authority_or_readback_unavailable",
        )


def volume(
    action: str, level: int | None = None, run=None, which=shutil.which
) -> tuple[bool, str]:
    """Tuple-compatible API; modern tools carry its structured result."""
    import phone_session
    from action_outcome import parse_outcome

    def authorize():
        phone_session.check_approval_effect(current=True)
        if phone_session.guest_required():
            raise PermissionError("Guest mode refuses owner actions")

    result = volume_result(action, level, run, which, authorize=authorize)
    outcome = parse_outcome(result)
    if outcome.verified:
        if action == "mute":
            say = "Muted."
        elif action == "unmute":
            say = "Unmuted."
        elif action == "status":
            say = f"Volume is at {outcome.evidence['actual_pct']} percent{', muted' if outcome.evidence.get('actual_muted') else ''}."
        else:
            say = f"Volume {outcome.evidence['actual_pct']} percent."
    elif outcome.reason == "audio_unavailable":
        say = "I can't reach the audio system, Sir: neither wpctl nor pactl answered."
    elif outcome.reason == "target_mismatch":
        say = "The volume didn't move to the requested state, Sir."
    else:
        say = outcome.say
    return VolumeReply(result, say)


# --- media keys ---


def players(run=None) -> list[tuple[str, str]]:
    """[(player, status)] for every MPRIS player, e.g. ("brave.instance1", "Playing")."""
    proc = _run(["playerctl", "-l"], run)
    if proc is None or proc.returncode != 0:
        return []
    out = []
    for name in (proc.stdout or "").split():
        st = _run(["playerctl", "-p", name, "status"], run)
        status = (
            (st.stdout or "").strip() if st is not None and st.returncode == 0 else ""
        )
        out.append((name, status))
    return out


def media(action: str, run=None) -> tuple[bool, str]:
    """pause / play / play-pause / stop / next / previous on the right player."""
    action = (action or "").strip().lower().replace("_", "-")
    found = players(run)
    if not found:
        return False, "Nothing is playing that I can control, Sir."
    playing = [p for p, s in found if s == "Playing"]
    paused = [p for p, s in found if s == "Paused"]
    if action == "play-pause":
        action = "pause" if playing else "play"
    if action in ("pause", "stop"):
        if not playing:
            return True, "Nothing is playing, Sir."
        targets = playing
    elif action == "play":
        if playing:
            return True, "It's already playing, Sir."
        targets = paused[:1] or [found[0][0]]
    elif action in ("next", "previous"):
        targets = (playing or paused or [found[0][0]])[:1]
    else:
        return False, f"I don't know the media action {action}."
    ok_all = True
    for p in targets:
        proc = _run(["playerctl", "-p", p, action], run)
        ok_all = ok_all and proc is not None and proc.returncode == 0
    if not ok_all:
        return False, f"The player refused to {action}, Sir."
    if action in ("pause", "stop", "play"):
        want = {"pause": "Paused", "stop": "Stopped", "play": "Playing"}[action]
        now = dict(players(run))
        if any(
            now.get(p) not in (want, "Stopped" if action == "pause" else want)
            for p in targets
        ):
            return False, f"I sent {action}, but the player didn't respond, Sir."
    return True, {
        "pause": "Paused.",
        "stop": "Stopped.",
        "play": "Playing.",
        "next": "Next track.",
        "previous": "Previous track.",
    }[action]
