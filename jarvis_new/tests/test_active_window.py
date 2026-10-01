"""Tests for src/active_window.py: parsing, cache, KWin script wiring."""

import json
import types

import pytest

import active_window as AW  # noqa: N812


def _reset():
    AW._cache.update(at=0.0, value=None)
    AW._push.update(at=0.0, value=None)
    AW._listener_started = False
    AW._windows.clear()
    AW._windows_order.clear()
    AW._windows_seq = 0


def test_clean_normalizes():
    got = AW.clean(
        {"title": "  foo — Brave  ", "app": "brave-browser", "pid": "12", "id": "x"}
    )
    assert got == {"title": "foo — Brave", "app": "brave-browser", "pid": 12, "id": "x"}
    assert AW.clean({"title": "", "app": ""}) is None
    assert AW.clean(None) is None
    assert AW.clean({"title": "t", "app": "a", "pid": "junk"})["pid"] == 0


def test_parse_kdotool_id():
    assert AW.parse_kdotool_id("123456\n") == "123456"
    assert AW.parse_kdotool_id("  \n") == ""


def test_kdotool_active(monkeypatch):
    _reset()
    outs = {
        "getactivewindow": "777\n",
        "getwindowpid": "4242\n",
        "getwindowclassname": "brave-browser\n",
        "getwindowname": "Docs - Brave\n",
    }

    def fake_run(argv, **kw):
        return types.SimpleNamespace(returncode=0, stdout=outs[argv[1]])

    monkeypatch.setattr(AW, "_run", fake_run)
    got = AW.kdotool_active(binpath="/usr/bin/kdotool")
    assert got == {
        "title": "Docs - Brave",
        "app": "brave-browser",
        "pid": 4242,
        "id": "777",
    }


def test_kdotool_active_no_binary(monkeypatch):
    _reset()
    monkeypatch.setattr(AW, "kdotool_path", lambda: None)
    assert AW.kdotool_active() is None


@pytest.mark.parametrize("other", ["brave-browser", "brave", "claude", "jarvis-shell"])
def test_active_window_rejects_unrelated_resolved_command(monkeypatch, other):
    """A broad executable mock must never turn focus polling into a GUI launch."""
    from system import window_ctl

    _reset()
    monkeypatch.setattr(AW.shutil, "which", lambda name: f"/usr/bin/{other}")
    calls = []

    def capture(argv, **kwargs):
        calls.append(argv)
        return types.SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(AW, "_run", capture)
    assert window_ctl.active_title() == ""
    assert calls == []


def test_kdotool_path_accepts_installed_command(monkeypatch):
    monkeypatch.setattr(AW.shutil, "which", lambda name: "/opt/tools/kdotool")
    assert AW.kdotool_path() == "/opt/tools/kdotool"


def test_default_focus_test_isolation_never_spawns(monkeypatch):
    """The shared test fixture also blocks the non-D-Bus polling fallback."""
    calls = []

    def capture(argv, **kwargs):
        calls.append(argv)
        return types.SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(AW.subprocess, "run", capture)
    assert AW.kdotool_active(binpath="/usr/bin/kdotool") is None
    assert calls == []


def test_kdotool_active_empty_id(monkeypatch):
    _reset()

    def fake_run(argv, **kw):
        return types.SimpleNamespace(returncode=0, stdout="\n")

    monkeypatch.setattr(AW, "_run", fake_run)
    assert AW.kdotool_active(binpath="kdotool") is None


def test_active_caches_kdotool(monkeypatch):
    _reset()
    calls = []

    def fake_kdotool():
        calls.append(1)
        return {"title": "t", "app": "a", "pid": 1, "id": "i"}

    monkeypatch.setattr(AW, "ensure_listener", lambda: False)
    monkeypatch.setattr(AW, "kdotool_active", fake_kdotool)
    assert AW.active() == {"title": "t", "app": "a", "pid": 1, "id": "i"}
    assert AW.active() == {"title": "t", "app": "a", "pid": 1, "id": "i"}
    assert len(calls) == 1  # second call served from the 0.5 s cache


def test_active_prefers_push(monkeypatch):
    _reset()
    AW._listener_started = True
    AW._note_push("Konsole title", "org.kde.konsole", 99, "{uuid-1}")

    def boom():
        raise AssertionError("kdotool must not run when push cache is full")

    monkeypatch.setattr(AW, "kdotool_active", boom)
    got = AW.active()
    assert got is not None and got["app"] == "org.kde.konsole" and got["pid"] == 99


def test_kwin_script_shape():
    assert "workspace.windowActivated" in AW.KWIN_SCRIPT
    assert "workspace.activeWindow" in AW.KWIN_SCRIPT
    assert "callDBus" in AW.KWIN_SCRIPT
    assert AW.BUS_NAME in AW.KWIN_SCRIPT and AW.SCRIPT_NAME == "jarvis-focus-watcher"


def test_script_loaded_parses(monkeypatch):
    def fake_run(argv, **kw):
        assert argv[-1] == AW.SCRIPT_NAME
        return types.SimpleNamespace(returncode=0, stdout="true\n")

    assert AW.script_loaded(run=fake_run) is True

    def fake_run_false(argv, **kw):
        return types.SimpleNamespace(returncode=0, stdout="false\n")

    assert AW.script_loaded(run=fake_run_false) is False


def test_ensure_and_unload_script(tmp_path, monkeypatch):
    _reset()
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    calls = []
    loaded = {"v": False}

    def fake_qdbus(argv, timeout=8.0):
        calls.append(argv)
        if any("isScriptLoaded" in a for a in argv):
            return "true" if loaded["v"] else ""
        if any("loadScript" in a for a in argv):
            loaded["v"] = True
        if any("unloadScript" in a for a in argv):
            loaded["v"] = False
        return ""

    monkeypatch.setattr(AW, "_qdbus", fake_qdbus)
    assert AW.ensure_script() is True
    assert AW.script_path().read_text() == AW.KWIN_SCRIPT
    assert any(any("loadScript" in a for a in c) for c in calls)
    assert AW.unload_script() is True
    assert any(any("unloadScript" in a for a in c) for c in calls)


def test_kwin_script_pushes_window_list():
    assert "workspace.windowList" in AW.KWIN_SCRIPT
    assert '"Windows"' in AW.KWIN_SCRIPT
    assert "JSON.stringify" in AW.KWIN_SCRIPT
    assert "workspace.windowAdded" in AW.KWIN_SCRIPT
    assert "workspace.windowRemoved" in AW.KWIN_SCRIPT
    assert "captionChanged" in AW.KWIN_SCRIPT
    assert "minimizedChanged" in AW.KWIN_SCRIPT
    assert "desktopFileName" in AW.KWIN_SCRIPT
    assert "normalWindow" in AW.KWIN_SCRIPT
    assert "skipTaskbar" in AW.KWIN_SCRIPT
    # Same bus endpoint, same script name: one script, one listener.
    assert AW.KWIN_SCRIPT.count("callDBus") == 3
    # "Active" push untouched.
    assert "workspace.windowActivated" in AW.KWIN_SCRIPT
    assert "workspace.activeWindow" in AW.KWIN_SCRIPT


def _win_payload(items):
    return json.dumps(items)


def test_windows_routing_and_active_untouched():
    _reset()
    assert AW.handle_dbus_message(
        "Windows",
        [_win_payload([{"id": "a", "title": "t", "app": "x", "pid": 1}])],
    ) == "windows"
    assert [w["id"] for w in AW.windows()] == ["a"]
    assert AW.push_active() is None
    assert AW.handle_dbus_message("Active", ["T", "A", 7, "w1"]) == "active"
    assert AW.push_active() == {"title": "T", "app": "A", "pid": 7, "id": "w1"}
    # Windows store unchanged by the Active push.
    assert [w["id"] for w in AW.windows()] == ["a"]
    # Missing member keeps the old Active behavior.
    assert AW.handle_dbus_message(None, ["T2", "A2", 8, "w2"]) == "active"
    assert AW.push_active()["id"] == "w2"


def test_windows_parsing_filters_and_fail_soft():
    _reset()
    assert AW.parse_windows_payload("not json") == []
    assert AW.parse_windows_payload('{"id": "x"}') == []
    assert AW.parse_windows_payload("") == []
    got = AW.parse_windows_payload(
        _win_payload(
            [
                {"id": "keep", "title": " t ", "app": "brave", "pid": "42",
                 "desktop": "brave.desktop", "active": True, "minimized": 0},
                {"id": "self", "title": "orb", "app": "jarvis-shell", "pid": 9},
                {"title": "no id", "app": "x"},
                "junk",
            ]
        )
    )
    assert got == [
        {"id": "keep", "title": "t", "app": "brave", "pid": 42,
         "desktop": "brave.desktop", "active": True, "minimized": False}
    ]


def test_windows_order_stable_and_copy():
    _reset()
    AW.handle_dbus_message(
        "Windows", [_win_payload([{"id": "a"}, {"id": "b"}])]
    )
    # KWin re-sends in stacking order; first-seen order wins.
    AW.handle_dbus_message(
        "Windows", [_win_payload([{"id": "b"}, {"id": "a"}])]
    )
    assert [w["id"] for w in AW.windows()] == ["a", "b"]
    # Removal drops, re-add goes last.
    AW.handle_dbus_message("Windows", [_win_payload([{"id": "b"}])])
    assert [w["id"] for w in AW.windows()] == ["b"]
    AW.handle_dbus_message(
        "Windows", [_win_payload([{"id": "b"}, {"id": "a"}])]
    )
    assert [w["id"] for w in AW.windows()] == ["b", "a"]
    # Copy semantics: caller mutations never leak back in.
    first = AW.windows()
    first.append({"id": "evil"})
    first[0]["title"] = "evil"
    assert [w["id"] for w in AW.windows()] == ["b", "a"]
    assert AW.windows()[0]["title"] == ""


def test_startmenu_routes_to_helper(monkeypatch):
    _reset()
    calls = []
    monkeypatch.setattr(
        AW, "toggle_start_menu", lambda *a, **k: calls.append(1) or True)
    assert AW.handle_dbus_message("StartMenu", []) == "startmenu"
    assert calls == [1]
    # Neither store is touched by the menu call.
    assert AW.push_active() is None
    assert AW.windows() == []


def test_startmenu_fail_soft_still_replies(monkeypatch):
    _reset()

    def _boom(*a, **k):
        raise RuntimeError("shell down")

    monkeypatch.setattr(AW, "toggle_start_menu", _boom)
    assert AW.handle_dbus_message("StartMenu", []) == "startmenu"


def test_toggle_start_menu_argv():
    import sys

    calls = []

    def fake(argv, **kw):
        calls.append(list(argv))
        assert kw.get("timeout") == 10
        return object()

    # sys.executable exists, so the existence check passes.
    assert AW.toggle_start_menu(run=fake, bins=[sys.executable]) is True
    assert calls == [[sys.executable, "schoolmenu", "launcher"]]


def test_toggle_start_menu_search_order(monkeypatch):
    import shutil

    # Only the bare "jarvis-shell" resolves (repo binary + "jarvis" don't).
    monkeypatch.setattr(AW.Path, "exists", lambda self: False)
    monkeypatch.setattr(
        shutil, "which",
        lambda name: "/u/jarvis-shell" if name == "jarvis-shell" else None)
    calls = []

    def fake(argv, **kw):
        calls.append(list(argv))
        return object()

    assert AW.toggle_start_menu(run=fake) is True
    assert calls == [["jarvis-shell", "schoolmenu", "launcher"]]


def test_toggle_start_menu_none_found(monkeypatch):
    import shutil

    monkeypatch.setattr(AW.Path, "exists", lambda self: False)
    monkeypatch.setattr(shutil, "which", lambda name: None)

    def _boom(argv, **kw):
        raise AssertionError("must not run without a shell binary")

    assert AW.toggle_start_menu(run=_boom) is False


def test_school_geom_push_is_kept_and_bad_json_dropped():
    """The shell's measure script reports geometry for the entry transition."""
    AW._SCHOOL_GEOM.update(geom=None, at=0.0)
    assert AW.school_geom() is None
    raw = '{"nonce": "n1", "panel": 48, "same": true}'
    assert AW.handle_dbus_message("SchoolGeom", [raw]) == "schoolgeom"
    assert AW.school_geom() == {"nonce": "n1", "panel": 48, "same": True}
    AW.handle_dbus_message("SchoolGeom", ["{broken"])
    assert AW.school_geom()["nonce"] == "n1"
    AW._SCHOOL_GEOM["at"] = 0.0
    assert AW.school_geom() is None
