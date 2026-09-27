"""Tests for the RustDesk remote-session voice tools (no real processes)."""

import json
import sys

sys.path.insert(0, "src")

import bridge


def test_remote_start_patterns():
    for phrase in (
        "start a remote session",
        "start the remote session",
        "start remote session",
        "open a remote session",
        "begin a remote session",
        "begin the remote desktop session",
        "remote into my laptop",
        "remote into the pc",
        "remote in to my computer",
        "remote session",
    ):
        matched = bridge._match_voice_tool(phrase)
        assert matched is not None, phrase
        tool, _args, reply = matched
        assert tool == "remote_start", phrase
        assert reply == "Remote session ready, Sir."


def test_remote_stop_patterns():
    for phrase in (
        "end remote session",
        "end the remote session",
        "stop the remote session",
        "stop remote desktop session",
        "close the remote session",
    ):
        matched = bridge._match_voice_tool(phrase)
        assert matched is not None, phrase
        tool, _args, reply = matched
        assert tool == "remote_stop", phrase
        assert reply == "Remote session closed, Sir."


def test_universal_launcher_not_swallowed():
    matched = bridge._match_voice_tool("start spotify")
    assert matched is not None
    assert matched[0] == "open_app"
    assert matched[1] == {"app": "spotify"}


def test_remote_not_guest_allowed():
    for tool in ("remote_start", "remote_stop", "remote_status"):
        assert tool not in bridge._GUEST_VOICE_TOOLS


def test_guest_denied_route_and_chat(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        bridge, "run_phone_tool", lambda tool, args: calls.append(tool) or {"ok": True}
    )
    code, payload = bridge.handle_route(
        {"text": "start a remote session", "guest": True}
    )
    assert code == 200
    assert payload["reply"] == bridge._GUEST_REFUSAL
    assert payload["action"] == {
        "tool": "remote_start",
        "ok": False,
        "denied": "guest mode",
    }
    assert calls == []
    code, payload = bridge.handle_chat(
        {"text": "stop the remote session", "guest": True}
    )
    assert code == 200
    assert payload["reply"] == bridge._GUEST_REFUSAL
    assert payload["action"]["denied"] == "guest mode"


def test_handle_route_remote_start_action_has_host_port(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "run_phone_tool",
        lambda tool, args: {
            "ok": True,
            "running": True,
            "port": 21118,
            "host": "100.1.2.3",
        },
    )
    code, payload = bridge.handle_route({"text": "start a remote session"})
    assert code == 200
    assert payload["reply"] == "Remote session ready, Sir."
    assert payload["action"] == {
        "tool": "remote_start",
        "ok": True,
        "host": "100.1.2.3",
        "port": 21118,
    }


def test_run_phone_tool_parses_script_json_and_host(monkeypatch):
    seen: list = []

    def fake_run(argv, timeout=10.0):
        seen.append((list(argv), timeout))
        if argv[0] == "tailscale":
            return 0, "100.1.2.3\n", ""
        assert argv[0].endswith("remote_session.sh")
        assert timeout == 15
        assert isinstance(argv, list)
        verb = argv[-1]
        if verb == "start":
            return 0, json.dumps({"ok": True, "running": True, "port": 21118}), ""
        if verb == "stop":
            return 0, json.dumps({"ok": True, "running": False}), ""
        return 0, json.dumps({"ok": True, "running": False}), ""

    monkeypatch.setattr(bridge, "_run", fake_run)
    res = bridge.run_phone_tool("remote_start", {})
    assert res == {"ok": True, "running": True, "port": 21118, "host": "100.1.2.3"}
    assert seen[0][1] == 15
    res = bridge.run_phone_tool("remote_stop", {})
    assert res["ok"] is True and res["running"] is False
    assert res["host"] == "100.1.2.3"
    # tailscale absent -> no host key, still ok
    monkeypatch.setattr(
        bridge,
        "_run",
        lambda argv, timeout=10.0: (0, '{"ok":true,"running":false}', ""),
    )
    res = bridge.run_phone_tool("remote_status", {})
    assert res == {"ok": True, "running": False}
