import pytest

import monitor_ascii as ma
import monitor_mode


@pytest.mark.parametrize(
    "t",
    [
        "monitor mode",
        "start monitor mode",
        "can you monitor this device",
        "vault mode please",
    ],
)
def test_start_phrases(t):
    assert monitor_mode.parse_command(t)


@pytest.mark.parametrize(
    "t", ["monitor the build", "my monitor is flickering", "stop monitor mode", ""]
)
def test_not_start(t):
    assert not monitor_mode.parse_command(t)


@pytest.mark.parametrize("size", [(170, 50), (100, 30), (60, 20), (10, 4)])
def test_compose_ascii_and_sized(size):
    for t in (0.3, 1.5, 3.0, 5.0, 9.0, 25.0):
        g = ma.compose(
            *size,
            t,
            bay=1,
            bays=2,
            clock="01:02:03",
            left=("A",),
            right=("B",),
            status="x",
        )
        assert (g.cols, g.rows) == size and g.text().isascii()


def test_idle_scene_has_vault_parts():
    text = ma.compose(170, 50, 9.0, bay=0, bays=2).text()
    assert "MONITOR :: VAULT 01/02" in text and "LOCKED" in text
    assert "JARVIS IS MONITORING THIS DEVICE" in text


def test_launch_and_stop_leave_no_log(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    seen = {}
    monkeypatch.setattr(monitor_mode._wm, "is_running", lambda: False)
    got = monitor_mode.launch(popen=lambda argv, **kw: seen.update(kw))
    assert got["ok"]
    import subprocess

    assert seen["stdout"] is subprocess.DEVNULL and seen["stderr"] is subprocess.DEVNULL
    assert not any(p.suffix == ".log" for p in tmp_path.rglob("*"))
    assert monitor_mode.request_stop() is False  # not running
