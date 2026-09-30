"""Test isolation for the persistent Brave profiles.

Every test gets throwaway profile dirs so the suite never touches the
real ~/.jarvis/brave-profile and never trips Chromium's single-process
profile lock when several managers launch in one test.
"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_browser_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_BROWSER_PROFILE", str(tmp_path / "brave-profile"))
    monkeypatch.setenv(
        "JARVIS_BROWSER_RESEARCH_PROFILE", str(tmp_path / "brave-research-profile")
    )


@pytest.fixture(autouse=True)
def _isolated_actions_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep test tool calls out of Sir's real ~/.jarvis/actions.log (the
    HUD activity feed tails it)."""
    import system

    monkeypatch.setattr(system, "LOG_PATH", tmp_path / "actions.log")
    monkeypatch.setenv("JARVIS_ACTIONS_LOG", str(tmp_path / "actions.log"))


@pytest.fixture(autouse=True)
def _no_real_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never spend Sir's Pro usage from tests: headless Claude is off unless
    a test opts in (and stubs the runner)."""
    monkeypatch.setenv("JARVIS_CLAUDE", "0")
    # Nor the OpenRouter key: WhatsApp's analyst/fallback stay off unless a
    # test opts in with a fake chat function.
    monkeypatch.setenv("JARVIS_WA_ANALYST", "0")
    monkeypatch.setenv("JARVIS_WA_OPENROUTER", "0")
    monkeypatch.setenv("JARVIS_WA_VISION", "0")
    monkeypatch.setenv("JARVIS_EMAIL_EVENTS", "0")


@pytest.fixture(autouse=True)
def _isolated_school_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests off Sir's real ~/.jarvis/mode.json (school mode).

    src/school.py reads $JARVIS_HOME/mode.json and mtime-caches it in
    school._cache, so without isolation a machine that is really in
    school mode leaks into every test (loud-action guards, VAD kwargs).
    Every test gets a throwaway JARVIS_HOME plus a reset cache; a test
    that needs its own mode sets JARVIS_HOME itself (its own patch wins,
    applied after this fixture).
    """
    import school

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis-home"))
    school._cache.update(path=None, mtime=None, mode=school.NORMAL)
    school._confirmed_at.update(at=0.0, used=None)
    school._asked_at.update(at=0.0)


@pytest.fixture(autouse=True)
def _no_real_dbus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touch the live session D-Bus from tests.

    A real active_window.ensure_listener() RequestNames org.jarvis.Focus
    and loads/unloads the real KWin script "jarvis-focus-watcher" — while
    pytest runs, the live bridge loses its window feed (the taskbar shows
    0 open apps). So every test gets fakes; a test that needs the real
    logic monkeypatches it back itself (its own patch wins, applied
    after this fixture).
    """
    import active_window

    # Listener thread + bus name: tests get "no listener" by default.
    monkeypatch.setattr(active_window, "ensure_listener", lambda: False)
    monkeypatch.setattr(active_window, "_listener_loop",
                         lambda stop: None)
    # KWin scripting traffic (load/start/unload/isScriptLoaded) goes
    # through _qdbus: answer "not loaded" so ensure/unload stay no-ops
    # unless a test stubs _qdbus itself.
    monkeypatch.setattr(active_window, "_qdbus",
                         lambda argv, timeout=8.0: "")
