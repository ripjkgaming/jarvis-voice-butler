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

STEP_PCT = 10
MAX_PCT = 150


def _run(argv: list[str], run=None) -> subprocess.CompletedProcess | None:
    try:
        return (run or subprocess.run)(
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


def volume(
    action: str, level: int | None = None, run=None, which=shutil.which
) -> tuple[bool, str]:
    """status / up / down / set / mute / unmute, verified by reading back."""
    action = (action or "").strip().lower()
    before = get_volume(run)
    if before is None:
        return (
            False,
            "I can't reach the audio system, Sir: neither wpctl nor pactl answered.",
        )
    if action == "status":
        return (
            True,
            f"Volume is at {before['pct']} percent{', muted' if before['muted'] else ''}.",
        )
    if action in ("mute", "unmute"):
        want = action == "mute"
        if not _set_mute(want, run, which):
            return False, f"The {action} command failed, Sir."
        after = get_volume(run)
        if after is None or after["muted"] != want:
            return False, f"I asked for {action}, but the sound didn't change, Sir."
        return True, "Muted." if want else f"Unmuted, volume {after['pct']} percent."
    if action in ("up", "down"):
        target = before["pct"] + (STEP_PCT if action == "up" else -STEP_PCT)
    elif action in ("set", "level"):
        if level is None:
            return False, "What level, Sir?"
        target = int(level)
    else:
        return False, f"I don't know the volume action {action}."
    target = max(0, min(MAX_PCT, target))
    if before["muted"] and action in ("up", "set", "level"):
        _set_mute(
            False, run, which
        )  # raising the volume of a muted sink does nothing audible
    if not _set_pct(target, run, which):
        return False, "The volume command failed, Sir."
    after = get_volume(run)
    if after is None:
        return False, "I set the volume but couldn't read it back, Sir."
    if after["pct"] == before["pct"] and target != before["pct"]:
        return False, f"The volume didn't move from {before['pct']} percent, Sir."
    return True, f"Volume {after['pct']} percent."


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
