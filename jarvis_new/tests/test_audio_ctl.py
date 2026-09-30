"""Verified volume and media keys (src/system/audio_ctl.py).

The point of audio_ctl is that it never says "done" for a change that
didn't happen, and that media keys drive the *playing* player rather than
whichever one playerctl lists first. Both are checked with a fake wpctl /
playerctl that carries real state across calls.
"""

from __future__ import annotations

import subprocess

from system import audio_ctl


class FakeSink:
    """A fake wpctl/pactl audio sink: volume percent + mute flag."""

    def __init__(self, pct: int = 50, muted: bool = False, wpctl: bool = True) -> None:
        self.pct = pct
        self.muted = muted
        self.wpctl = wpctl  # False => wpctl absent, pactl path taken

    def which(self, name: str):
        if name == "wpctl" and not self.wpctl:
            return None
        return f"/usr/bin/{name}"

    def run(self, argv, capture_output=True, text=True, timeout=None):
        def done(code=0, out=""):
            return subprocess.CompletedProcess(argv, code, out, "")

        tool = argv[0]
        if tool == "wpctl" and argv[1] == "get-volume":
            vol = 0.0 if False else self.pct / 100
            return done(0, f"Volume: {vol:.2f}{' [MUTED]' if self.muted else ''}")
        if tool == "wpctl" and argv[1] == "set-volume":
            self.pct = round(float(argv[-1]) * 100)
            return done()
        if tool == "wpctl" and argv[1] == "set-mute":
            self.muted = argv[-1] == "1"
            return done()
        if tool == "pactl" and argv[1] == "get-sink-volume":
            return done(0, f"Volume: front-left: 0 / {self.pct}% / 0 dB")
        if tool == "pactl" and argv[1] == "set-sink-volume":
            self.pct = int(argv[-1].rstrip("%"))
            return done()
        if tool == "pactl" and argv[1] == "set-sink-mute":
            self.muted = argv[-1] == "1"
            return done()
        return done(1)


def test_status_reads_back() -> None:
    sink = FakeSink(pct=42, muted=False)
    ok, say = audio_ctl.volume("status", run=sink.run, which=sink.which)
    assert ok and "42 percent" in say


def test_up_and_down_step_and_confirm() -> None:
    sink = FakeSink(pct=40)
    ok, say = audio_ctl.volume("up", run=sink.run, which=sink.which)
    assert ok and sink.pct == 50 and "50 percent" in say
    ok, say = audio_ctl.volume("down", run=sink.run, which=sink.which)
    assert ok and sink.pct == 40


def test_set_exact_level() -> None:
    sink = FakeSink(pct=10)
    ok, _ = audio_ctl.volume("set", level=75, run=sink.run, which=sink.which)
    assert ok and sink.pct == 75


def test_raising_a_muted_sink_unmutes_first() -> None:
    sink = FakeSink(pct=30, muted=True)
    ok, _ = audio_ctl.volume("up", run=sink.run, which=sink.which)
    assert ok and sink.muted is False and sink.pct == 40


def test_mute_and_unmute_verified() -> None:
    sink = FakeSink(pct=30)
    ok, say = audio_ctl.volume("mute", run=sink.run, which=sink.which)
    assert ok and sink.muted is True and say == "Muted."
    ok, say = audio_ctl.volume("unmute", run=sink.run, which=sink.which)
    assert ok and sink.muted is False


def test_no_audio_stack_is_a_failure_not_a_lie() -> None:
    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, "", "")

    ok, say = audio_ctl.volume("down", run=run, which=lambda n: None)
    assert ok is False and "audio" in say.lower()


def test_change_that_does_not_move_is_reported() -> None:
    """wpctl returns 0 but the level never changes -> failure, not 'done'."""
    sink = FakeSink(pct=50)

    def run(argv, **kw):
        if argv[:2] == ["wpctl", "set-volume"]:
            return subprocess.CompletedProcess(
                argv, 0, "", ""
            )  # pretend, but don't move
        return sink.run(argv, **kw)

    ok, say = audio_ctl.volume("down", run=run, which=sink.which)
    assert ok is False and "didn't move" in say


def test_pactl_fallback_when_no_wpctl() -> None:
    sink = FakeSink(pct=20, wpctl=False)
    ok, _ = audio_ctl.volume("up", run=sink.run, which=sink.which)
    assert ok and sink.pct == 30


# --- media ---


class FakePlayers:
    """A fake playerctl: named MPRIS players, each with a status."""

    def __init__(self, players: dict[str, str]) -> None:
        self.players = dict(players)

    def run(self, argv, capture_output=True, text=True, timeout=None):
        def done(code=0, out=""):
            return subprocess.CompletedProcess(argv, code, out, "")

        if argv[:2] == ["playerctl", "-l"]:
            return done(0, "\n".join(self.players))
        if len(argv) >= 4 and argv[1] == "-p" and argv[3] == "status":
            name = argv[2]
            return (
                done(0, self.players.get(name, "Stopped"))
                if name in self.players
                else done(1)
            )
        if len(argv) >= 4 and argv[1] == "-p":
            name, action = argv[2], argv[3]
            if action in ("pause", "stop"):
                self.players[name] = "Paused" if action == "pause" else "Stopped"
            elif action == "play":
                self.players[name] = "Playing"
            return done()
        return done(1)


def test_pause_hits_the_playing_player_not_the_first() -> None:
    # Spotify (paused) is listed first, the browser is actually playing.
    p = FakePlayers({"spotify": "Paused", "brave": "Playing"})
    ok, say = audio_ctl.media("pause", run=p.run)
    assert ok and p.players["brave"] == "Paused" and say == "Paused."


def test_pause_when_nothing_playing_is_honest() -> None:
    p = FakePlayers({"spotify": "Paused"})
    ok, say = audio_ctl.media("pause", run=p.run)
    assert ok and "Nothing is playing" in say


def test_play_resumes_a_paused_player() -> None:
    p = FakePlayers({"spotify": "Paused"})
    ok, say = audio_ctl.media("play", run=p.run)
    assert ok and p.players["spotify"] == "Playing" and say == "Playing."


def test_play_pause_toggles_on_state() -> None:
    p = FakePlayers({"brave": "Playing"})
    ok, _ = audio_ctl.media("play-pause", run=p.run)
    assert ok and p.players["brave"] == "Paused"


def test_no_player_at_all() -> None:
    p = FakePlayers({})
    ok, say = audio_ctl.media("pause", run=p.run)
    assert ok is False and "control" in say.lower()


def test_players_lists_status() -> None:
    p = FakePlayers({"a": "Playing", "b": "Paused"})
    assert dict(audio_ctl.players(run=p.run)) == {"a": "Playing", "b": "Paused"}
