"""Tests for src/active_window.py: parsing, cache, KWin script wiring."""

import types

import active_window as AW


def _reset():
    AW._cache.update(at=0.0, value=None)
    AW._push.update(at=0.0, value=None)
    AW._listener_started = False


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
