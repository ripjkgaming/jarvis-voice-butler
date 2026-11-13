"""Maximise / full screen / restore / attention for windows (window_ctl).

Hermetic: KWin is faked through an injected runner that answers the
WindowsRunner Match (dbus-send), KWin getWindowInfo and Scripting calls
(qdbus). The one-shot KWin script that is loaded is read back from disk so
the command construction is proven, not assumed.
"""

from __future__ import annotations

import subprocess
import types

import pytest
from jarvis_rt import ToolError

from system import kwin_windows, window_ctl, window_layout
from system.window_tools import WindowTools

UUID = "{38292273-416c-4f8d-b6f1-487f0325d866}"

MATCH_BRAVE = f"""method return time=1 sender=:1.9 -> destination=:1.2 serial=5 reply_serial=2
   array [
      struct {{
         string "0_{UUID}"
         string "Less Talk, More Action - YouTube - Brave"
         string "brave-browser"
         int32 100
         double 0.8
      }}
   ]
"""

MATCH_NONE = (
    "method return time=1 sender=:1.9 -> destination=:1.2 serial=5\n   array [\n   ]\n"
)

# Shape copied from a live `qdbus org.kde.KWin /KWin org.kde.KWin.getWindowInfo`.
INFO_PLAIN = """caption: Less Talk, More Action - YouTube - Brave
desktopFile: brave-browser
fullscreen: false
height: 1040
maximizeHorizontal: 0
maximizeVertical: 0
minimized: false
resourceClass: brave-browser
uuid: {38292273-416c-4f8d-b6f1-487f0325d866}
width: 1920
x: 0
y: 0
"""

INFO_MAXIMISED = INFO_PLAIN.replace(
    "maximizeHorizontal: 0", "maximizeHorizontal: 2"
).replace("maximizeVertical: 0", "maximizeVertical: 1")
INFO_FULLSCREEN = INFO_PLAIN.replace("fullscreen: false", "fullscreen: true")
INFO_MINIMISED = INFO_PLAIN.replace("minimized: false", "minimized: true")


def _cp(argv, code=0, out=""):
    return subprocess.CompletedProcess(argv, code, out, "")


def make_run(match_out: str, info_out: str | None, info_code: int = 0):
    """Fake subprocess.run routing dbus-send Match, qdbus getWindowInfo and
    KWin Scripting. info_out None means getWindowInfo fails (unreadable)."""
    calls: list[list[str]] = []

    def run(argv, **kw):
        calls.append(list(argv))
        if argv[0] == "dbus-send":
            return _cp(argv, 0, match_out)
        if argv[0] == "qdbus" and "org.kde.KWin.getWindowInfo" in argv:
            if info_out is None:
                return _cp(argv, info_code, "")
            return _cp(argv, 0, info_out)
        if argv[0] == "qdbus" and "org.kde.kwin.Scripting.loadScript" in argv:
            return _cp(argv, 0, "7")
        return _cp(argv, 0, "")

    return run, calls


def no_sleep(_s: float) -> None:
    return None


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    # run_script sleeps 0.4s while the script runs; skip that in tests.
    monkeypatch.setattr(
        window_layout,
        "time",
        types.SimpleNamespace(sleep=no_sleep, time=lambda: 0.0),
    )
    monkeypatch.setattr(kwin_windows, "available", lambda env=None: True)
    return tmp_path


def loaded_script(tmp_path) -> str:
    return (tmp_path / "kwin" / f"{window_layout.SCRIPT_NAME}-state.js").read_text()


# --- pure helpers ---


def test_state_script_targets_the_uuid_and_mode() -> None:
    js = window_ctl.state_script(UUID, "maximize")
    assert '"38292273-416c-4f8d-b6f1-487f0325d866"' in js  # braces and case dropped
    assert "setMaximize(true, true)" in js
    assert "w.minimized = false" in js and "workspace.activeWindow = w" in js
    assert 'var mode = "fullscreen";' in window_ctl.state_script(UUID, "fullscreen")
    assert 'var mode = "restore";' in window_ctl.state_script(UUID, "restore")
    assert 'var mode = "attention";' in window_ctl.state_script(UUID, "attention")


def test_parse_window_info_handles_colons_in_captions() -> None:
    info = window_ctl.parse_window_info(INFO_MAXIMISED)
    assert info["caption"] == "Less Talk, More Action - YouTube - Brave"
    assert info["maximizeVertical"] == "1" and info["fullscreen"] == "false"
    assert window_ctl.parse_window_info("") == {}


def test_state_predicates() -> None:
    assert window_ctl._state_ok(
        "maximize", window_ctl.parse_window_info(INFO_MAXIMISED)
    )
    assert not window_ctl._state_ok(
        "maximize", window_ctl.parse_window_info(INFO_PLAIN)
    )
    assert window_ctl._state_ok(
        "fullscreen", window_ctl.parse_window_info(INFO_FULLSCREEN)
    )
    assert window_ctl._state_ok("restore", window_ctl.parse_window_info(INFO_PLAIN))
    assert not window_ctl._state_ok(
        "restore", window_ctl.parse_window_info(INFO_MAXIMISED)
    )
    assert window_ctl._state_ok("attention", window_ctl.parse_window_info(INFO_PLAIN))
    assert not window_ctl._state_ok(
        "attention", window_ctl.parse_window_info(INFO_MINIMISED)
    )


def test_restore_name_clash_keeps_the_old_restore() -> None:
    # window_ctl.restore puts a previous window back in front (play_media);
    # the size-state helper must not replace it.
    assert window_ctl.restore is not window_ctl.restore_window
    assert callable(window_ctl.restore)


# --- command construction and read-back ---


def test_maximize_matches_like_focus_loads_script_and_confirms(tmp_path) -> None:
    run, calls = make_run(MATCH_BRAVE, INFO_MAXIMISED)
    ok, say = window_ctl.maximize(
        "browser", run=run, active=lambda: "Brave", sleep=no_sleep
    )
    assert ok and say == "Less Talk, More Action - YouTube - Brave is maximised."
    # The literal query hit, so the alias is not needed (same order as focus).
    matches = [c[-1] for c in calls if c[0] == "dbus-send" and "Match" in " ".join(c)]
    assert matches == ["string:browser"]
    loads = [c for c in calls if "org.kde.kwin.Scripting.loadScript" in c]
    assert loads and loads[0][-2].endswith("jarvis-layout-state.js")
    js = loaded_script(tmp_path)
    assert 'var mode = "maximize";' in js and "38292273" in js


def test_alias_is_tried_after_the_literal_query_misses(tmp_path) -> None:
    run0, _ = make_run(MATCH_BRAVE, INFO_MAXIMISED)

    def run(argv, **kw):
        if argv[0] == "dbus-send" and argv[-1] == "string:browser":
            return _cp(argv, 0, MATCH_NONE)
        return run0(argv, **kw)

    ok, _ = window_ctl.maximize(
        "browser", run=run, active=lambda: "Brave", sleep=no_sleep
    )
    assert ok


def test_fullscreen_uses_the_fullscreen_state(tmp_path) -> None:
    run, _ = make_run(MATCH_BRAVE, INFO_FULLSCREEN)
    ok, say = window_ctl.maximize(
        "brave", fullscreen=True, run=run, active=lambda: "Brave", sleep=no_sleep
    )
    assert ok and "is full screen." in say
    assert 'var mode = "fullscreen";' in loaded_script(tmp_path)


def test_maximize_reports_honestly_when_kwin_disagrees(tmp_path) -> None:
    run, _ = make_run(MATCH_BRAVE, INFO_PLAIN)
    ok, say = window_ctl.maximize(
        "brave", run=run, active=lambda: "Brave", sleep=no_sleep
    )
    assert ok is False and "reports it otherwise" in say


def test_unreadable_state_is_unconfirmed_not_failed(tmp_path) -> None:
    run, _ = make_run(MATCH_BRAVE, None, info_code=1)
    ok, say = window_ctl.maximize(
        "brave", run=run, active=lambda: "Brave", sleep=no_sleep
    )
    assert ok is True and "can't read" in say


def test_maximize_unknown_window_is_a_failure(tmp_path) -> None:
    run, calls = make_run(MATCH_NONE, None)
    ok, say = window_ctl.maximize("zzz", run=run, active=lambda: "", sleep=no_sleep)
    assert ok is False and "can't find" in say
    assert not any("loadScript" in " ".join(c) for c in calls)


def test_maximize_fails_when_kwin_does_not_load_the_script(tmp_path) -> None:
    def run(argv, **kw):
        if argv[0] == "dbus-send":
            return _cp(argv, 0, MATCH_BRAVE)
        if "org.kde.kwin.Scripting.loadScript" in argv:
            return _cp(argv, 0, "-1")
        return _cp(argv, 0, "")

    ok, say = window_ctl.maximize(
        "brave", run=run, active=lambda: "Brave", sleep=no_sleep
    )
    assert ok is False and "didn't take" in say


def test_maximize_that_did_not_come_forward_is_reported(tmp_path) -> None:
    run, _ = make_run(MATCH_BRAVE, INFO_MAXIMISED)
    ok, say = window_ctl.maximize(
        "brave", run=run, active=lambda: "Chemistry notes - Kate", sleep=no_sleep
    )
    assert ok is False and "not in front" in say


def test_restore_window_unmaximises_and_activates(tmp_path) -> None:
    run, _ = make_run(MATCH_BRAVE, INFO_PLAIN)
    ok, say = window_ctl.restore_window(
        "brave", run=run, active=lambda: "Brave", sleep=no_sleep
    )
    assert ok and "back to normal size" in say
    js = loaded_script(tmp_path)
    assert 'var mode = "restore";' in js


def test_attention_unminimises_flags_and_confirms_front(tmp_path) -> None:
    run, _ = make_run(MATCH_BRAVE, INFO_PLAIN)
    ok, say = window_ctl.attention(
        "brave", run=run, active=lambda: "Brave", sleep=no_sleep
    )
    assert ok and say == "Less Talk, More Action - YouTube - Brave is in front."
    js = loaded_script(tmp_path)
    assert 'var mode = "attention";' in js


def test_attention_behind_others_is_reported(tmp_path) -> None:
    run, _ = make_run(MATCH_BRAVE, INFO_PLAIN)
    ok, say = window_ctl.attention(
        "brave", run=run, active=lambda: "Chemistry notes - Kate", sleep=no_sleep
    )
    assert ok is False and "not in front" in say


def test_non_plasma_falls_back_to_wmctrl(monkeypatch) -> None:
    monkeypatch.setattr(kwin_windows, "available", lambda env=None: False)
    calls: list[list[str]] = []

    def run(argv, **kw):
        calls.append(list(argv))
        return _cp(argv, 0, "")

    ok, say = window_ctl.maximize(
        "spotify",
        run=run,
        which=lambda n: "/usr/bin/wmctrl",
        active=lambda: "Spotify Premium",
        sleep=no_sleep,
    )
    assert ok is True and "can't read the state back" in say
    assert [
        "wmctrl",
        "-r",
        "spotify",
        "-b",
        "add,maximized_vert,maximized_horz",
    ] in calls


def test_non_plasma_without_wmctrl_says_so(monkeypatch) -> None:
    monkeypatch.setattr(kwin_windows, "available", lambda env=None: False)
    ok, say = window_ctl.maximize(
        "spotify", run=lambda a, **k: _cp(a), which=lambda n: None, sleep=no_sleep
    )
    assert ok is False and "neither is here" in say


def test_empty_title_asks_which_window() -> None:
    assert window_ctl.maximize("  ") == (False, "Which window, Sir?")
    assert window_ctl.attention("") == (False, "Which window, Sir?")


# --- tools ---


def test_tools_are_registered() -> None:
    ids = [t.id for t in WindowTools().tools]
    for name in ("maximize_window", "restore_window", "bring_to_attention"):
        assert name in ids


@pytest.mark.asyncio
async def test_maximize_window_tool(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(
        window_ctl,
        "maximize",
        lambda t, fullscreen=False, **k: (
            seen.append((t, fullscreen)) or (True, "Terminal is maximised.")
        ),
    )
    out = await WindowTools.maximize_window(WindowTools(), None, title="terminal")  # type: ignore[arg-type]
    assert out["say"] == "Terminal is maximised." and seen == [("terminal", False)]
    out = await WindowTools.maximize_window(
        WindowTools(), None, title="spotify", fullscreen=True
    )  # type: ignore[arg-type]
    assert seen[-1] == ("spotify", True)
    monkeypatch.setattr(
        window_ctl, "maximize", lambda t, fullscreen=False, **k: (False, "nope")
    )
    with pytest.raises(ToolError):
        await WindowTools.maximize_window(WindowTools(), None, title="zzz")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_restore_window_tool(monkeypatch) -> None:
    monkeypatch.setattr(
        window_ctl,
        "restore_window",
        lambda t, **k: (True, "Spotify is back to normal size."),
    )
    out = await WindowTools.restore_window(WindowTools(), None, title="spotify")  # type: ignore[arg-type]
    assert out["say"] == "Spotify is back to normal size."
    monkeypatch.setattr(window_ctl, "restore_window", lambda t, **k: (False, "nope"))
    with pytest.raises(ToolError):
        await WindowTools.restore_window(WindowTools(), None, title="zzz")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_bring_to_attention_tool_routes_maximize(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        window_ctl,
        "attention",
        lambda t, **k: calls.append(("attention", t)) or (True, "a"),
    )
    monkeypatch.setattr(
        window_ctl,
        "maximize",
        lambda t, fullscreen=False, **k: calls.append(("maximize", t)) or (True, "m"),
    )
    await WindowTools.bring_to_attention(WindowTools(), None, title="browser")  # type: ignore[arg-type]
    await WindowTools.bring_to_attention(
        WindowTools(), None, title="browser", maximize=True
    )  # type: ignore[arg-type]
    assert calls == [("attention", "browser"), ("maximize", "browser")]
    monkeypatch.setattr(window_ctl, "attention", lambda t, **k: (False, "not in front"))
    with pytest.raises(ToolError):
        await WindowTools.bring_to_attention(WindowTools(), None, title="browser")  # type: ignore[arg-type]
