"""Tests for watch mode helpers (src/watch_mode.py). Hermetic: throwaway
$JARVIS_HOME, fake writers/popen/run, never the real camera or screen."""

import os
import types

import numpy as np
import pytest

import watch_mode


@pytest.fixture()
def home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis"))


# --- parse_command ----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "i'm leaving",
        "im leaving now",
        "i am heading out",
        "watch the laptop",
        "watch my computer",
        "keep an eye on the laptop",
        "guard the laptop please",
        "watch mode",
        "start watch mode",
        "could you watch the laptop",
        "I'm leaving.",
    ],
)
def test_parse_command_starts(text: str) -> None:
    assert watch_mode.parse_command(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "i'm leaving for school at eight remind me",
        "watch a video",
        "what is the weather",
        "stop watch mode",
        "",
    ],
)
def test_parse_command_ignores(text: str) -> None:
    assert watch_mode.parse_command(text) is False


# --- is_stop_combo ----------------------------------------------------------

ALL = 0x4 | 0x1 | 0x8


@pytest.mark.parametrize("keysym", ["W", "w"])
def test_stop_combo_state_bits(keysym: str) -> None:
    assert watch_mode.is_stop_combo(keysym, ALL) is True


def test_stop_combo_held_keys() -> None:
    assert (
        watch_mode.is_stop_combo("W", 0, held={"Control_L", "Shift_R", "Alt_L"}) is True
    )


@pytest.mark.parametrize(
    "state",
    [0x4 | 0x1, 0x4 | 0x8, 0x1 | 0x8, 0x0],
)
def test_stop_combo_missing_state_modifier(state: int) -> None:
    assert watch_mode.is_stop_combo("W", state) is False


@pytest.mark.parametrize(
    "held",
    [
        {"Shift_R", "Alt_L"},  # no ctrl
        {"Control_L", "Alt_L"},  # no shift
        {"Control_L", "Shift_R"},  # no alt
        set(),
    ],
)
def test_stop_combo_missing_held_modifier(held: set) -> None:
    assert watch_mode.is_stop_combo("W", 0, held=set(held)) is False


def test_stop_combo_wrong_keysym() -> None:
    assert watch_mode.is_stop_combo("q", ALL) is False
    assert (
        watch_mode.is_stop_combo("q", 0, held={"Control_L", "Shift_R", "Alt_L"})
        is False
    )
    assert watch_mode.is_stop_combo("Escape", ALL) is False


# --- _mix -------------------------------------------------------------------


def test_mix_endpoints_and_midpoint() -> None:
    assert watch_mode._mix("#000000", "#ffffff", 0) == "#000000"
    assert watch_mode._mix("#000000", "#ffffff", 1) == "#ffffff"
    assert watch_mode._mix("#000000", "#ffffff", 0.5) == "#808080"


def test_mix_clamps_k() -> None:
    assert watch_mode._mix("#000000", "#ffffff", -1) == "#000000"
    assert watch_mode._mix("#000000", "#ffffff", 2) == "#ffffff"


# --- MotionDetector ---------------------------------------------------------


def _still():
    return np.zeros((720, 1280, 3), dtype=np.uint8)


def test_motion_detector_still_then_motion() -> None:
    det = watch_mode.MotionDetector()
    still = _still()
    assert not det.update(still)  # first frame: background learn
    for _ in range(3):
        assert not det.update(still)  # identical frames: no motion
    moving = still.copy()
    moving[100:500, 100:500] = 255  # big white rectangle
    assert det.update(moving)


# --- ClipRecorder -----------------------------------------------------------


class _FakeWriter:
    def __init__(self, path, fps, size) -> None:
        self.path = path
        self.fps = fps
        self.size = size
        self.frames = []
        self.released = False

    def write(self, frame) -> None:
        self.frames.append(frame)

    def release(self) -> None:
        self.released = True


def _factory(writers: list):
    def make(path, fps, size):
        w = _FakeWriter(path, fps, size)
        writers.append(w)
        return w

    return make


def _frame():
    return np.zeros((4, 4, 3), dtype=np.uint8)


def test_clip_recorder_preroll_start_then_stop(tmp_path) -> None:
    writers: list = []
    rec = watch_mode.ClipRecorder(
        folder=tmp_path / "clips",
        make_writer=_factory(writers),
        stamp=lambda f, t: f,
        pre_roll_s=0.25,
        post_roll_s=0.3,
        max_clip_s=120.0,
    )
    f = _frame()
    for i in range(5):  # t = 0, 0.1, ... 0.4: still, buffered only
        assert rec.push(f, False, i * 0.1) is None
    assert not rec.recording
    assert writers == []
    assert rec.push(f, True, 0.5) == "start"  # first motion
    assert rec.events == 1
    assert rec.recording
    # Only buffered frames within pre_roll_s (t >= 0.25) plus current.
    assert len(writers[0].frames) == 3
    assert writers[0].size == (4, 4)
    # Post-roll: still frames keep it open until post_roll_s passes.
    assert rec.push(f, False, 0.6) is None
    assert rec.push(f, False, 0.7) is None
    # Note: 0.8 - 0.5 is 0.30000000000000004 in float, so the 0.3s
    # post-roll already trips here (strict > in ClipRecorder.push).
    assert rec.push(f, False, 0.8) == "stop"
    assert not rec.recording
    assert len(rec.clips) == 1
    assert rec.clips[0].name == "clip_0000.mp4"
    assert writers[0].released


def test_clip_recorder_second_burst(tmp_path) -> None:
    writers: list = []
    rec = watch_mode.ClipRecorder(
        folder=tmp_path / "clips",
        make_writer=_factory(writers),
        stamp=lambda f, t: f,
        pre_roll_s=0.25,
        post_roll_s=0.3,
        max_clip_s=120.0,
    )
    f = _frame()
    assert rec.push(f, True, 1.0) == "start"
    assert rec.events == 1
    t = 1.1
    while rec.recording and t < 5.0:
        rec.push(f, False, t)
        t = round(t + 0.1, 10)
    assert len(rec.clips) == 1
    # A later, separate burst counts as a second event and clip.
    assert rec.push(f, True, 3.0) == "start"
    assert rec.events == 2
    assert rec.clips and writers[1].path.name == "clip_0001.mp4"
    t = 3.1
    while rec.recording and t < 8.0:
        rec.push(f, False, t)
        t = round(t + 0.1, 10)
    assert [c.name for c in rec.clips] == ["clip_0000.mp4", "clip_0001.mp4"]


def test_clip_recorder_max_clip_cut_keeps_one_event(tmp_path) -> None:
    writers: list = []
    rec = watch_mode.ClipRecorder(
        folder=tmp_path / "clips",
        make_writer=_factory(writers),
        stamp=lambda f, t: f,
        pre_roll_s=0.1,
        post_roll_s=10.0,
        max_clip_s=1.0,
    )
    f = _frame()
    assert rec.push(f, True, 10.0) == "start"
    assert rec.events == 1
    t = 10.1
    stopped = None
    while t < 15.0:
        stopped = rec.push(f, True, round(t, 10))
        if stopped == "stop":
            break
        t = round(t + 0.1, 10)
    assert stopped == "stop"  # cut by max_clip_s, not post-roll
    assert len(rec.clips) == 1
    assert rec.events == 1
    # Continuing motion opens the next clip as the same event.
    assert rec.push(f, True, round(t + 0.1, 10)) == "start"
    assert rec.events == 1
    clips = rec.finish()
    assert len(clips) == 2
    assert [c.name for c in clips] == ["clip_0000.mp4", "clip_0001.mp4"]
    assert all(w.released for w in writers)


def test_clip_recorder_finish(tmp_path) -> None:
    writers: list = []
    rec = watch_mode.ClipRecorder(
        folder=tmp_path / "clips",
        make_writer=_factory(writers),
        stamp=lambda f, t: f,
        pre_roll_s=0.1,
        post_roll_s=10.0,
    )
    assert rec.finish() == []  # nothing open: no-op
    assert rec.push(_frame(), True, 5.0) == "start"
    assert rec.recording
    clips = rec.finish()
    assert len(clips) == 1
    assert not rec.recording
    assert writers[0].released


# --- stitch -----------------------------------------------------------------


def test_stitch_empty_or_missing_never_runs(tmp_path) -> None:
    def boom(argv, **kw):
        raise AssertionError("run must not be called")

    assert watch_mode.stitch([], tmp_path / "out.mp4", run=boom) is False
    assert (
        watch_mode.stitch([tmp_path / "nope.mp4"], tmp_path / "out.mp4", run=boom)
        is False
    )
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    assert watch_mode.stitch([empty], tmp_path / "out.mp4", run=boom) is False


def test_stitch_success(tmp_path) -> None:
    c1 = tmp_path / "a.mp4"
    c2 = tmp_path / "b.mp4"
    c1.write_bytes(b"111")
    c2.write_bytes(b"222")
    out = tmp_path / "watch.mp4"
    captured: dict = {}

    def fake_run(argv, **kw):
        captured["argv"] = list(argv)
        out.write_bytes(b"joined")
        return types.SimpleNamespace(returncode=0)

    assert watch_mode.stitch([c1, c2], out, run=fake_run) is True
    argv = captured["argv"]
    assert "ffmpeg" in argv[0]
    assert "-f" in argv and "concat" in argv and "libx264" in argv
    assert not out.with_suffix(".txt").exists()
    assert out.stat().st_size > 0


def test_stitch_failure(tmp_path) -> None:
    c1 = tmp_path / "a.mp4"
    c1.write_bytes(b"111")
    out = tmp_path / "watch.mp4"

    def fake_run(argv, **kw):
        out.write_bytes(b"partial")
        return types.SimpleNamespace(returncode=1)

    assert watch_mode.stitch([c1], out, run=fake_run) is False


# --- launch -----------------------------------------------------------------


def test_launch_starts_detached(home, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_popen(argv, **kw):
        captured["argv"] = list(argv)
        captured["kw"] = kw
        return types.SimpleNamespace(pid=1234)

    res = watch_mode.launch(popen=fake_popen)
    assert res == {"ok": True, "state": "watching"}
    assert captured["argv"][-1].endswith("watch_mode.py")
    assert captured["kw"].get("start_new_session") is True


def test_launch_already_watching(home, monkeypatch: pytest.MonkeyPatch) -> None:
    watch_mode.state_dir().mkdir(parents=True, exist_ok=True)
    watch_mode.pid_path().write_text(str(os.getpid()))
    monkeypatch.setattr(watch_mode, "_pid_alive", lambda pid: True)

    def boom(*args, **kwargs):
        raise AssertionError("popen must not be called")

    assert watch_mode.launch(popen=boom) == {"ok": True, "state": "already watching"}


def test_parse_monitors_reads_every_output():
    text = (
        "Monitors: 3\n"
        " 0: +*HDMI-A-2 1920/544x1080/303+0+0  HDMI-A-2\n"
        " 1: +eDP-1 1920/344x1080/194+945+1080  eDP-1\n"
        " 2: +DP-1 1920/544x1080/303+1920+0  DP-1\n"
    )
    assert watch_mode.parse_monitors(text) == [
        (0, 0, 1920, 1080),
        (945, 1080, 1920, 1080),
        (1920, 0, 1920, 1080),
    ]
    assert watch_mode.parse_monitors("") == []
    assert watch_mode.parse_monitors("garbage") == []


# --- welcome_line -----------------------------------------------------------


def test_welcome_line_nothing_moved(home) -> None:
    line = watch_mode.welcome_line({"events": 0, "video": None, "skipped": 0})
    assert "Nothing moved" in line


def test_welcome_line_once_with_video(home) -> None:
    line = watch_mode.welcome_line({"events": 1, "video": "/v.mp4", "skipped": 0})
    assert "moved once" in line
    assert "Videos" in line


def test_welcome_line_three_times(home) -> None:
    line = watch_mode.welcome_line({"events": 3, "video": "/v.mp4", "skipped": 0})
    assert "3 times" in line


def test_welcome_line_disk_too_full(home) -> None:
    line = watch_mode.welcome_line({"events": 2, "video": None, "skipped": 0})
    assert "disk was too full" in line


def test_welcome_line_later_footage_missing(home) -> None:
    line = watch_mode.welcome_line({"events": 2, "video": "/v.mp4", "skipped": 1})
    assert "later footage is missing" in line


# --- disk_ok ----------------------------------------------------------------


def test_disk_ok_thresholds(home, tmp_path) -> None:
    assert watch_mode.disk_ok(tmp_path, min_free=0) is True
    assert watch_mode.disk_ok(tmp_path, min_free=10**18) is False


def test_disk_ok_unknown_returns_true(
    home, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    def boom(path):
        raise OSError("no disk info")

    monkeypatch.setattr(shutil, "disk_usage", boom)
    assert watch_mode.disk_ok(tmp_path, min_free=0) is True


# --- ClipRecorder disk-full -------------------------------------------------


def test_clip_recorder_disk_full_counts_without_recording(home, tmp_path) -> None:
    writers: list = []
    rec = watch_mode.ClipRecorder(
        folder=tmp_path / "clips",
        make_writer=_factory(writers),
        stamp=lambda f, t: f,
        pre_roll_s=0.2,
        post_roll_s=1.0,
        max_clip_s=120.0,
        can_record=lambda: False,
    )
    f = _frame()
    # A moving frame counts the event but records nothing.
    assert rec.push(f, True, 10.0) is None
    assert rec.events == 1
    assert rec.skipped == 1
    assert writers == []
    assert rec.recording is False
    # A second moving frame within post_roll does not double count.
    assert rec.push(f, True, 10.5) is None
    assert rec.events == 1
    assert rec.skipped == 1
    assert writers == []
    assert rec.recording is False
    # Stillness, then a new motion after post_roll counts again.
    assert rec.push(f, False, 11.0) is None
    assert rec.push(f, False, 11.6) is None
    assert rec.push(f, True, 12.0) is None
    assert rec.events == 2
    assert rec.skipped == 2
    assert writers == []
    assert rec.recording is False


# --- breath -----------------------------------------------------------------


def test_breath_endpoints(home) -> None:
    assert watch_mode.breath(0) == pytest.approx(0)
    assert watch_mode.breath(1.4, 2.8) == pytest.approx(1)


def test_breath_stays_in_range(home) -> None:
    for i in range(200):
        t = i * 0.13
        v = watch_mode.breath(t)
        assert 0 <= v <= 1
        v2 = watch_mode.breath(t, 2.8)
        assert 0 <= v2 <= 1


# --- save_session -----------------------------------------------------------


def test_save_session_no_clips_removes_folder(home, tmp_path) -> None:
    import time

    folder = tmp_path / "s"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "keep.txt").write_text("x")
    watcher = types.SimpleNamespace(
        stop=lambda: [],
        recorder=types.SimpleNamespace(events=0, skipped=0),
        folder=folder,
        started=time.time(),
    )
    result = watch_mode.save_session(watcher)
    assert result == {"events": 0, "video": None, "skipped": 0}
    assert not folder.exists()


def test_save_session_with_clips_stitches_under_output_dir(
    home, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time
    from pathlib import Path

    folder = tmp_path / "s"
    folder.mkdir(parents=True, exist_ok=True)
    clip = folder / "clip_0000.mp4"
    clip.write_bytes(b"data")
    watcher = types.SimpleNamespace(
        stop=lambda: [clip],
        recorder=types.SimpleNamespace(events=2, skipped=0),
        folder=folder,
        started=time.time(),
    )
    captured: dict = {}

    def fake_stitch(clips, out):
        captured["out"] = Path(out)
        captured["clips"] = list(clips)
        return True

    monkeypatch.setattr(watch_mode, "stitch", fake_stitch)
    monkeypatch.setattr(watch_mode, "output_dir", lambda: tmp_path)
    result = watch_mode.save_session(watcher)
    assert result["events"] == 2
    assert result["skipped"] == 0
    assert result["video"] is not None
    assert Path(result["video"]).parent == tmp_path
    assert captured["out"].parent == tmp_path
    assert not folder.exists()


# --- finish_session ---------------------------------------------------------


def test_finish_session_announces_save_result(
    home, monkeypatch: pytest.MonkeyPatch
) -> None:
    watcher = types.SimpleNamespace()
    saved = {"events": 2, "video": "/v.mp4", "skipped": 0}
    seen: dict = {}
    monkeypatch.setattr(watch_mode, "save_session", lambda w: saved)
    monkeypatch.setattr(
        watch_mode, "announce", lambda r, wait_s=8.0: seen.update({"result": r})
    )
    out = watch_mode.finish_session(watcher)
    assert out == saved
    assert seen["result"] == saved
