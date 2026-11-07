"""Always Aware capture: HUD exclusion, scaling, window blackout, dedupe, burst."""

import shutil

import numpy as np
import pytest

from aware import capture as cap
from aware import skip

SCREENS = [
    {"name": "HDMI-A-2", "x": 0, "y": 0, "w": 1920, "h": 1080, "priority": 1},
    {"name": "eDP-1", "x": 1920, "y": 0, "w": 1920, "h": 1080, "priority": 2},
    {"name": "DP-1", "x": 3840, "y": 0, "w": 1920, "h": 1080, "priority": 3},
]


def test_capture_set_excludes_hud():
    names = [s["name"] for s in cap.capture_set(SCREENS, "DP-1")]
    assert names == ["HDMI-A-2", "eDP-1"]


def test_capture_set_with_unknown_hud_excludes_primary_fallback():
    # hud_screen() falls back to primary when the shell isn't found: keep that
    # (privacy-safe) exclusion, caller logs hud_unknown.
    names = [s["name"] for s in cap.capture_set(SCREENS, None)]
    assert names == ["eDP-1", "DP-1"]


def test_scale_for_matching_image():
    assert cap.scale_for((1080, 5760), SCREENS) == (1.0, 1.0)
    assert cap.scale_for((2160, 11520), SCREENS) == (2.0, 2.0)


def test_scale_for_mismatch_is_none():
    assert cap.scale_for((1080, 3840), SCREENS) is None  # monitor set changed
    assert cap.scale_for((1200, 5760), SCREENS) is None  # non-uniform


def test_crop_takes_only_that_monitor():
    img = np.zeros((1080, 5760, 3), np.uint8)
    img[:, 3840:] = 200  # HUD monitor bright
    img[:, 1920:3840] = 50
    crop = cap.crop(img, SCREENS[1], SCREENS, (1.0, 1.0))
    assert crop.shape == (1080, 1920, 3)
    assert int(crop.max()) == 50


def test_redaction_rects_sensitive_window_and_zones():
    wins = [
        {
            "app": "org.keepassxc.KeePassXC",
            "title": "db",
            "x": 2000,
            "y": 100,
            "w": 400,
            "h": 300,
            "minimized": False,
        },
        {
            "app": "org.kde.konsole",
            "title": "make",
            "x": 0,
            "y": 0,
            "w": 900,
            "h": 900,
            "minimized": False,
        },
        {
            "app": "Bitwarden",
            "title": "v",
            "x": 100,
            "y": 100,
            "w": 10,
            "h": 10,
            "minimized": True,
        },
    ]
    rects = cap.redaction_rects(wins, SCREENS[1], skip.Rules(), tabs=[])
    # keepass window translated to monitor-local coordinates
    assert (80, 100, 400, 300) in rects
    # minimised sensitive windows are not visible; zones always present
    assert any(r[1] + r[3] == 1080 and r[2] == 1920 for r in rects)  # panel strip
    assert all(r != (100, 100, 10, 10) for r in rects)
    # konsole not sensitive and not on this monitor
    rects0 = cap.redaction_rects(wins, SCREENS[0], skip.Rules(), tabs=[])
    assert all(r[2] != 900 for r in rects0)


def test_brave_window_with_denied_tab_is_redacted():
    wins = [
        {
            "app": "brave-browser",
            "title": "My Account - Brave",
            "x": 0,
            "y": 0,
            "w": 800,
            "h": 600,
            "minimized": False,
        },
    ]
    tabs = [
        {"type": "page", "title": "My Account", "url": "https://online.hsbc.co.uk/x"}
    ]
    rects = cap.redaction_rects(wins, SCREENS[0], skip.Rules(), tabs=tabs)
    assert (0, 0, 800, 600) in rects


def test_blackout_fills_rects_scaled():
    img = np.full((200, 400, 3), 255, np.uint8)
    out = cap.blackout(img, [(10, 10, 50, 20)], scale=(2.0, 2.0))
    assert int(out[20:60, 20:120].max()) == 0
    assert int(out[100, 300].min()) == 255


def test_dhash_similar_and_different():
    rng = np.random.default_rng(1)
    a = rng.integers(0, 255, (300, 400, 3), dtype=np.uint8)
    b = a.copy()
    b[0:2, 0:2] = 0
    c = rng.integers(0, 255, (300, 400, 3), dtype=np.uint8)
    assert cap.hamming(cap.dhash(a), cap.dhash(b)) <= 6
    assert cap.hamming(cap.dhash(a), cap.dhash(c)) > 6


def test_changed_monitor_dedupe():
    prev = {"eDP-1": {"hash": 0, "windows": ["a"]}}
    assert cap.monitor_changed("eDP-1", 0b111, ["a"], prev) is False
    assert cap.monitor_changed("eDP-1", 0b111, ["a", "b"], prev) is True
    assert cap.monitor_changed("eDP-1", (1 << 40) - 1, ["a"], prev) is True
    assert cap.monitor_changed("HDMI-A-2", 0, [], prev) is True


def test_long_edge():
    img = np.zeros((1080, 1920, 3), np.uint8)
    assert cap.downscale(img, 1280).shape[:2] == (720, 1280)
    assert cap.downscale(img, 4000).shape[:2] == (1080, 1920)


def test_ffmpeg_argv_is_niced_single_thread(tmp_path):
    argv = cap.ffmpeg_argv(tmp_path / "m0_%02d.jpg", tmp_path / "m0.mp4")
    assert argv[:4] == ["nice", "-n", "19", "ionice"] or argv[:3] == [
        "nice",
        "-n",
        "19",
    ]
    assert "-threads" in argv and argv[argv.index("-threads") + 1] == "1"
    assert "libx264" in argv and "-crf" in argv


def _frame(fill):
    img = np.zeros((1080, 5760, 3), np.uint8)
    img[:, :1920] = fill
    img[:, 1920:3840] = 30
    img[:, 3840:] = 250  # HUD content must never survive
    img[500:600, 2000:2400] = 255  # keepass window on eDP-1
    return img


def test_burst_excludes_hud_and_blacks_out(tmp_path):
    frames = iter([_frame(i * 20) for i in range(6)])
    wins = [
        {
            "app": "keepassxc",
            "title": "db",
            "x": 2000,
            "y": 500,
            "w": 400,
            "h": 100,
            "minimized": False,
            "id": "k",
        },
    ]
    burst = cap.burst(
        shoot=lambda: next(frames),
        screens=SCREENS,
        hud="DP-1",
        windows_fn=lambda: wins,
        tabs=[],
        active_monitor="HDMI-A-2",
        workdir=tmp_path,
        sleep=lambda s: None,
        rules=skip.Rules(),
    )
    assert burst.ok, burst.reason
    assert set(burst.monitors) == {"HDMI-A-2", "eDP-1"}
    m = burst.monitors["eDP-1"]
    assert len(m["frames"]) == 6
    import cv2

    img = cv2.imread(str(m["frames"][0]))
    # 1920 -> 960 long edge for the non-active monitor; keepass rect black
    assert img.shape[1] == 960
    assert int(img[260:290, 50:240].max()) < 15
    assert int(img.max()) < 80  # no HUD pixels (250) anywhere
    assert burst.monitors["HDMI-A-2"]["frames"]
    assert cv2.imread(str(burst.monitors["HDMI-A-2"]["frames"][0])).shape[1] == 1280


def test_burst_fails_closed_without_window_list(tmp_path):
    burst = cap.burst(
        shoot=lambda: _frame(0),
        screens=SCREENS,
        hud="DP-1",
        windows_fn=lambda: None,
        tabs=[],
        active_monitor="HDMI-A-2",
        workdir=tmp_path,
        sleep=lambda s: None,
        rules=skip.Rules(),
    )
    assert not burst.ok and burst.reason == "redaction_unavailable"
    assert not list(tmp_path.rglob("*.jpg"))


def test_burst_aborts_on_geometry_change(tmp_path):
    frames = iter([_frame(0), np.zeros((1080, 3840, 3), np.uint8)])
    burst = cap.burst(
        shoot=lambda: next(frames),
        screens=SCREENS,
        hud="DP-1",
        windows_fn=lambda: [],
        tabs=[],
        active_monitor="HDMI-A-2",
        workdir=tmp_path,
        sleep=lambda s: None,
        rules=skip.Rules(),
    )
    assert not burst.ok and burst.reason == "monitors_changed"
    assert not list(tmp_path.rglob("*.jpg"))


def test_burst_capture_failure(tmp_path):
    burst = cap.burst(
        shoot=lambda: None,
        screens=SCREENS,
        hud="DP-1",
        windows_fn=lambda: [],
        tabs=[],
        active_monitor="HDMI-A-2",
        workdir=tmp_path,
        sleep=lambda s: None,
        rules=skip.Rules(),
    )
    assert not burst.ok and burst.reason == "capture"


def test_workdir_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    d = cap.private_workdir()
    assert d.is_dir() and (d.stat().st_mode & 0o777) == 0o700
    assert str(d).startswith(str(tmp_path / "jarvis-aware"))


@pytest.mark.slow
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")
def test_encode_real_mp4(tmp_path):
    import cv2

    for i in range(6):
        img = np.full((540, 960, 3), i * 30, np.uint8)
        cv2.imwrite(str(tmp_path / f"m0_{i:02d}.jpg"), img)
    out = cap.encode(tmp_path, "m0", tmp_path / "m0.mp4")
    assert out and out.stat().st_size > 500
