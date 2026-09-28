"""Tests for the school-mode taskbar backend (src/taskbar.py + bridge routes).

All subprocess use is faked: the fake runner records argv (tests assert
exact argv) and returns canned results. Real power/window/launch commands
are NEVER executed here.
"""

import json
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, "src")

import taskbar


@pytest.fixture(autouse=True)
def _clear_caches():
    taskbar._launchers_cache.clear()
    taskbar._apps_cache.clear()
    yield
    taskbar._launchers_cache.clear()
    taskbar._apps_cache.clear()


def _cp(rc=0, stdout="", stderr=""):
    import types

    return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


def _fake_clock(start=100.0):
    now = [start]
    return now, lambda: now[0]


UUID = "12345678-1234-1234-1234-123456789abc"
BRACED = "{" + UUID + "}"


# --- window id validation ---


def test_normalize_window_id():
    assert taskbar.normalize_window_id(BRACED) == BRACED
    assert taskbar.normalize_window_id(UUID) == BRACED
    assert taskbar.normalize_window_id("  " + UUID + "  ") == BRACED
    assert taskbar.normalize_window_id(UUID.upper()) == "{" + UUID.upper() + "}"
    for bad in ("", "{}", "{xyz}", "1234", UUID[:-1], None, 42, "{ " + UUID + "}"):
        assert taskbar.normalize_window_id(bad) is None


# --- POST /window ---


def _run_fail(*a, **k):
    raise AssertionError("runner must not be called")


def test_window_action_activate_argv():
    calls = []

    def fake(argv, **kw):
        calls.append(list(argv))
        assert kw.get("timeout") == 5.0
        return _cp(0, "")

    assert taskbar.window_action(BRACED, "activate", run=fake) is True
    assert calls == [
        [
            "dbus-send", "--session", "--print-reply",
            "--dest=org.kde.KWin", "/WindowsRunner",
            "org.kde.krunner1.Run",
            f"string:0_{BRACED}", "string:",
        ]
    ]


def test_window_action_minimize_argv():
    calls = []

    def fake(argv, **kw):
        calls.append(list(argv))
        return _cp(0, "")

    assert taskbar.window_action(BRACED, "minimize", run=fake) is True
    assert calls[0][5] == "org.kde.krunner1.Run"
    assert calls[0][6] == f"string:2_{BRACED}"
    assert calls[0][7] == "string:"


def test_window_action_bad_action_runs_nothing():
    assert taskbar.window_action(BRACED, "close", run=_run_fail) is False
    assert taskbar.window_action(BRACED, "", run=_run_fail) is False


def test_window_action_falls_back_to_kwin_script(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(taskbar, "kwin_script_path", lambda: tmp_path / "act.js")

    def fake(argv, **kw):
        calls.append(list(argv))
        if argv[0] == "dbus-send":
            return _cp(1, "", "nope")  # Run fails -> fallback
        return _cp(0, "ok")

    slept = []
    monkeypatch.setattr(taskbar.time, "sleep", lambda s: slept.append(s))
    assert taskbar.window_action(BRACED, "minimize", run=fake) is True
    kinds = [c[0] for c in calls]
    assert kinds[0] == "dbus-send"  # primary first
    assert kinds[1:] == ["qdbus", "qdbus", "qdbus"]  # load, start, unload
    assert "loadScript" in calls[1][3]
    assert calls[1][4].endswith(".js")
    assert calls[1][5] == taskbar._KWIN_ACTION_NAME
    assert calls[2][3] == "org.kde.kwin.Scripting.start"
    assert calls[3][3] == "org.kde.kwin.Scripting.unloadScript"
    text = (tmp_path / "act.js").read_text()
    assert UUID in text and "minimized" in text


def test_window_action_false_when_everything_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(taskbar, "kwin_script_path", lambda: tmp_path / "x.js")

    def fake(argv, **kw):
        return _cp(1, "", "down")

    monkeypatch.setattr(taskbar.time, "sleep", lambda s: None)
    assert taskbar.window_action(BRACED, "activate", run=fake) is False


def test_kwin_script_source_shapes():
    mini = taskbar.kwin_script_source(BRACED, "minimize")
    assert UUID in mini and "w.minimized = true" in mini
    assert "activeWindow" not in mini
    act = taskbar.kwin_script_source(BRACED, "activate")
    assert UUID in act and "workspace.activeWindow = w" in act


def test_handle_window_validation(monkeypatch):
    monkeypatch.setattr(taskbar, "window_action", lambda *a, **k: True)
    assert taskbar.handle_window({})[0] == 400
    assert taskbar.handle_window({"id": "nope", "action": "activate"})[0] == 400
    assert taskbar.handle_window({"id": UUID, "action": "close"})[0] == 400
    assert taskbar.handle_window({"id": UUID})[0] == 400
    assert taskbar.handle_window("junk")[0] == 400
    code, payload = taskbar.handle_window({"id": UUID, "action": "minimize"})
    assert code == 200 and payload == {"ok": True}


# --- launchers parsing ---


_APPLETS = """\
[Containments][215]
lastScreen=0
location=4
plugin=org.kde.panel

[Containments][215][Applets][218]
plugin=org.kde.plasma.icontasks

[Containments][215][Applets][218][Configuration][General]
launchers=applications:brave-browser.desktop,preferred://browser

[Containments][269]
lastScreen=1
location=4
plugin=org.kde.panel

[Containments][269][Applets][272]
plugin=org.kde.plasma.icontasks

[Containments][269][Applets][272][Configuration][General]
launchers=applications:systemsettings.desktop,preferred://filemanager
"""


def test_select_launcher_entries_prefers_primary_panel():
    assert taskbar.select_launcher_entries(_APPLETS) == [
        "applications:brave-browser.desktop",
        "preferred://browser",
    ]


def test_select_launcher_entries_falls_back_to_first_with_launchers():
    text = _APPLETS.replace(
        "launchers=applications:brave-browser.desktop,preferred://browser",
        "groupingAppIdBlacklist=x",
    )
    assert taskbar.select_launcher_entries(text) == [
        "applications:systemsettings.desktop",
        "preferred://filemanager",
    ]


def test_select_launcher_entries_empty():
    assert taskbar.select_launcher_entries("") == []
    assert taskbar.select_launcher_entries("[Containments][1]\nplugin=x\n") == []


def test_launcher_entry_to_desktop():
    assert taskbar.launcher_entry_to_desktop("applications:foo.desktop") == "foo.desktop"
    assert taskbar.launcher_entry_to_desktop("applications:../evil") is None
    assert taskbar.launcher_entry_to_desktop("file:///usr/share/x.desktop") == "x.desktop"
    assert taskbar.launcher_entry_to_desktop("file:///tmp/notes.txt") is None
    assert taskbar.launcher_entry_to_desktop("") is None
    assert taskbar.launcher_entry_to_desktop("garbage") is None

    def fake(argv, **kw):
        if argv[0] == "xdg-settings":
            assert argv == ["xdg-settings", "get", "default-web-browser"]
            return _cp(0, "brave-browser.desktop\n")
        assert argv == ["xdg-mime", "query", "default", "inode/directory"]
        return _cp(0, "org.kde.dolphin.desktop\n")

    assert taskbar.launcher_entry_to_desktop("preferred://browser", run=fake) == (
        "brave-browser.desktop"
    )
    assert taskbar.launcher_entry_to_desktop("preferred://filemanager", run=fake) == (
        "org.kde.dolphin.desktop"
    )
    assert taskbar.resolve_preferred("preferred://other", run=fake) is None
    assert taskbar.resolve_preferred("preferred://browser", run=_run_fail) is None


def test_launchers_resolves_names_and_caches(tmp_path):
    apps = tmp_path / "apps"
    apps.mkdir()
    (apps / "brave-browser.desktop").write_text("[Desktop Entry]\nName=Brave\n")
    applets = tmp_path / "appletsrc"
    applets.write_text(_APPLETS)
    now, clock = _fake_clock()

    def fake(argv, **kw):
        assert argv[0] == "xdg-settings"
        return _cp(0, "brave-browser.desktop\n")

    first = taskbar.launchers(path=applets, run=fake, dirs=[apps], clock=clock)
    assert first == [
        {"desktop": "brave-browser.desktop", "name": "Brave"},
        {"desktop": "brave-browser.desktop", "name": "Brave"},
    ]
    # Cached path (default roots) avoids re-read: force default-shape call.
    taskbar._launchers_cache.clear()
    got = taskbar.launchers(text=_APPLETS, run=fake, dirs=[apps], clock=clock)
    assert got[0]["name"] == "Brave"
    now[0] += 60.0  # TTL expiry still returns fresh values, not crashes
    again = taskbar.launchers(text=_APPLETS, run=fake, dirs=[apps], clock=clock)
    assert again == got


def test_launchers_missing_file_is_empty(tmp_path):
    assert taskbar.launchers(path=tmp_path / "nope", clock=_fake_clock()[1]) == []


# --- POST /launch ---


def test_validate_desktop_id():
    assert taskbar.validate_desktop_id("org.kde.kcalc.desktop") == "org.kde.kcalc.desktop"
    assert taskbar.validate_desktop_id(" brave-browser.desktop ") == "brave-browser.desktop"
    for bad in ("", "x", "a b.desktop", "../x.desktop", "/abs/x.desktop",
                "x.desktop;rm", "x", None, 42, ".hidden.desktop", "x.Desktop"):
        if bad == "x.Desktop":
            assert taskbar.validate_desktop_id(bad) is None
        else:
            assert taskbar.validate_desktop_id(bad) is None


def test_launch_argv_prefers_kstart_then_gtk_then_kioclient(tmp_path):
    target = tmp_path / "org.kde.kcalc.desktop"
    assert taskbar.launch_argv("org.kde.kcalc.desktop", target,
                               which=lambda n: "/u/kstart" if n == "kstart" else None) == [
        "kstart", "--application", "org.kde.kcalc"
    ]
    assert taskbar.launch_argv("org.kde.kcalc.desktop", target,
                               which=lambda n: "/u/gtk-launch" if n == "gtk-launch" else None) == [
        "gtk-launch", "org.kde.kcalc"
    ]
    assert taskbar.launch_argv("org.kde.kcalc.desktop", target,
                               which=lambda n: "/u/kioclient" if n == "kioclient" else None) == [
        "kioclient", "exec", str(target)
    ]
    assert taskbar.launch_argv("org.kde.kcalc.desktop", target,
                               which=lambda n: None) is None


def test_launch_desktop_spawns_detached(tmp_path):
    apps = tmp_path / "apps"
    apps.mkdir()
    (apps / "org.kde.kcalc.desktop").write_text("[Desktop Entry]\nName=KCalc\n")
    calls = []

    def fake_popen(argv, **kw):
        calls.append((list(argv), kw))
        assert kw.get("start_new_session") is True
        return object()

    assert taskbar.launch_desktop(
        "org.kde.kcalc.desktop", dirs=[apps],
        which=lambda n: "/usr/bin/kstart" if n == "kstart" else None,
        popen=fake_popen,
    ) is True
    assert calls[0][0] == ["kstart", "--application", "org.kde.kcalc"]
    assert taskbar.launch_desktop("missing.desktop", dirs=[apps],
                                  which=lambda n: "/k", popen=fake_popen) is False
    assert taskbar.launch_desktop("../evil.desktop", dirs=[apps],
                                  which=lambda n: "/k", popen=fake_popen) is False
    assert len(calls) == 1  # failures never spawn


def test_handle_launch_validation(monkeypatch, tmp_path):
    assert taskbar.handle_launch({})[0] == 400
    assert taskbar.handle_launch({"desktop": "../x.desktop"})[0] == 400
    assert taskbar.handle_launch("junk")[0] == 400
    monkeypatch.setattr(taskbar, "find_desktop", lambda *a, **k: None)
    assert taskbar.handle_launch({"desktop": "nope.desktop"})[0] == 400
    monkeypatch.setattr(taskbar, "find_desktop", lambda *a, **k: tmp_path / "x.desktop")
    monkeypatch.setattr(taskbar, "launch_desktop", lambda *a, **k: True)
    code, payload = taskbar.handle_launch({"desktop": "x.desktop"})
    assert code == 200 and payload == {"ok": True}


# --- GET /apps ---


def _write_desktop(directory, name, body):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(body)


def test_parse_desktop_file_filters():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        good = root / "good.desktop"
        good.write_text(
            "[Desktop Entry]\nType=Application\nName=Good\nName[en]=Good EN\n"
            "GenericName=Calc\nCategories=Utility;Office;\n"
        )
        rec = taskbar.parse_desktop_file(good)
        assert rec == {"desktop": "good.desktop", "name": "Good EN",
                       "generic": "Calc", "categories": ["Utility", "Office"],
                       "icon_key": "good.desktop"}
        for name, body in (
            ("nodisp.desktop", "[Desktop Entry]\nName=X\nNoDisplay=true\n"),
            ("hidden.desktop", "[Desktop Entry]\nName=X\nHidden=true\n"),
            ("gnome.desktop", "[Desktop Entry]\nName=X\nOnlyShowIn=GNOME;\n"),
            ("link.desktop", "[Desktop Entry]\nName=X\nType=Link\nURL=http://x\n"),
            ("noname.desktop", "[Desktop Entry]\nType=Application\n"),
        ):
            (root / name).write_text(body)
            assert taskbar.parse_desktop_file(root / name) is None, name
        (root / "kde.desktop").write_text(
            "[Desktop Entry]\nName=K\nOnlyShowIn=KDE;GNOME;\n")
        assert taskbar.parse_desktop_file(root / "kde.desktop") is not None


def test_list_apps_dedupes_sorts_and_caches(tmp_path):
    user = tmp_path / "user"
    sysd = tmp_path / "sys"
    _write_desktop(user, "dup.desktop", "[Desktop Entry]\nName=User Dup\n")
    _write_desktop(sysd, "dup.desktop", "[Desktop Entry]\nName=Sys Dup\n")
    _write_desktop(sysd, "aaa.desktop", "[Desktop Entry]\nName=zzz last\n")
    _write_desktop(sysd, "bbb.desktop", "[Desktop Entry]\nName=aaa first\n")
    _write_desktop(sysd, "hide.desktop", "[Desktop Entry]\nName=H\nNoDisplay=true\n")
    now, clock = _fake_clock()
    apps = taskbar.list_apps(dirs=[user, sysd], clock=clock)
    assert [a["name"] for a in apps] == ["aaa first", "User Dup", "zzz last"]
    assert apps[1]["icon_key"] == "dup.desktop"  # user dir wins
    now[0] += 120.0
    assert taskbar.list_apps(dirs=[user, sysd], clock=clock) == apps


# --- GET /quick readers ---


def test_wifi_state():
    def fake(argv, **kw):
        if argv[:2] == ["nmcli", "radio"]:
            assert argv == ["nmcli", "radio", "wifi"]
            return _cp(0, "enabled\n")
        assert argv == ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi"]
        return _cp(0, "no:Other:70\nyes:HomeNet:80\n")

    assert taskbar.wifi_state(run=fake) == {"on": True, "ssid": "HomeNet"}
    assert taskbar.wifi_state(run=_run_fail) is None
    assert taskbar.wifi_state(run=lambda argv, **kw: _cp(1, "", "e")) is None


def test_bluetooth_state():
    def on(argv, **kw):
        return _cp(0, "Controller XX\n\tPowered: yes\n")

    def off(argv, **kw):
        return _cp(0, "Controller XX\n\tPowered: no\n")

    def empty(argv, **kw):
        return _cp(0, "nothing")

    assert taskbar.bluetooth_state(run=on) == {"on": True}
    assert taskbar.bluetooth_state(run=off) == {"on": False}
    assert taskbar.bluetooth_state(run=empty) is None
    assert taskbar.bluetooth_state(run=_run_fail) is None


def test_volume_state_wpctl_and_pactl():
    assert taskbar.volume_state(
        run=lambda argv, **kw: _cp(0, "Volume: 0.45 [MUTED]\n")) == {
        "pct": 45, "muted": True}
    # Overamplification reads back honestly (>100).
    assert taskbar.volume_state(
        run=lambda argv, **kw: _cp(0, "Volume: 1.25\n")) == {
        "pct": 125, "muted": False}

    def fake(argv, **kw):
        if argv[0] == "wpctl":
            return _cp(1, "", "nope")
        if "get-sink-volume" in argv:
            return _cp(0, "Volume: front-left: 50000 /  76% / -4.16 dB")
        return _cp(0, "Mute: yes")

    assert taskbar.volume_state(run=fake) == {"pct": 76, "muted": True}
    assert taskbar.volume_state(run=lambda argv, **kw: _cp(1, "", "d")) is None


def test_brightness_state_and_set(tmp_path):
    dev = tmp_path / "intel_backlight"
    dev.mkdir()
    (dev / "brightness").write_text("9600\n")
    (dev / "max_brightness").write_text("19200\n")
    assert taskbar.brightness_state(root=tmp_path) == {"pct": 50}
    assert taskbar.brightness_state(root=tmp_path / "nope") is None
    assert taskbar.set_brightness(50, root=tmp_path) is True
    assert (dev / "brightness").read_text() == "9600"
    assert taskbar.set_brightness(0, root=tmp_path) is False
    assert taskbar.set_brightness(101, root=tmp_path) is False
    assert taskbar.set_brightness(50, root=tmp_path / "nope") is False


def test_dnd_state():
    def yes(argv, **kw):
        return _cp(0, "variant boolean true\n")

    def no(argv, **kw):
        return _cp(0, "variant       boolean false\n")

    def failing(argv, **kw):
        return _cp(1, "", "e")

    assert taskbar.dnd_state(run=yes) is True
    assert taskbar.dnd_state(run=no) is False
    assert taskbar.dnd_state(run=failing) is None
    assert taskbar.dnd_state(run=_run_fail) is None


def test_quick_state_shape():
    def fake(argv, **kw):
        cmd = argv[0]
        if cmd == "nmcli":
            return _cp(0, "enabled\n" if argv[:2] == ["nmcli", "radio"] else "yes:S:80\n")
        if cmd == "bluetoothctl":
            return _cp(0, "Powered: yes\n")
        if cmd == "wpctl":
            return _cp(0, "Volume: 1.00\n")
        if cmd == "dbus-send":
            return _cp(0, "variant boolean false\n")
        raise AssertionError(argv)

    state = taskbar.quick_state(run=fake, backlight="/nonexistent-backlight-xyz")
    assert state == {"wifi": {"on": True, "ssid": "S"}, "bluetooth": {"on": True},
                     "volume": {"pct": 100, "muted": False},
                     "brightness": None, "dnd": False}


# --- POST /quick ---


def test_handle_quick_wifi_bluetooth_argv():
    calls = []

    def fake(argv, **kw):
        calls.append(list(argv))
        return _cp(0, "")

    assert taskbar.handle_quick({"action": "wifi", "on": False}, run=fake) == (
        200, {"ok": True})
    assert taskbar.handle_quick({"action": "bluetooth", "on": True}, run=fake) == (
        200, {"ok": True})
    assert calls == [["nmcli", "radio", "wifi", "off"],
                     ["bluetoothctl", "power", "on"]]
    assert taskbar.handle_quick({"action": "wifi", "on": "yes"}, run=fake)[0] == 400
    assert taskbar.handle_quick({"action": "wifi"}, run=fake)[0] == 400


def test_handle_quick_volume_and_mute_argv():
    calls = []

    def fake(argv, **kw):
        calls.append(list(argv))
        return _cp(0, "")

    assert taskbar.handle_quick({"action": "volume", "pct": 45}, run=fake,
                                which=lambda n: "/u/wpctl") == (200, {"ok": True})
    assert taskbar.handle_quick({"action": "mute", "on": True}, run=fake,
                                which=lambda n: "/u/wpctl") == (200, {"ok": True})
    assert calls == [
        ["wpctl", "set-volume", "-l", "1.5", "@DEFAULT_AUDIO_SINK@", "0.45"],
        ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"],
    ]
    # Overamplification to 150% passes the wpctl limit flag through.
    calls.clear()
    assert taskbar.handle_quick({"action": "volume", "pct": 150}, run=fake,
                                which=lambda n: "/u/wpctl") == (200, {"ok": True})
    assert calls == [
        ["wpctl", "set-volume", "-l", "1.5", "@DEFAULT_AUDIO_SINK@", "1.50"],
    ]
    # pactl fallback when wpctl is absent
    calls.clear()
    assert taskbar.handle_quick({"action": "volume", "pct": 100}, run=fake,
                                which=lambda n: None) == (200, {"ok": True})
    assert calls == [["pactl", "set-sink-volume", "@DEFAULT_SINK@", "100%"]]
    calls.clear()
    assert taskbar.handle_quick({"action": "volume", "pct": 150}, run=fake,
                                which=lambda n: None) == (200, {"ok": True})
    assert calls == [["pactl", "set-sink-volume", "@DEFAULT_SINK@", "150%"]]
    for bad in ({"action": "volume"}, {"action": "volume", "pct": -1},
                {"action": "volume", "pct": 151}, {"action": "volume", "pct": True},
                {"action": "mute"}, {"action": "mute", "on": 1}):
        assert taskbar.handle_quick(bad, run=fake)[0] == 400, bad
    assert taskbar.handle_quick({"action": "volume", "pct": 10},
                                run=_run_fail)[0] == 200  # runner error -> ok false


def test_handle_quick_brightness_validation(tmp_path):
    dev = tmp_path / "bl"
    dev.mkdir()
    (dev / "brightness").write_text("100\n")
    (dev / "max_brightness").write_text("100\n")
    assert taskbar.handle_quick({"action": "brightness", "pct": 50},
                                backlight=tmp_path) == (200, {"ok": True})
    for bad in ({"action": "brightness"}, {"action": "brightness", "pct": 0},
                {"action": "brightness", "pct": 101}):
        assert taskbar.handle_quick(bad, backlight=tmp_path)[0] == 400
    code, payload = taskbar.handle_quick({"action": "brightness", "pct": 50},
                                         backlight=tmp_path / "nope")
    assert code == 200 and payload["ok"] is False


def test_handle_quick_dnd_and_unknown():
    code, payload = taskbar.handle_quick({"action": "dnd", "on": True}, run=_run_fail)
    assert code == 200 and payload["ok"] is False and "error" in payload
    assert taskbar.handle_quick({"action": "dnd"}, run=_run_fail)[0] == 400
    assert taskbar.handle_quick({"action": "frobnicate"})[0] == 400
    assert taskbar.handle_quick({})[0] == 400
    assert taskbar.handle_quick("junk")[0] == 400


# --- POST /power (never executes without a valid action) ---


def test_power_argv_exact():
    assert taskbar.power_argv("lock") == ["loginctl", "lock-session"]
    assert taskbar.power_argv("sleep") == ["systemctl", "suspend"]
    assert taskbar.power_argv("logout") == [
        "dbus-send", "--session", "--print-reply",
        "--dest=org.kde.Shutdown", "/Shutdown", "org.kde.Shutdown.logout",
    ]
    assert taskbar.power_argv("restart") == ["systemctl", "reboot"]
    assert taskbar.power_argv("shutdown") == ["systemctl", "poweroff"]
    assert taskbar.power_argv("halt") is None
    assert taskbar.power_argv("") is None


def test_run_power_records_argv_and_logs(monkeypatch):
    import system

    calls, logged = [], []
    monkeypatch.setattr(system, "log_action", lambda c, d: logged.append((c, d)))
    for action, want in (("lock", ["loginctl", "lock-session"]),
                         ("sleep", ["systemctl", "suspend"]),
                         ("logout", taskbar.power_argv("logout")),
                         ("restart", ["systemctl", "reboot"]),
                         ("shutdown", ["systemctl", "poweroff"])):
        calls.clear()

        def fake(argv, **kw):
            calls.append(list(argv))
            assert kw.get("timeout") == 5.0
            return _cp(0, "")

        assert taskbar.run_power(action, run=fake) is True
        assert calls == [want]
    assert logged == [("power", a) for a in ("lock", "sleep", "logout", "restart", "shutdown")]


def test_run_power_never_runs_without_valid_action():
    assert taskbar.run_power("rm -rf /", run=_run_fail) is False
    assert taskbar.run_power("", run=_run_fail) is False

    def fake(argv, **kw):
        return _cp(1, "", "denied")

    assert taskbar.run_power("lock", run=fake) is False


def test_handle_power_rejects_before_running(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("must not run without a valid action")

    monkeypatch.setattr(taskbar, "run_power", _boom)
    for bad in ({}, {"action": "halt"}, {"action": ""}, {"action": None}, "junk"):
        code, payload = taskbar.handle_power(bad)
        assert code == 400 and payload["ok"] is False
    monkeypatch.setattr(taskbar, "run_power", lambda action: True)
    assert taskbar.handle_power({"action": "lock"}) == (200, {"ok": True})


# --- HTTP routes through the bridge (same auth + JSON rules as /mode) ---


def _get(server, path, token=""):
    port = server.server_address[1]
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.load(resp)
    except Exception as exc:
        status = getattr(exc, "code", None) or 0
        try:
            body = json.loads(exc.read().decode())
        except Exception:
            body = {}
        return status, body


def _post(server, path, payload, token="", raw=None):
    import urllib.error

    port = server.server_address[1]
    body = raw if raw is not None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body, method="POST", headers={"Content-Type": "application/json"},
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return exc.code, {}


def test_window_route(monkeypatch):
    import bridge

    seen = []
    monkeypatch.setattr(taskbar, "window_action",
                        lambda uuid, action: seen.append((uuid, action)) or True)
    server, _ = bridge._run_in_thread()
    try:
        code, body = _post(server, "/window", {"id": UUID, "action": "activate"})
        assert code == 200 and body == {"ok": True}
        assert seen == [(BRACED, "activate")]
        code, body = _post(server, "/window", {"id": "junk", "action": "activate"})
        assert code == 400 and body["ok"] is False
        code, body = _post(server, "/window", {}, raw=b"not json")
        assert code == 400 and body["ok"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_launch_route(monkeypatch):
    import bridge

    monkeypatch.setattr(taskbar, "find_desktop", lambda d: Path("/x/x.desktop"))
    monkeypatch.setattr(taskbar, "launch_desktop", lambda d: True)
    server, _ = bridge._run_in_thread()
    try:
        code, body = _post(server, "/launch", {"desktop": "x.desktop"})
        assert code == 200 and body == {"ok": True}
        code, body = _post(server, "/launch", {"desktop": "../evil.desktop"})
        assert code == 400 and body["ok"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_apps_and_quick_get_routes(monkeypatch):
    import bridge

    monkeypatch.setattr(taskbar, "list_apps",
                        lambda: [{"desktop": "a.desktop", "name": "A",
                                  "generic": "", "categories": [], "icon_key": "a.desktop"}])
    monkeypatch.setattr(bridge, "_volume_status", lambda **kw: {"pct": 40, "muted": False})
    monkeypatch.setattr(taskbar, "wifi_state", lambda **kw: {"on": True, "ssid": "S"})
    monkeypatch.setattr(taskbar, "bluetooth_state", lambda **kw: {"on": False})
    monkeypatch.setattr(taskbar, "brightness_state", lambda **kw: {"pct": 80})
    monkeypatch.setattr(taskbar, "dnd_state", lambda **kw: False)
    server, _ = bridge._run_in_thread()
    try:
        code, body = _get(server, "/apps")
        assert code == 200 and body["ok"] is True
        assert body["apps"][0]["icon_key"] == "a.desktop"
        code, body = _get(server, "/quick")
        assert code == 200 and body["ok"] is True
        assert body["volume"] == {"pct": 40, "muted": False}
        assert body["wifi"] == {"on": True, "ssid": "S"}
        assert body["dnd"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_quick_post_route_clears_volume_cache(monkeypatch):
    import bridge

    monkeypatch.setattr(taskbar, "handle_quick",
                        lambda body: (200, {"ok": True}))
    bridge._VOL_CACHE.update(at=0.0, value={"pct": 1, "muted": False})
    server, _ = bridge._run_in_thread()
    try:
        code, body = _post(server, "/quick", {"action": "volume", "pct": 10})
        assert code == 200 and body == {"ok": True}
        assert bridge._VOL_CACHE == {}
        code, body = _post(server, "/quick", {"action": "nope"})
        assert code == 200  # stubbed handler decides; validation lives in taskbar
    finally:
        server.shutdown()
        server.server_close()


def test_power_route_rejects_and_gates(monkeypatch):
    import bridge

    def _boom(*a, **k):
        raise AssertionError("power must never run on a bad action")

    monkeypatch.setattr(taskbar, "run_power", _boom)
    server, _ = bridge._run_in_thread(token="s3cret")
    try:
        code, _ = _post(server, "/power", {"action": "shutdown"})
        assert code == 401  # same bearer gate as /mode
        code, body = _post(server, "/power", {"action": "halt"}, token="s3cret")
        assert code == 400 and body["ok"] is False
        code, body = _post(server, "/power", {}, raw=b"nope", token="s3cret")
        assert code == 400 and body["ok"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_power_route_valid_action_uses_runner(monkeypatch):
    import bridge

    seen = []
    monkeypatch.setattr(taskbar, "run_power",
                        lambda action: seen.append(action) or True)
    server, _ = bridge._run_in_thread()
    try:
        code, body = _post(server, "/power", {"action": "lock"})
        assert code == 200 and body == {"ok": True}
        assert seen == ["lock"]
    finally:
        server.shutdown()
        server.server_close()


def test_taskbar_routes_share_bearer_gate(monkeypatch):
    import bridge

    monkeypatch.setattr(taskbar, "list_apps", lambda: [])
    server, _ = bridge._run_in_thread(token="s3cret")
    try:
        code, _ = _get(server, "/apps")
        assert code == 401
        code, _ = _get(server, "/apps", token="s3cret")
        assert code == 200
        code, _ = _post(server, "/window", {"id": UUID, "action": "activate"})
        assert code == 401
    finally:
        server.shutdown()
        server.server_close()
