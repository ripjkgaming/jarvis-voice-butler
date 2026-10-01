"""The headless desktop harness must own and clean every process it starts."""

import importlib.util
import io
import os
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_sandbox():
    path = Path(__file__).resolve().parents[1] / "scripts" / "sandbox_computer_use.py"
    spec = importlib.util.spec_from_file_location("sandbox_computer_use", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Process:
    def __init__(self, pid, output="unix:path=/tmp/private-test-bus\n"):
        self.pid = pid
        self.stdout = io.StringIO(output)
        self.waits = 0

    def poll(self):
        return None

    def wait(self, timeout):
        self.waits += 1
        return 0


def test_wait_window_matches_mapped_app_class_not_qt_helper_title(monkeypatch):
    sandbox = _load_sandbox()
    searches = []

    def xdo(*args):
        searches.append(args)
        return "123\n456"

    monkeypatch.setattr(sandbox, "_xdo", xdo)
    assert sandbox._wait_window("KCalc") == "123"
    assert searches == [("search", "--onlyvisible", "--class", "^KCalc$")]


def test_partial_setup_stops_xvfb_and_restores_environment(monkeypatch):
    sandbox = _load_sandbox()
    display = _Process(12345, "91\n")
    stopped = []
    private_paths = []
    monkeypatch.setattr(sandbox.select, "select", lambda readers, *a: (readers, [], []))
    monkeypatch.setattr(sandbox, "_stop_process", lambda proc: stopped.append(proc))
    monkeypatch.setattr(
        sandbox.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout="1280 800\n"),
    )
    monkeypatch.setenv("WAYLAND_DISPLAY", "real-wayland")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "real-session-bus")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/real/config")
    original = dict(os.environ)

    def popen(argv, **kwargs):
        private_paths.append(Path(os.environ["XDG_CONFIG_HOME"]))
        assert kwargs["start_new_session"]
        assert "WAYLAND_DISPLAY" not in os.environ
        assert "DBUS_SESSION_BUS_ADDRESS" not in os.environ
        if argv[0] == "Xvfb":
            assert "DISPLAY" not in os.environ
            assert argv[1:3] == ["-displayfd", "1"]
            return display
        assert os.environ["DISPLAY"] == ":91"
        raise OSError("fake bus startup failure")

    monkeypatch.setattr(sandbox.subprocess, "Popen", popen)
    with pytest.raises(OSError, match="fake bus"), sandbox._sandbox_session():
        pytest.fail("setup must fail before tests execute")
    assert stopped == [display]
    assert dict(os.environ) == original
    assert all(not path.exists() for path in private_paths)


def test_check_failure_stops_bus_and_display_and_restores_tool_hooks(monkeypatch):
    import system
    import system.desktop as desktop

    sandbox = _load_sandbox()
    processes = [_Process(12345, "91\n"), _Process(12346)]
    stopped = []
    created = iter(processes)
    original_log, original_input = system.LOG_PATH, desktop.UInputMouse
    monkeypatch.setattr(sandbox.select, "select", lambda readers, *a: (readers, [], []))
    monkeypatch.setattr(sandbox, "_stop_process", lambda proc: stopped.append(proc))
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *a, **kw: next(created))
    monkeypatch.setattr(
        sandbox.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout="1280 800\n"),
    )
    with (
        pytest.raises(RuntimeError, match="fake check failure"),
        sandbox._sandbox_session(),
    ):
        assert (
            os.environ["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/tmp/private-test-bus"
        )
        assert original_log != system.LOG_PATH
        with pytest.raises(AssertionError, match="sandbox leak"):
            desktop.UInputMouse()
        root = Path(os.environ["JARVIS_HOME"]).parent
        assert root.exists()
        raise RuntimeError("fake check failure")
    assert stopped == list(reversed(processes))
    assert original_log == system.LOG_PATH
    assert desktop.UInputMouse is original_input
    assert not root.exists()


@pytest.mark.parametrize("report", ["", "not-a-display\n", "1000\n", ":91\n"])
def test_invalid_display_report_never_starts_bus_or_runs_checks(monkeypatch, report):
    sandbox = _load_sandbox()
    display = _Process(12345, report)
    started, stopped = [], []
    original = dict(os.environ)
    original_display = sandbox.DISPLAY
    monkeypatch.setattr(sandbox.select, "select", lambda readers, *a: (readers, [], []))
    monkeypatch.setattr(sandbox, "_stop_process", lambda proc: stopped.append(proc))

    def popen(argv, **kwargs):
        started.append(argv)
        assert argv[0] == "Xvfb", "invalid display must fail before bus startup"
        return display

    monkeypatch.setattr(sandbox.subprocess, "Popen", popen)
    with (
        pytest.raises(RuntimeError, match="usable private display"),
        sandbox._sandbox_session(),
    ):
        pytest.fail("checks must not run against an unverified display")
    assert len(started) == 1
    assert stopped == [display]
    assert dict(os.environ) == original
    assert original_display == sandbox.DISPLAY


def test_display_startup_timeout_reaps_server_and_restores_environment(monkeypatch):
    sandbox = _load_sandbox()
    display = _Process(12345, "91\n")
    stopped = []
    original = dict(os.environ)
    monkeypatch.setattr(sandbox.select, "select", lambda *a: ([], [], []))
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *a, **kw: display)
    monkeypatch.setattr(sandbox, "_stop_process", lambda proc: stopped.append(proc))
    with (
        pytest.raises(RuntimeError, match="Xvfb startup timed out"),
        sandbox._sandbox_session(),
    ):
        pytest.fail("checks must not run before Xvfb reports its display")
    assert stopped == [display]
    assert dict(os.environ) == original


def test_cleanup_escalates_and_reaps_unresponsive_owned_process(monkeypatch):
    sandbox = _load_sandbox()
    proc = _Process(12345)
    calls = []
    monkeypatch.setattr(sandbox.os, "killpg", lambda pid, sig: calls.append((pid, sig)))

    def wait(timeout):
        proc.waits += 1
        if proc.waits == 1:
            raise subprocess.TimeoutExpired("owned-test-child", timeout)
        return -signal.SIGKILL

    monkeypatch.setattr(proc, "wait", wait)
    sandbox._stop_process(proc)
    assert calls == [(proc.pid, signal.SIGTERM), (proc.pid, signal.SIGKILL)]
    assert proc.waits == 2
    assert proc.stdout.closed
