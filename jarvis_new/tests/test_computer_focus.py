"""Foreground probes use fake DBus/subprocesses, never the desktop session."""

import asyncio
import json
import re
import subprocess
import threading
from collections import deque
from contextlib import contextmanager
from pathlib import Path

import pytest
from jeepney import (
    DBusAddress,
    HeaderFields,
    MessageType,
    new_method_call,
    new_method_return,
)

import system.computer_focus as focus
from system.computer_focus import ComputerFocusError, FocusProbe, FocusSnapshot


def payload(nonce="n"):
    return {
        "nonce": nonce,
        "window_id": "{aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}",
        "focus_id": "{aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}",
        "app": "test-app",
        "pid": 123,
        "active": True,
        "minimized": False,
        "deleted": False,
        "frame": [10, 20, 120, 70],
        "desktop": [0, 0, 200, 100],
        "overlays": [["overlay", 180, 0, 20, 10]],
    }


@pytest.fixture
def kwin(monkeypatch):
    calls = []
    state = {"data": payload(), "sender": ":1.20", "nonce": None, "failure": None}

    class Connection:
        unique_name = ":1.91"

        def __init__(self):
            self.messages = deque()
            self.rule = None

        @contextmanager
        def filter(self, rule, **kwargs):
            self.rule = rule
            yield self.messages

        def send_and_get_reply(self, message, timeout=None):
            assert timeout is not None and 0 < timeout < 3
            member = message.header.fields[HeaderFields.member]
            calls.append((member, tuple(message.body)))
            if state["failure"] == member:
                raise TimeoutError("fake timeout")
            if member == "GetNameOwner":
                return new_method_return(message, "s", (":1.20",))
            if member == "loadScript":
                path, plugin = message.body
                script = Path(path).read_text()
                state["script"] = script
                state["path"] = Path(path)
                state["plugin"] = plugin
                state["nonce"] = json.loads(
                    re.search(r"const nonce = (.+);", script).group(1)
                )
                assert Path(path).stat().st_mode & 0o777 == 0o600
                return new_method_return(message, "i", (7,))
            if member == "run":
                assert message.header.fields[HeaderFields.path] == "/Scripting/Script7"
                data = dict(state["data"], nonce=state["nonce"])
                if state.get("bad_nonce"):
                    data["nonce"] = "wrong"
                address = DBusAddress(
                    "/org/jarvis/ComputerFocus",
                    self.unique_name,
                    "org.jarvis.ComputerFocus",
                )
                callback = new_method_call(
                    address, "Snapshot", "s", (json.dumps(data),)
                )
                callback.header.fields[HeaderFields.sender] = state["sender"]
                if self.rule.matches(callback):
                    self.messages.append(callback)
                return new_method_return(message)
            if member == "unloadScript":
                return new_method_return(message, "b", (True,))
            raise AssertionError(f"unexpected DBus operation {member}")

        def recv_until_filtered(self, queue, timeout=None):
            if queue:
                return queue.popleft()
            raise TimeoutError("no authenticated snapshot")

        def send(self, message):
            assert message.header.message_type == MessageType.method_return
            calls.append(("ack", ()))

        def close(self):
            calls.append(("close", ()))

    def connect(*, bus, auth_timeout):
        assert bus == "unix:path=/private-test-bus"
        assert 0 < auth_timeout <= 1
        calls.append(("connect", ()))
        return Connection()

    monkeypatch.setattr(focus, "open_dbus_connection", connect)
    return calls, state


async def test_kwin_snapshot_authenticates_and_cleans_only_own_script(kwin, tmp_path):
    calls, state = kwin
    probe = FocusProbe(
        tmp_path, None, {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/private-test-bus"}
    )
    first = await probe.snapshot()
    assert first.window_id == payload()["window_id"]
    assert first.focus_id == first.window_id
    assert first.frame == (10, 20, 120, 70)
    assert first.desktop == (0, 0, 200, 100)
    assert first.overlays == (("overlay", 180, 0, 20, 10),)
    assert not state["path"].exists()
    assert ("unloadScript", (state["plugin"],)) in calls
    assert "workspace.activeWindow =" not in state["script"]
    assert "frameGeometry =" not in state["script"]
    assert not any(member == "start" for member, _ in calls)
    second = await probe.snapshot()
    assert first.signature == second.signature
    assert first.observed_at <= second.observed_at
    assert calls.count(("connect", ())) == 1
    await probe.close()
    assert calls[-1] == ("close", ())


@pytest.mark.parametrize(
    "bad",
    ["sender", "nonce", "geometry", "focus", "pid", "app", "minimized", "overlays"],
)
async def test_kwin_fails_closed_on_untrusted_or_ambiguous_snapshot(
    bad, kwin, tmp_path
):
    calls, state = kwin
    if bad == "sender":
        state["sender"] = ":1.99"
    elif bad == "nonce":
        state["bad_nonce"] = True
    elif bad == "geometry":
        state["data"]["frame"] = [10.5, 20, 120, 70]
    elif bad == "focus":
        state["data"]["focus_id"] = "another"
    elif bad == "pid":
        state["data"]["pid"] = 0
    elif bad == "app":
        state["data"]["app"] = ""
    elif bad == "minimized":
        state["data"]["minimized"] = True
    else:
        state["data"]["overlays"] = [["", 0, 0, 10, 10]]
    probe = FocusProbe(
        tmp_path, None, {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/private-test-bus"}
    )
    with pytest.raises(ComputerFocusError):
        await probe.snapshot()
    assert any(member == "unloadScript" for member, _ in calls)
    assert list(tmp_path.iterdir()) == []
    await probe.close()


@pytest.mark.parametrize("failure", ["loadScript", "run"])
async def test_kwin_cleanup_after_unknown_load_or_run_outcome(failure, kwin, tmp_path):
    calls, state = kwin
    state["failure"] = failure
    probe = FocusProbe(
        tmp_path, None, {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/private-test-bus"}
    )
    with pytest.raises(ComputerFocusError):
        await probe.snapshot()
    assert any(member == "unloadScript" for member, _ in calls)
    assert list(tmp_path.iterdir()) == []
    await probe.close()


@pytest.fixture
def x11(monkeypatch):
    calls = []
    state = {
        "raw_focus": "43",
        "top": "42",
        "active": "42",
        "class": "test-app",
        "pid": "123",
        "visible": "999\n31\n42\n77",
        "version": "xdotool version 3.20211022.1",
    }
    monkeypatch.setattr(
        focus.shutil, "which", lambda name, **kwargs: "/usr/bin/" + name
    )

    def run(argv, **kwargs):
        assert Path(argv[0]).name == "xdotool"
        assert kwargs["env"]["DISPLAY"] == ":91"
        assert "WAYLAND_DISPLAY" not in kwargs["env"]
        assert 0 < kwargs["timeout"] < 3
        calls.append(tuple(argv[1:]))
        command = argv[1]
        out = ""
        if command == "--version":
            out = state["version"]
        elif command == "getwindowfocus":
            out = state["raw_focus"] if "-f" in argv else state["top"]
            if "-f" in argv and state.get("focus_sequence"):
                out = state["focus_sequence"].pop(0)
        elif command == "getactivewindow":
            if not state["active"]:
                return subprocess.CompletedProcess(argv, 1, "", "no WM")
            out = state["active"]
        elif command == "getwindowgeometry":
            wid = argv[-1]
            out = f"WINDOW={wid}\nX=10\nY=20\nWIDTH=120\nHEIGHT=70\nSCREEN=0\n"
            if wid == "42" and state.get("geometry_sequence"):
                out = out.replace("X=10", f"X={state['geometry_sequence'].pop(0)}")
        elif command == "getwindowpid":
            out = state["pid"]
        elif command == "getwindowclassname":
            out = state["class"]
        elif command == "getdisplaygeometry":
            out = "200 100"
        elif command == "search":
            assert "--screen" in argv and argv[argv.index("--screen") + 1] == "0"
            out = state["visible"]
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(argv, 0, out, "")

    monkeypatch.setattr(focus.subprocess, "run", run)
    return calls, state


async def test_x11_pinned_focus_geometry_and_conservative_overlays(x11, tmp_path):
    calls, state = x11
    probe = FocusProbe(tmp_path, ":91", {"DISPLAY": ":0", "WAYLAND_DISPLAY": "real"})
    result = await probe.snapshot()
    assert result.window_id == "42" and result.focus_id == "43"
    assert result.app == "test-app" and result.pid == 123
    assert result.frame == (10, 20, 120, 70)
    assert result.desktop == (0, 0, 200, 100)
    assert result.overlays == (("77", 10, 20, 120, 70),)
    assert not any(
        call[0] == "getwindowgeometry" and call[-1] in {"999", "31"} for call in calls
    )
    state["active"] = ""
    assert (await probe.snapshot()).window_id == "42"  # WM-less Xvfb.
    await probe.close()


@pytest.mark.parametrize(
    "field,value",
    [("active", "99"), ("top", "0"), ("raw_focus", "1 2"), ("pid", "0"), ("class", "")],
)
async def test_x11_missing_or_ambiguous_target_fails(field, value, x11, tmp_path):
    _, state = x11
    state[field] = value
    probe = FocusProbe(tmp_path, ":91", {})
    with pytest.raises(ComputerFocusError):
        await probe.snapshot()
    await probe.close()


async def test_bad_executable_resolution_never_launches_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(focus.shutil, "which", lambda name, **kwargs: "/usr/bin/brave")
    monkeypatch.setattr(
        focus.subprocess, "run", lambda *a, **k: pytest.fail("must never execute")
    )
    probe = FocusProbe(tmp_path, ":91", {})
    with pytest.raises(ComputerFocusError):
        await probe.snapshot()
    await probe.close()


async def test_cancellation_drains_probe_before_close(monkeypatch, tmp_path):
    started, release = threading.Event(), threading.Event()
    calls = []

    def slow(self):
        started.set()
        assert release.wait(2)
        calls.append("cleaned")
        raise ComputerFocusError("fake probe ended")

    monkeypatch.setattr(FocusProbe, "_snapshot_sync", slow)
    probe = FocusProbe(tmp_path, ":91", {})
    task = asyncio.create_task(probe.snapshot())
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    closing = asyncio.create_task(probe.close())
    await asyncio.sleep(0.01)
    assert not task.done() and not closing.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await closing
    assert calls == ["cleaned"]


def test_signature_excludes_observation_time():
    values = {
        "window_id": "42",
        "app": "app",
        "pid": 123,
        "focus_id": "43",
        "frame": (0, 0, 100, 50),
        "desktop": (0, 0, 200, 100),
        "overlays": (),
    }
    assert (
        FocusSnapshot(**values, observed_at=1).signature
        == FocusSnapshot(**values, observed_at=2).signature
    )


def test_invalid_display_cannot_fall_through(tmp_path):
    with pytest.raises(ComputerFocusError):
        FocusProbe(tmp_path, "invalid", {})
    with pytest.raises(ComputerFocusError):
        FocusProbe(tmp_path, None, {"JARVIS_DESKTOP_SANDBOX": ":91"})


@pytest.mark.parametrize("change", ["focus_sequence", "geometry_sequence"])
async def test_x11_rechecks_focus_and_geometry_before_return(change, x11, tmp_path):
    _, state = x11
    state[change] = ["43", "44"] if change == "focus_sequence" else [10, 11]
    probe = FocusProbe(tmp_path, ":91", {})
    with pytest.raises(ComputerFocusError, match="changed"):
        await probe.snapshot()
    await probe.close()


async def test_x11_reparented_target_fails_closed(x11, tmp_path):
    _, state = x11
    state["visible"] = "999\n998\n77"
    probe = FocusProbe(tmp_path, ":91", {})
    with pytest.raises(ComputerFocusError, match="ambiguous"):
        await probe.snapshot()
    await probe.close()


async def test_cleanup_failure_is_reported_and_close_retries_only_owned_plugin(
    kwin, tmp_path
):
    calls, state = kwin
    state["failure"] = "unloadScript"
    probe = FocusProbe(
        tmp_path, None, {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/private-test-bus"}
    )
    with pytest.raises(ComputerFocusError, match="cleanup"):
        await probe.snapshot()
    plugin = state["plugin"]
    assert not state["path"].exists()
    state["failure"] = None
    await probe.close()
    assert [body for member, body in calls if member == "unloadScript"] == [
        (plugin,),
        (plugin,),
    ]


async def test_snapshot_deadline_prevents_queries_after_budget(
    x11, monkeypatch, tmp_path
):
    calls, _ = x11
    monkeypatch.setattr(focus, "PROBE_TIMEOUT_S", 0)
    probe = FocusProbe(tmp_path, ":91", {})
    with pytest.raises(ComputerFocusError, match="timed out"):
        await probe.snapshot()
    assert calls == []
    await probe.close()


@pytest.mark.parametrize(
    "version",
    [
        "xdotool version 3.20160805.1",
        "xdotool version 9.0",
        "",
        "3.20211022.1",
        "xdotool version 3.20211022.1\nextra",
    ],
)
async def test_x11_rejects_unverified_version_before_window_queries(
    version, x11, tmp_path
):
    calls, state = x11
    state["version"] = version
    probe = FocusProbe(tmp_path, ":91", {})
    with pytest.raises(ComputerFocusError, match="version"):
        await probe.snapshot()
    assert calls == [("--version",)]
    await probe.close()


async def test_x11_checks_supported_version_once_per_task(x11, tmp_path):
    calls, _ = x11
    probe = FocusProbe(tmp_path, ":91", {})
    await probe.snapshot()
    await probe.snapshot()
    assert calls[0] == ("--version",)
    assert calls.count(("--version",)) == 1
    await probe.close()


async def test_x11_keeps_focused_popup_above_target_in_overlay_inventory(x11, tmp_path):
    _, state = x11
    state["visible"] = "999\n31\n42\n43\n77"
    probe = FocusProbe(tmp_path, ":91", {})
    snapshot = await probe.snapshot()
    assert snapshot.focus_id == "43"
    assert snapshot.overlays == (("43", 10, 20, 120, 70), ("77", 10, 20, 120, 70))
    await probe.close()
