"""The bridge reports real action state and contains sandbox app launches."""

import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, "src")

import action_guard
import bridge


def test_route_duplicate_does_not_claim_pending_action_finished(monkeypatch):
    action_guard.claim("play_media", {"query": ""}, "model")
    monkeypatch.setattr(
        bridge, "_run_voice_action", lambda *args: pytest.fail("duplicate executed")
    )
    code, result = bridge.handle_route({"text": "play some music"})
    assert code == 200
    assert result["duplicate"] is True
    assert result["action"]["ok"] is None
    assert result["reply"] == "That action is still in progress, Sir."


def test_route_exception_releases_claim_for_retry(monkeypatch):
    def broken(*args):
        raise RuntimeError("test backend unavailable")

    monkeypatch.setattr(bridge, "_run_voice_action", broken)
    code, result = bridge.handle_route({"text": "play some music"})
    assert code == 200
    assert result["action"]["ok"] is False
    assert "test backend unavailable" in result["reply"]
    assert action_guard.claim("play_media", {"query": ""}, "model") is None


@pytest.mark.parametrize(
    "route", ["media", "fixed_app", "priority_app", "universal_app"]
)
def test_desktop_launches_apply_sandbox_argv_and_environment(monkeypatch, route):
    from system import core, desktop, launcher

    monkeypatch.setattr(desktop, "sandbox_display", lambda: ":99")
    sandbox_calls, launched = [], []

    def sandbox_launch(argv, display):
        sandbox_calls.append((argv, display))
        return [*argv, "sandbox-marker"], {"DISPLAY": display}

    monkeypatch.setattr(core, "_sandbox_launch", sandbox_launch, raising=False)
    monkeypatch.setattr(
        bridge.subprocess,
        "Popen",
        lambda argv, **kwargs: launched.append((argv, kwargs)),
    )
    monkeypatch.setattr(bridge, "_which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(launcher, "is_blocked", lambda *args: None)
    monkeypatch.setattr(launcher, "priority_app", lambda *args, **kwargs: None)
    if route == "media":
        result = bridge._play_media("")
    elif route == "fixed_app":
        result = bridge.run_phone_tool("open_app", {"app": "calculator"})
    elif route == "priority_app":
        monkeypatch.setattr(
            launcher,
            "priority_app",
            lambda *args, **kwargs: SimpleNamespace(
                argv=["brave-browser"], target="brave"
            ),
        )
        result = bridge.run_phone_tool("open_app", {"app": "brave"})
    else:
        monkeypatch.setattr(
            launcher,
            "resolve_launch",
            lambda *args, **kwargs: SimpleNamespace(
                argv=["test-app"], target="test-app", kind="app", reason="exact"
            ),
        )
        result = bridge._launch_anything("test-app")
    assert result["ok"] is True
    assert len(sandbox_calls) == len(launched) == 1
    assert sandbox_calls[0][1] == ":99"
    assert launched[0][0][-1] == "sandbox-marker"
    assert launched[0][1]["env"] == {"DISPLAY": ":99"}
