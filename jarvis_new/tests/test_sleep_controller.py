"""Hermetic tests for src/sleep_controller.py.

Fake clock, fake signals, and every side effect (screensaver spawn, sleep_ctl,
away_log, wake_sequence, os.kill) replaced. Nothing touches the real
~/.jarvis, spawns a process, or signals a pid.
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

import sleep_controller as sc

REAL_CALL_SLEEP_CTL = sc._call_sleep_ctl  # captured before any fixture patches it
REAL_SPAWN = sc._spawn_screensaver


class FakeClock:
    def __init__(self, t: float = 1_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeProc:
    pid = 4242

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "jhome"
    monkeypatch.setenv("JARVIS_HOME", str(home))
    monkeypatch.setattr(sc, "_screensaver_proc", None)
    events: list = []

    def fake_spawn(args):
        events.append(("spawn", tuple(args)))
        return FakeProc()

    def fake_call(target, attr, *args):
        events.append((getattr(target, "name", target), attr))
        return None

    def fake_kill(pid, sig):
        if sig != 0:
            events.append(("kill", pid, sig))

    def fake_sleep_ctl(attr, **kw):
        events.append(("sleep_ctl.py", attr))
        return None

    monkeypatch.setattr(sc, "_spawn_screensaver", fake_spawn)
    monkeypatch.setattr(sc, "_call_module", fake_call)
    monkeypatch.setattr(sc, "_call_sleep_ctl", fake_sleep_ctl)
    monkeypatch.setattr(sc, "_fullscreen_seen", None)
    monkeypatch.setattr(sc, "_start_wake_anim", lambda: events.append("wake_anim"))
    monkeypatch.setattr(
        sc, "_start_wake_sequence", lambda *a, **k: events.append("wake_sequence")
    )
    monkeypatch.setattr(sc, "WAKE_WINDOWS_DELAY_S", 0)
    monkeypatch.setattr(sc, "RESTORE_VERIFY_S", 0)
    monkeypatch.setattr(sc, "windows_state_path", lambda: home / "sleep_windows.json")
    monkeypatch.setattr(sc, "_last_wake_windows", None)
    monkeypatch.setattr(sc.os, "kill", fake_kill)
    return {"home": home, "events": events}


# ------------------------------------------------------------- pure logic


def test_idle_seconds_constant_is_twenty_minutes():
    assert sc.IDLE_SECONDS == 1200


@pytest.mark.parametrize("flag", ["voice_active", "audio_playing", "fullscreen"])
def test_each_activity_flag_resets_to_now(flag):
    kwargs = {
        "input_idle_s": 900.0,
        "voice_active": False,
        "audio_playing": False,
        "fullscreen": False,
    }
    kwargs[flag] = True
    assert sc.merge_last_activity(1000.0, **kwargs) == 1000.0


def test_input_idle_counts_back_from_now():
    got = sc.merge_last_activity(
        1000.0,
        input_idle_s=100.0,
        voice_active=False,
        audio_playing=False,
        fullscreen=False,
    )
    assert got == 900.0


def test_negative_input_idle_is_clamped():
    got = sc.merge_last_activity(
        1000.0,
        input_idle_s=-5.0,
        voice_active=False,
        audio_playing=False,
        fullscreen=False,
    )
    assert got == 1000.0


def test_unknown_input_counts_as_activity():
    got = sc.merge_last_activity(
        1000.0,
        input_idle_s=None,
        voice_active=False,
        audio_playing=False,
        fullscreen=False,
    )
    assert got == 1000.0


def test_idle_timer_expires_exactly_at_threshold():
    timer = sc.IdleTimer(threshold=1200)
    timer.reset(1000.0)
    assert not timer.expired(2199.9)
    assert timer.expired(2200.0)


def test_idle_timer_observe_never_moves_backwards():
    timer = sc.IdleTimer(threshold=1200)
    timer.reset(5000.0)
    timer.observe(
        5100.0,
        input_idle_s=3000.0,  # real input is old, but the timer already saw more
        voice_active=False,
        audio_playing=False,
        fullscreen=False,
    )
    assert timer.last_activity == 5000.0


def test_audio_playing_detects_uncorked_stream():
    corked = "Sink Input #1\n\tCorked: yes\n\tMute: no\n"
    playing = "Sink Input #2\n\tCorked: no\n\tMute: no\n"
    assert sc.audio_playing_from_pactl(corked) is False
    assert sc.audio_playing_from_pactl(corked + playing) is True
    assert sc.audio_playing_from_pactl("") is False


# Shape of real `pactl list sink-inputs` output: jarvis-shell's WebKit webaudio
# streams are uncorked and unmuted but silent while the shell is open.
JARVIS_SHELL_STREAM = """\
Sink Input #171
\tDriver: PipeWire
\tCorked: no
\tMute: no
\tVolume: front-left: 65536 / 100% / 0.00 dB,   front-right: 65536 / 100% / 0.00 dB
\t        balance 0.00
\tProperties:
\t\tclient.api = "pipewire-pulse"
\t\tapplication.name = "jarvis-shell"
\t\tapplication.process.id = "6120"
\t\tapplication.process.binary = "WebKitWebProcess"
\t\tmedia.role = "webaudio"
\t\tpulse.corked = "false"

"""

BRAVE_STREAM = """\
Sink Input #900
\tCorked: no
\tMute: no
\tProperties:
\t\tapplication.name = "Brave"
\t\tapplication.process.id = "31337"
\t\tapplication.process.binary = "brave"

"""


def test_jarvis_shell_webaudio_streams_do_not_count_as_playing():
    assert sc.audio_playing_from_pactl(JARVIS_SHELL_STREAM) is False
    assert sc.audio_playing_from_pactl(JARVIS_SHELL_STREAM * 2) is False


def test_other_uncorked_stream_still_counts_beside_jarvis_shell():
    assert sc.audio_playing_from_pactl(JARVIS_SHELL_STREAM + BRAVE_STREAM) is True


def test_muted_stream_does_not_count_as_playing():
    muted = BRAVE_STREAM.replace("Mute: no", "Mute: yes")
    assert sc.audio_playing_from_pactl(muted) is False


def test_corked_stream_does_not_count_as_playing():
    corked = BRAVE_STREAM.replace("Corked: no", "Corked: yes")
    assert sc.audio_playing_from_pactl(corked) is False


def test_jarvis_shell_binary_alone_is_excluded():
    binary_only = BRAVE_STREAM.replace(
        'application.name = "Brave"', 'application.name = "x"'
    ).replace(
        'application.process.binary = "brave"',
        'application.process.binary = "jarvis-shell"',
    )
    assert sc.audio_playing_from_pactl(binary_only) is False


def test_webkit_child_of_jarvis_shell_is_excluded_by_pid():
    # No application.name at all: only the pid tells it is jarvis-shell's child.
    anonymous = (
        "Sink Input #7\n\tCorked: no\n\tMute: no\n\tProperties:\n"
        '\t\tapplication.process.id = "6120"\n'
        '\t\tapplication.process.binary = "WebKitWebProcess"\n\n'
    )
    assert sc.audio_playing_from_pactl(anonymous) is True
    assert sc.audio_playing_from_pactl(anonymous, own_pids={6120}) is False


def test_audio_playing_probe_fails_soft(monkeypatch):
    def boom(*a, **k):
        raise OSError("no pactl")

    monkeypatch.setattr(sc.subprocess, "run", boom)
    assert sc._audio_playing() is False


def test_audio_playing_probe_ignores_jarvis_shell_output(monkeypatch):
    class Done:
        stdout = JARVIS_SHELL_STREAM * 2

    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: Done())
    monkeypatch.setattr(sc, "_jarvis_shell_pids", lambda: frozenset())
    assert sc._audio_playing() is False


def test_jarvis_shell_pids_fail_soft(monkeypatch):
    def no_proc(*a, **k):
        raise OSError("no proc")

    monkeypatch.setattr(sc, "Path", no_proc)
    assert sc._jarvis_shell_pids() == frozenset()


def test_jarvis_shell_pids_is_a_set_of_ints():
    pids = sc._jarvis_shell_pids()
    assert isinstance(pids, frozenset)
    assert all(isinstance(p, int) and p > 0 for p in pids)


def test_fullscreen_from_listing_needs_active_fullscreen_window():
    active_full = {
        "status": "ok",
        "windows": [
            {"id": "a", "active": True, "fullScreen": True},
        ],
    }
    background_full = {
        "status": "ok",
        "windows": [
            {"id": "a", "active": False, "fullScreen": True},
            {"id": "b", "active": True, "fullScreen": False},
        ],
    }
    assert sc.fullscreen_from_listing(active_full) is True
    assert sc.fullscreen_from_listing(background_full) is False


@pytest.mark.parametrize(
    "listing",
    [
        {},
        {"status": "error", "error": "no dbus"},
        {"status": "timeout"},
        {"status": "ok"},
        {"status": "ok", "windows": "not a list"},
        None,
    ],
)
def test_fullscreen_unknown_is_not_fullscreen(listing):
    assert sc.fullscreen_from_listing(listing) is False


def test_fullscreen_probe_failure_is_fail_safe(monkeypatch):
    monkeypatch.setattr(sc, "_call_sleep_ctl", lambda attr, **kw: None)
    assert sc._probe_fullscreen() is False


def test_fullscreen_probe_reads_list_windows_and_is_cached(monkeypatch):
    calls = {"n": 0}

    def fake(attr, **kw):
        assert attr == "list_windows"
        calls["n"] += 1
        return {"status": "ok", "windows": [{"active": True, "fullScreen": True}]}

    monkeypatch.setattr(sc, "_fullscreen_seen", None)
    monkeypatch.setattr(sc, "_call_sleep_ctl", fake)
    assert sc._fullscreen_now(now=100.0) is True
    assert sc._fullscreen_now(now=100.0 + sc.FULLSCREEN_PROBE_S - 1) is True
    assert calls["n"] == 1  # cached: no second KWin script load
    assert sc._fullscreen_now(now=100.0 + sc.FULLSCREEN_PROBE_S) is True
    assert calls["n"] == 2


# ------------------------------------------------- sleep_ctl subprocess


class _Done:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _patch_run(monkeypatch, behaviour):
    calls: list = []

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        if isinstance(behaviour, BaseException):
            raise behaviour
        return behaviour

    monkeypatch.setattr(sc.subprocess, "run", fake_run)
    return calls


@pytest.mark.parametrize(
    "attr,cmd",
    [("sleep_windows", "sleep"), ("wake_windows", "wake"), ("list_windows", "list")],
)
def test_sleep_ctl_runs_in_fresh_subprocess(monkeypatch, attr, cmd):
    calls = _patch_run(monkeypatch, _Done(0, '{"status": "ok", "restored": 2}'))
    assert REAL_CALL_SLEEP_CTL(attr) == {"status": "ok", "restored": 2}
    argv, kw = calls[0]
    assert argv == ["/usr/bin/python3", str(sc.SLEEP_CTL_PATH), cmd]
    assert kw["timeout"] == sc.SLEEP_CTL_TIMEOUT_S


def test_sleep_ctl_nonzero_is_failure_and_loud(monkeypatch, caplog):
    _patch_run(monkeypatch, _Done(1, "", "boom"))
    assert REAL_CALL_SLEEP_CTL("wake_windows") is None
    assert "sleep_ctl wake failed" in caplog.text


def test_sleep_ctl_timeout_is_failure_and_loud(monkeypatch, caplog):
    _patch_run(monkeypatch, sc.subprocess.TimeoutExpired("x", 30))
    assert REAL_CALL_SLEEP_CTL("wake_windows") is None
    assert "timed out" in caplog.text


def test_sleep_ctl_bad_json_is_failure(monkeypatch):
    _patch_run(monkeypatch, _Done(0, "not json"))
    assert REAL_CALL_SLEEP_CTL("list_windows") is None


def test_sleep_ctl_absent_is_tolerated(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "SLEEP_CTL_PATH", tmp_path / "missing_sleep_ctl.py")
    assert REAL_CALL_SLEEP_CTL("sleep_windows") is None


def test_controller_never_registers_dbus_in_process():
    src = Path(sc.__file__).read_text()
    assert "import dbus" not in src and "exec_module" not in src
    assert not hasattr(sc, "_sleep_ctl")


def test_failed_wake_retries_via_retry_restore(env, monkeypatch, tmp_path):
    win_state = tmp_path / "sleep_windows.json"
    win_state.write_text('{"windows": []}')
    monkeypatch.setattr(sc, "windows_state_path", lambda: win_state)
    monkeypatch.setattr(sc, "_call_sleep_ctl", REAL_CALL_SLEEP_CTL)
    calls = _patch_run(monkeypatch, sc.subprocess.TimeoutExpired("x", 30))
    sc._write_state("awake", 1.0, env["home"])
    clock = FakeClock(100.0)
    ctl = sc.SleepController(env["home"], clock=clock)
    assert ctl.retry_restore() is False
    clock.t = 101.0
    assert ctl.retry_restore() is False  # throttled: no second subprocess
    assert len(calls) == 1
    clock.t = 106.0
    assert ctl.retry_restore() is False
    assert len(calls) == 2 and win_state.exists()


def test_sleep_ctl_calls_are_serialised(monkeypatch):
    active = {"n": 0, "max": 0}

    def fake_run(argv, **kw):
        active["n"] += 1
        active["max"] = max(active["max"], active["n"])
        time.sleep(0.05)
        active["n"] -= 1
        return _Done(0, '{"status": "ok"}')

    monkeypatch.setattr(sc.subprocess, "run", fake_run)
    ts = [
        threading.Thread(target=REAL_CALL_SLEEP_CTL, args=(a,))
        for a in ("wake_windows", "list_windows")
    ]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert active["max"] == 1


def test_verify_restore_reruns_wake_when_window_still_minimised(
    env, monkeypatch, tmp_path
):
    win_state = tmp_path / "sleep_windows.json"  # gone after a clean wake
    monkeypatch.setattr(sc, "windows_state_path", lambda: win_state)
    saved = {
        "active": "a",
        "windows": [{"id": "a"}, {"id": "b"}, {"id": "c", "minimized": True}],
    }
    ops: list = []

    def fake(attr, **kw):
        ops.append(attr)
        if attr == "list_windows":
            return {
                "status": "ok",
                "windows": [
                    {"id": "a", "minimized": False},
                    {"id": "b", "minimized": True},
                    {"id": "c", "minimized": True},
                ],
            }
        assert json.loads(win_state.read_text())["windows"] == [{"id": "b"}]
        return {"status": "ok"}

    monkeypatch.setattr(sc, "_call_sleep_ctl", fake)
    sc._verify_restore(saved)
    assert ops == ["list_windows", "wake_windows"]


def test_verify_restore_noop_when_all_restored(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "windows_state_path", lambda: tmp_path / "s.json")
    ops: list = []
    monkeypatch.setattr(
        sc,
        "_call_sleep_ctl",
        lambda attr, **kw: (
            ops.append(attr)
            or {"status": "ok", "windows": [{"id": "a", "minimized": False}]}
        ),
    )
    sc._verify_restore({"windows": [{"id": "a"}]})
    assert ops == ["list_windows"]


def _kwin_candidates(windows):
    """Run sleep-kwin.js's candidate() under node against fake window objects."""
    import shutil
    import subprocess as sp

    node = shutil.which("node")
    if node is None:
        pytest.skip("node missing")
    js = (sc.REPO_ROOT / "desktop/kwin/jarvis-sleep/sleep-kwin.js").read_text()
    head = js.split("function record")[0]
    prog = (
        "var runPids=null;"
        + head.replace("'use strict';", "")
        + f"console.log(JSON.stringify({json.dumps(windows)}.map(candidate)));"
    )
    prog = prog.replace("function call(", "function call_unused(", 1)
    out = sp.run([node, "-e", prog], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_snapshot_excludes_jarvis_windows_and_saver():
    base = {"normalWindow": True, "deleted": False, "pid": 1}
    wins = [
        {**base, "caption": "Firefox", "resourceClass": "firefox"},
        {**base, "caption": "Jarvis", "resourceClass": "jarvis-shell"},
        {**base, "caption": "x", "resourceClass": "x", "desktopFileName": "jarvis"},
        {**base, "caption": "Jarvis HUD", "resourceClass": "x"},
        {**base, "caption": "hologram overlay", "resourceClass": "x"},
        {**base, "caption": "JARVIS-SLEEP", "resourceClass": "x"},
        {**base, "caption": "notes about jarvis_new", "resourceClass": "konsole"},
        {"normalWindow": False, "caption": "Jarvis", "resourceClass": "jarvis-shell"},
    ]
    assert _kwin_candidates(wins) == [
        True,
        False,
        False,
        False,
        False,
        False,
        True,
        False,
    ]


def test_import_optional_falls_back_and_stays_tolerant():
    with pytest.raises(ImportError):
        sc._import_optional("no_such_module_for_sleep_tests")
    assert sc._import_optional("input_idle") is not None


# ------------------------------------------------------------- controller


def _signals(clock, *, voice_until=-1.0, audio_until=-1.0, input_last=1000.0):
    """Input idle since `input_last`; voice/audio active until a given time."""

    def fake():
        now = clock()
        return {
            "input_idle_s": max(0.0, now - input_last),
            "voice_active": now <= voice_until,
            "audio_playing": now <= audio_until,
            "fullscreen": False,
        }

    return fake


def test_sleeps_after_twenty_minutes_of_no_activity(env):
    clock = FakeClock(1000.0)
    sleeps: list = []
    ctl = sc.SleepController(env["home"], clock=clock, signals=_signals(clock))
    original = sc.go_to_sleep
    sc.go_to_sleep = lambda home=None, now=None: sleeps.append(now) or True
    try:
        assert ctl.tick() == "awake"  # idle 0 at t=1000
        clock.t = 2199.0
        assert ctl.tick() == "awake"  # idle 1199
        assert sleeps == []
        clock.t = 2200.0
        assert ctl.tick() == "asleep"  # idle 1200
        assert sleeps == [2200.0]
    finally:
        sc.go_to_sleep = original


def test_voice_call_resets_the_timer(env):
    clock = FakeClock(1000.0)
    ctl = sc.SleepController(
        env["home"], clock=clock, signals=_signals(clock, voice_until=3000.0)
    )
    clock.t = 3000.0
    assert ctl.tick() == "awake"  # voice active now: last activity 3000
    clock.t = 4199.0
    assert ctl.tick() == "awake"
    clock.t = 4200.0
    assert ctl.tick() == "asleep"


def test_audio_playing_resets_the_timer(env):
    clock = FakeClock(1000.0)
    ctl = sc.SleepController(
        env["home"], clock=clock, signals=_signals(clock, audio_until=2500.0)
    )
    clock.t = 2500.0
    assert ctl.tick() == "awake"
    clock.t = 3699.0
    assert ctl.tick() == "awake"
    clock.t = 3700.0
    assert ctl.tick() == "asleep"


def test_unknown_input_never_sleeps(env):
    clock = FakeClock(1000.0)
    ctl = sc.SleepController(
        env["home"],
        clock=clock,
        signals=lambda: {
            "input_idle_s": None,
            "voice_active": False,
            "audio_playing": False,
            "fullscreen": False,
        },
    )
    clock.t = 10_000.0
    assert ctl.tick() == "awake"


def test_input_while_asleep_does_not_wake(env):
    clock = FakeClock(1000.0)
    ctl = sc.SleepController(env["home"], clock=clock, signals=_signals(clock))
    assert sc.go_to_sleep(env["home"], now=1000.0) is True
    clock.t = 1005.0  # key press: input idle 0
    assert ctl.tick() == "asleep"
    assert ctl.poll_wake() is False
    assert sc.read_state(env["home"])["state"] == "asleep"
    assert ("sleep_ctl.py", "wake_windows") not in env["events"]


def test_wake_resets_idle_from_the_wake_time(env):
    clock = FakeClock(1000.0)
    ctl = sc.SleepController(env["home"], clock=clock, signals=_signals(clock))
    sc.go_to_sleep(env["home"], now=1000.0)
    clock.t = 5000.0  # long asleep; old input is ancient
    assert sc.request_wake(env["home"], now=5000.0) is True
    assert ctl.tick() == "awake"  # counts idle from the wake, not from input
    clock.t = 5000.0 + 1199.0
    assert ctl.tick() == "awake"
    clock.t = 5000.0 + 1200.0
    assert ctl.tick() == "asleep"


# ------------------------------------------------------------- transitions


def test_go_to_sleep_writes_state_and_runs_side_effects(env):
    home = env["home"]
    assert sc.go_to_sleep(home, now=1234.5) is True
    assert sc.read_state(home) == {"state": "asleep", "since": 1234.5}
    assert ("away_log", "start_away") in env["events"]
    assert ("sleep_ctl.py", "sleep_windows") in env["events"]
    assert ("spawn", ()) in env["events"]
    assert (home / "sleep_screensaver.pid").read_text() == "4242"


def test_go_to_sleep_is_idempotent(env):
    home = env["home"]
    sc.go_to_sleep(home, now=1.0)
    count = len(env["events"])
    assert sc.go_to_sleep(home, now=2.0) is False
    assert len(env["events"]) == count
    assert sc.read_state(home)["since"] == 1.0


def test_go_to_sleep_clears_a_stale_wake_request(env):
    home = env["home"]
    home.mkdir(parents=True)
    (home / "wake_request").touch()
    sc.go_to_sleep(home, now=1.0)
    assert not (home / "wake_request").exists()
    assert sc.SleepController(home).poll_wake() is False


def test_request_wake_idempotent_and_ordered(env):
    home = env["home"]
    assert sc.request_wake(home) is False  # awake: nothing to do
    assert env["events"] == []
    assert not (home / "wake_request").exists()  # and no stray request file

    sc.go_to_sleep(home, now=1.0)
    env["events"].clear()
    assert sc.request_wake(home) is True
    assert sc.read_state(home)["state"] == "awake"
    # The running saver plays its own wake clip (no kill, no second window first).
    assert env["events"][:1] == [("kill", 4242, signal.SIGUSR1)]
    assert "wake_anim" not in env["events"]
    sc._last_wake_windows.join()  # windows restore on a thread
    assert ("sleep_ctl.py", "wake_windows") in env["events"]
    assert ("away_log", "end_away") in env["events"]
    assert env["events"].index("wake_sequence") > env["events"].index(
        ("away_log", "end_away")
    )
    env["events"].clear()
    assert sc.request_wake(home) is False
    assert env["events"] == []


def test_wake_without_live_saver_spawns_the_wake_clip(env, monkeypatch):
    home = env["home"]
    sc.go_to_sleep(home, now=1.0)
    monkeypatch.setattr(sc, "_pid_alive", lambda pid: False)
    env["events"].clear()
    assert sc.request_wake(home) is True
    assert "wake_anim" in env["events"]
    assert not any(
        e[:2] == ("kill", 4242) for e in env["events"] if isinstance(e, tuple)
    )
    sc._last_wake_windows.join()


def test_wake_windows_start_at_the_fade_not_after_the_clip(env, monkeypatch):
    seen = {}
    monkeypatch.setattr(sc, "WAKE_WINDOWS_DELAY_S", 0.3)
    monkeypatch.setattr(
        sc, "_call_sleep_ctl", lambda attr: seen.setdefault(attr, time.monotonic())
    )
    sc.go_to_sleep(env["home"], now=1.0)
    t0 = time.monotonic()
    assert sc.request_wake(env["home"]) is True
    sc._last_wake_windows.join()
    assert seen["wake_windows"] - t0 >= 0.3


def test_wake_sequence_flag_file_skips_the_run(tmp_path, monkeypatch):
    home = tmp_path / "h"
    home.mkdir()
    calls = []
    monkeypatch.setattr(sc, "_call_module", lambda *a: calls.append(a))
    assert sc.wake_sequence_enabled(home) is True
    (home / "no_wake_sequence").touch()
    assert sc.wake_sequence_enabled(home) is False
    sc._start_wake_sequence(home)
    assert calls == []


def test_wake_sequence_runs_as_venv_subprocess(tmp_path, monkeypatch):
    home = tmp_path / "h"
    home.mkdir()
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(sc.subprocess, "run", fake_run)
    assert sc._run_wake_subprocess(home) == 0
    assert seen["cmd"][1:] == ["-m", "src.wake_sequence"]
    assert seen["kw"]["cwd"] == str(sc._repo_root())
    assert seen["kw"]["timeout"] == sc.WAKE_SEQUENCE_TIMEOUT_S
    assert (home / "logs" / "wake_sequence.log").exists()


def test_wake_sequence_double_run_is_guarded(tmp_path, monkeypatch):
    home = tmp_path / "h"
    home.mkdir()
    calls = []
    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: calls.append(a))
    held = os.open(str(sc._wake_lock_path(home)), os.O_CREAT | os.O_RDWR)
    fcntl.flock(held, fcntl.LOCK_EX)
    try:
        assert sc._run_wake_subprocess(home) is None
    finally:
        os.close(held)
    assert calls == []


def test_wake_sequence_timeout_is_not_fatal(tmp_path, monkeypatch):
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(sc.subprocess, "run", boom)
    assert sc._run_wake_subprocess(tmp_path) == -1


def test_wake_flag_file_skips_subprocess(tmp_path, monkeypatch):
    (tmp_path / "no_wake_sequence").touch()
    calls = []
    monkeypatch.setattr(sc, "_run_wake_subprocess", lambda *a: calls.append(a))
    sc._start_wake_sequence(tmp_path)
    assert calls == []


def test_request_wake_from_other_process_is_picked_up_by_poll(env):
    home = env["home"]
    ctl = sc.SleepController(home, clock=FakeClock(50.0))
    sc.go_to_sleep(home, now=1.0)
    env["events"].clear()

    # Clap detector in another process: it only touches the file.
    (home / "wake_request").touch()
    assert ctl.poll_wake() is True
    assert sc.read_state(home)["state"] == "awake"
    assert "wake_sequence" in env["events"]
    assert not (home / "wake_request").exists()


def test_request_wake_while_lock_busy_only_leaves_the_file(env):
    home = env["home"]
    sc.go_to_sleep(home, now=1.0)
    env["events"].clear()

    assert sc._thread_lock.acquire(blocking=False)
    try:
        assert sc.request_wake(home) is False
    finally:
        sc._thread_lock.release()
    assert (home / "wake_request").exists()
    assert sc.read_state(home)["state"] == "asleep"
    assert env["events"] == []

    assert sc.SleepController(home, clock=FakeClock(2.0)).poll_wake() is True


def test_state_file_is_atomic_and_leaves_no_temp_files(env):
    home = env["home"]
    sc.go_to_sleep(home, now=1.0)
    sc.request_wake(home)
    names = sorted(p.name for p in home.iterdir())
    assert "sleep_state.json" in names
    assert not [n for n in names if n.startswith(".sleep_state.json")]
    assert json.loads((home / "sleep_state.json").read_text())["state"] == "awake"


class _OneLoop:
    """Stop event that lets the run loop go round exactly once."""

    def __init__(self) -> None:
        self.checks = 0

    def is_set(self) -> bool:
        self.checks += 1
        return self.checks > 1

    def wait(self, timeout=None) -> None:
        pass


def test_run_loop_writes_the_state_file_on_sleep(env):
    home = env["home"]
    clock = FakeClock(2300.0)
    timer = sc.IdleTimer(threshold=1200)
    timer.reset(1000.0)  # last activity 1300s ago: past the threshold
    ctl = sc.SleepController(
        home, clock=clock, timer=timer, signals=_signals(clock, input_last=1000.0)
    )
    ctl.run(_OneLoop(), poll_s=0, tick_s=0)
    assert sc.read_state(home) == {"state": "asleep", "since": 2300.0}
    assert json.loads((home / "sleep_state.json").read_text())["state"] == "asleep"
    assert ("sleep_ctl.py", "sleep_windows") in env["events"]


def test_run_loop_survives_failing_signals_and_stops(env):
    stop = threading.Event()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("probe blew up")
        if calls["n"] == 3:
            stop.set()
        return {
            "input_idle_s": 0.0,
            "voice_active": False,
            "audio_playing": False,
            "fullscreen": False,
        }

    ctl = sc.SleepController(env["home"], clock=FakeClock(10.0), signals=flaky)
    ctl.run(stop, poll_s=0, tick_s=0)
    assert calls["n"] == 3


# ------------------------------------------------------------- collaborators


def test_missing_optional_modules_are_tolerated():
    assert sc._call_module("no_such_module_for_sleep_tests", "f") is None


def test_stop_screensaver_terminates_the_recorded_pid(env):
    home = env["home"]
    home.mkdir(parents=True)
    (home / "sleep_screensaver.pid").write_text("777")
    sc._stop_screensaver(home)
    assert ("kill", 777, signal.SIGTERM) in env["events"]
    assert not (home / "sleep_screensaver.pid").exists()


def test_read_state_defaults_to_awake(tmp_path):
    home = tmp_path / "h"
    assert sc.read_state(home) == {"state": "awake", "since": None}
    home.mkdir()
    (home / "sleep_state.json").write_text("{not json")
    assert sc.read_state(home)["state"] == "awake"
    (home / "sleep_state.json").write_text('{"state": "dozing"}')
    assert sc.read_state(home)["state"] == "awake"


# ------------------------------------------------------------------- CLI


def test_status_is_read_only(monkeypatch, tmp_path):
    home = tmp_path / "never_created"
    monkeypatch.setenv("JARVIS_HOME", str(home))
    monkeypatch.setattr(sc, "_audio_playing", lambda: False)
    monkeypatch.setattr(sc, "_voice_active", lambda: False)
    monkeypatch.setattr(sc, "_input_idle_s", lambda: None)
    report = sc.status()
    assert report["state"] == "awake"
    assert report["idle_threshold_s"] == 1200
    assert report["screensaver_running"] is False
    assert not home.exists()


def test_cli_status_prints_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    monkeypatch.setattr(sc, "_audio_playing", lambda: False)
    monkeypatch.setattr(sc, "_voice_active", lambda: False)
    assert sc.main(["status"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "awake"
    assert set(out["signals"]) == {
        "input_idle_s",
        "voice_active",
        "audio_playing",
        "fullscreen",
    }


def test_jarvis_home_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert sc.jarvis_home() == Path(tmp_path)


def test_screensaver_defaults_to_haiku(monkeypatch):
    monkeypatch.delenv("JARVIS_SCREENSAVER_MODULE", raising=False)
    assert sc.screensaver_module() == "src.sleep_screensaver"


def test_screensaver_module_env_swaps_build(monkeypatch):
    monkeypatch.setenv("JARVIS_SCREENSAVER_MODULE", "src.sleep_screensaver_sonnet")
    assert sc.screensaver_module() == "src.sleep_screensaver_sonnet"
    monkeypatch.setenv("JARVIS_SCREENSAVER_MODULE", "  ")
    assert sc.screensaver_module() == "src.sleep_screensaver"


def test_spawn_uses_screensaver_module_and_args(monkeypatch):
    seen: dict = {}

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        return FakeProc()

    monkeypatch.delenv("JARVIS_SCREENSAVER_MODULE", raising=False)
    monkeypatch.setattr(sc.subprocess, "Popen", fake_popen)
    assert REAL_SPAWN(["--wake-anim"]) is not None
    assert seen["cmd"][1:] == ["-m", "src.sleep_screensaver", "--wake-anim"]


# ------------------------------------------- clap wake: file only, restore retry


def test_touch_wake_request_only_writes_the_file_while_asleep(env):
    home = env["home"]
    assert sc.touch_wake_request(home) is False  # awake: nothing written
    assert not (home / "wake_request").exists()
    sc.go_to_sleep(home, now=1.0)
    env["events"].clear()
    assert sc.touch_wake_request(home) is True
    assert (home / "wake_request").exists()
    assert sc.read_state(home)["state"] == "asleep"  # no wake in this call
    assert env["events"] == []  # no sleep_ctl, screensaver or away_log call


def test_restore_failure_keeps_state_and_retries_until_restored(
    env, monkeypatch, tmp_path
):
    home = env["home"]
    win_state = tmp_path / "sleep_windows.json"
    win_state.write_text('{"windows": []}')
    monkeypatch.setattr(sc, "windows_state_path", lambda: win_state)
    sc._write_state("awake", 1.0, home)
    outcomes = iter([{"status": "error"}, {"status": "ok"}])
    calls: list = []

    def fake_wake_windows(attr):
        calls.append(attr)
        out = next(outcomes)
        if out["status"] == "ok":
            win_state.unlink()
        return out

    monkeypatch.setattr(sc, "_call_sleep_ctl", fake_wake_windows)
    clock = FakeClock(100.0)
    ctl = sc.SleepController(home, clock=clock)
    assert ctl.retry_restore() is False  # first retry fails: windows not restored
    assert win_state.exists()  # state kept, so the next retry can try again
    clock.t = 101.0
    assert ctl.retry_restore() is False  # throttled
    clock.t = 106.0
    assert ctl.retry_restore() is True
    assert calls == ["wake_windows", "wake_windows"]
    clock.t = 200.0
    assert ctl.retry_restore() is False  # nothing pending any more
    assert calls == ["wake_windows", "wake_windows"]


def test_retry_restore_does_nothing_while_asleep(env, monkeypatch, tmp_path):
    home = env["home"]
    win_state = tmp_path / "sleep_windows.json"
    win_state.write_text('{"windows": []}')
    monkeypatch.setattr(sc, "windows_state_path", lambda: win_state)
    sc.go_to_sleep(home, now=1.0)
    env["events"].clear()
    assert sc.SleepController(home, clock=FakeClock(50.0)).retry_restore() is False
    assert ("sleep_ctl.py", "wake_windows") not in env["events"]


# ------------------------------------------- sleep_ctl: D-Bus object path reuse


class _FakeBus:
    """One shared session connection, like dbus.SessionBus() returns in one process."""

    def __init__(self) -> None:
        self.paths: dict = {}
        self.owners: set = set()

    def name_has_owner(self, name) -> bool:
        return name in self.owners

    def release_name(self, name) -> None:
        self.owners.discard(name)


def test_repeated_dbus_runs_in_one_process_reuse_the_object_path(monkeypatch):
    """The fullscreen probe runs list_windows every 30 s; each run must free /Sleep."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("sleep_ctl_t", sc.SLEEP_CTL_PATH)
    ctl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ctl)
    bus = _FakeBus()

    class FakeObject:
        def __init__(self, name, path):
            if path in bus.paths:
                raise RuntimeError(
                    f"Can't register the object-path handler for {path}: there is already a handler"
                )
            self._path = path
            bus.paths[path] = self

        def remove_from_connection(self, path=None, connection=None):
            bus.paths.pop(self._path, None)

    class FakeBusName:
        def __init__(self, name, bus, do_not_queue=False):
            if name in bus.owners:
                raise RuntimeError("name already owned")
            bus.owners.add(name)

    class FakeLoop:
        def run(self):
            pass

        def quit(self):
            pass

    dbus = types.ModuleType("dbus")
    dbus.SessionBus = lambda: bus
    dbus_service = types.ModuleType("dbus.service")
    dbus_service.Object = FakeObject
    dbus_service.BusName = FakeBusName
    dbus_service.method = lambda *a, **k: lambda fn: fn
    dbus.service = dbus_service
    dbus_glib = types.ModuleType("dbus.mainloop.glib")
    dbus_glib.DBusGMainLoop = lambda **k: None
    gi_repo = types.ModuleType("gi.repository")
    gi_repo.GLib = types.SimpleNamespace(
        MainLoop=FakeLoop, timeout_add_seconds=lambda *a: None
    )
    for name, mod in {
        "dbus": dbus,
        "dbus.service": dbus_service,
        "dbus.mainloop": types.ModuleType("dbus.mainloop"),
        "dbus.mainloop.glib": dbus_glib,
        "gi": types.ModuleType("gi"),
        "gi.repository": gi_repo,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    fake_loader = types.SimpleNamespace(load=lambda *a: None, unload=lambda *a: None)
    monkeypatch.setattr(ctl, "kwin_bridge_loader", fake_loader)

    first = ctl.list_windows()
    second = ctl.list_windows()  # used to raise "already a handler"
    assert isinstance(first, dict) and isinstance(second, dict)
    assert bus.paths == {}
    assert bus.owners == set()
