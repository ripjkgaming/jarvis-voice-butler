"""Productive/sentry routing regressions with no camera or desktop capture."""

import types

import pytest
from livekit.agents.llm import ToolError

import agent
import bridge
import watch_mode
from system.watch_tools import WatchTools


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_WATCH_MODE", "1")
    monkeypatch.delenv("JARVIS_WATCH_CAPTURE_HELPER", raising=False)
    import watch_capture_package

    monkeypatch.setattr(watch_capture_package, "installed_helper", lambda: None)
    import action_guard

    monkeypatch.setattr(action_guard, "_recent", [])


@pytest.mark.parametrize(
    "text",
    [
        "watch the computer",
        "Jarvis, watch the laptop",
        "could you watch my computer please",
    ],
)
def test_exact_watch_maps_to_productive_session(text):
    assert bridge._match_voice_tool(text) == (
        "watch_mode",
        {},
        "Watching the laptop, Sir.",
    )


@pytest.mark.parametrize(
    "text",
    [
        "what's open",
        "what is the active window",
        "look at my screen",
        "stop watch mode",
        "cancel watch mode",
        "don't watch the computer",
    ],
)
def test_other_desktop_and_stop_requests_do_not_start_watch(text):
    hit = bridge._match_voice_tool(text)
    assert hit is None or hit[0] != "watch_mode"
    assert hit is None or hit[:2] != ("computer_watch", {"action": "start"})


def test_realtime_assistant_has_watch_tool(monkeypatch):
    monkeypatch.setenv("JARVIS_PIPELINE", "realtime")
    assistant = agent.Assistant(browser=types.SimpleNamespace())
    assert "start_watch_mode" in {tool.id for tool in assistant.tools}
    assert agent._never_demote("start_watch_mode")


@pytest.mark.asyncio
async def test_watch_tool_starts_productive_watch_once(monkeypatch):
    calls = []

    def fake_launch():
        if not calls:
            calls.append("launch")
        return {"ok": True, "active": True, "state": "watching"}

    monkeypatch.setattr(watch_mode, "launch", fake_launch)
    import computer_watch
    import computer_watch_client

    monkeypatch.setattr(
        computer_watch,
        "start",
        lambda *a, **k: pytest.fail("agent observer must not start"),
    )
    monkeypatch.setattr(
        computer_watch_client,
        "request",
        lambda *a, **k: pytest.fail("watch start must not use the bridge"),
    )
    tool = WatchTools()
    context = types.SimpleNamespace(
        speech_handle=types.SimpleNamespace(id="watch-test")
    )
    first = await tool.start_watch_mode(context)
    second = await tool.start_watch_mode(context)
    assert calls == ["launch"]
    assert first["say"] == "Watching the laptop, Sir."
    assert second["say"]


@pytest.mark.asyncio
async def test_watch_tool_reports_failure(monkeypatch):
    monkeypatch.setattr(
        watch_mode,
        "launch",
        lambda: {"ok": False, "error": "Watch mode is switched off."},
    )
    with pytest.raises(ToolError, match="switched off"):
        await WatchTools().start_watch_mode(types.SimpleNamespace())


@pytest.mark.asyncio
async def test_watch_tool_refuses_remote(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "0")
    monkeypatch.setattr(watch_mode, "launch", lambda: pytest.fail("remote launch"))
    with pytest.raises(ToolError):
        await WatchTools().start_watch_mode(types.SimpleNamespace())


def test_actual_conversational_transcript_needs_model_watch_tool():
    # Caption log: conversational prefix prevents the deliberately anchored
    # bridge matcher from claiming this whole utterance.
    text = "That's very nice. Can you watch the computer?"
    assert bridge._match_voice_tool(text) is None
    assistant = agent.Assistant(browser=types.SimpleNamespace())
    assert "start_watch_mode" in {tool.id for tool in assistant.tools}
    assert '"watch the computer"' in assistant.instructions


@pytest.mark.parametrize(
    "text", ["guard my computer", "I'm leaving", "start sentry mode"]
)
def test_security_watch_never_routes_to_fullscreen_guard(text):
    hit = bridge._match_voice_tool(text)
    assert hit is None or hit[0] != "watch_mode"


@pytest.mark.parametrize(
    "text,action",
    [
        ("stop watch mode", "stop"),
        ("watch mode status", "status"),
        ("what happened while watching", "briefing"),
    ],
)
def test_productive_controls_route(text, action):
    assert bridge._match_voice_tool(text)[:2] == ("computer_watch", {"action": action})


def test_bridge_returns_actual_briefing(monkeypatch):
    import computer_watch

    monkeypatch.setattr(
        computer_watch, "briefing", lambda: "Tests failed; inspect the first assertion."
    )
    monkeypatch.setattr(computer_watch, "note_context", lambda text: None)
    code, body = bridge.handle_route({"text": "what happened while watching"})
    assert code == 200
    assert body["reply"] == "Tests failed; inspect the first assertion."


@pytest.mark.asyncio
async def test_guard_tool_refuses_without_any_desktop_action(monkeypatch):
    import computer_watch

    calls = []
    monkeypatch.setattr(
        computer_watch, "stop", lambda: calls.append("stop") or {"ok": True}
    )
    monkeypatch.setattr(
        watch_mode, "launch", lambda: calls.append("guard") or {"ok": True}
    )
    monkeypatch.setattr(
        __import__("computer_watch_client"), "stop", computer_watch.stop
    )
    with pytest.raises(ToolError, match="cannot be started"):
        await WatchTools().start_guard_mode(types.SimpleNamespace())
    assert calls == []
    assert "start_guard_mode" not in {tool.id for tool in WatchTools().tools}


def test_productive_owner_tools_cannot_run_in_guest(monkeypatch):
    import computer_watch

    watch_mode.enter_guest()
    monkeypatch.setattr(
        computer_watch, "start", lambda **kwargs: pytest.fail("guest started watch")
    )
    _, body = bridge.handle_route({"text": "watch the computer", "guest": False})
    assert body["action"]["denied"] == "guest mode"


@pytest.mark.parametrize("action", ["start", "status", "briefing"])
def test_bridge_watch_controls_require_local_owner(monkeypatch, action):
    import computer_watch

    monkeypatch.setattr(computer_watch, "allowed", lambda: False)
    result = bridge.run_phone_tool("computer_watch", {"action": action})
    assert result["ok"] is False
    assert "owner" in result["error"]


def test_bridge_failed_watch_start_does_not_claim_watching(monkeypatch):
    import computer_watch

    monkeypatch.setattr(
        computer_watch,
        "start",
        lambda: {"ok": False, "error": "Previous observation is stopping."},
    )
    result = bridge.run_phone_tool("computer_watch", {"action": "start"})
    assert result["ok"] is False
    assert "say" not in result


@pytest.mark.asyncio
async def test_voice_controls_share_bridge_authority(monkeypatch):
    import computer_watch
    import watch_control

    monkeypatch.setattr(watch_control, "controls", watch_control.WatchControls(owner=lambda: True, target=lambda: {"id": "test", "app": "Terminal", "pid": 123}))

    monkeypatch.setattr(computer_watch, "start", lambda: {"ok": True, "active": True})
    monkeypatch.setattr(computer_watch, "stop", lambda: {"ok": True, "active": False})
    monkeypatch.setattr(
        computer_watch, "status", lambda: {"active": True, "task": "tests"}
    )
    monkeypatch.setattr(computer_watch, "briefing", lambda: "Following tests.")
    server, _ = bridge._run_in_thread(token="watch-test-token")
    monkeypatch.setenv("JARVIS_BRIDGE_PORT", str(server.server_port))
    monkeypatch.setattr(
        __import__("wizard.gate", fromlist=["bridge_token"]),
        "bridge_token",
        lambda: "watch-test-token",
    )
    try:
        code, body = bridge.handle_route({"text": "watch the computer"})
        assert code == 200 and body["action"]["ok"]
        assert body["confirmation_required"]
        result = await WatchTools().watch_mode_status(types.SimpleNamespace())
        assert result["task"] == "tests"
        assert (
            "Stopped"
            in (await WatchTools().stop_watch_mode(types.SimpleNamespace()))["say"]
        )
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
async def test_voice_watch_does_not_fallback_when_bridge_unavailable(monkeypatch):
    import computer_watch
    import computer_watch_client

    launches = []
    monkeypatch.setattr(
        watch_mode,
        "launch",
        lambda: launches.append("launch") or {"ok": True, "state": "watching"},
    )
    monkeypatch.setattr(
        computer_watch, "start", lambda: pytest.fail("agent observer started")
    )
    monkeypatch.setattr(
        computer_watch_client,
        "request",
        lambda *a, **k: {"ok": False, "error": "bridge unavailable"},
    )
    result = await WatchTools().start_watch_mode(types.SimpleNamespace())
    assert result["say"] == "Watching the laptop, Sir."
    assert launches == ["launch"]


def test_watch_stop_bypasses_running_context(monkeypatch):
    import threading

    import computer_watch_client

    entered, release = threading.Event(), threading.Event()
    calls = []

    def call(action, **kwargs):
        if action == "context":
            entered.set()
            release.wait(2)
        calls.append(action)
        return {"ok": True}

    monkeypatch.setattr(computer_watch_client, "_request", call)
    computer_watch_client.note_context("Follow the running tests")
    assert entered.wait(1)
    stopper = threading.Thread(target=computer_watch_client.stop)
    stopper.start()
    stopper.join(2)
    assert not stopper.is_alive()
    assert calls == ["stop"]
    release.set()
    computer_watch_client._transport.submit(lambda: None).result(timeout=2)
    assert calls == ["stop", "context"]


def test_watch_stop_cancels_pending_context(monkeypatch):
    import threading

    import computer_watch_client

    entered, release = threading.Event(), threading.Event()
    calls = []

    def call(action, **kwargs):
        if action == "context":
            entered.set()
            release.wait(2)
        calls.append(action)
        return {"ok": True}

    monkeypatch.setattr(computer_watch_client, "_request", call)
    computer_watch_client.note_context("first")
    assert entered.wait(1)
    computer_watch_client.note_context("queued")
    computer_watch_client.note_context("also queued")
    cancelled = threading.Event()
    with computer_watch_client._control_lock:
        pending = next(
            item
            for item in computer_watch_client._pending_context
            if not item.running()
        )
        pending.add_done_callback(
            lambda future: cancelled.set() if future.cancelled() else None
        )
    stopper = threading.Thread(target=computer_watch_client.stop)
    stopper.start()
    assert cancelled.wait(1)
    stopper.join(2)
    assert not stopper.is_alive()
    assert calls == ["stop"]
    release.set()
    computer_watch_client._transport.submit(lambda: None).result(timeout=2)
    assert calls == ["stop", "context"]


def test_bridge_watch_mode_action_launches_card(monkeypatch):
    calls = []
    monkeypatch.setattr(
        watch_mode,
        "launch",
        lambda: calls.append("launch") or {"ok": True, "state": "watching"},
    )
    result = bridge.run_phone_tool("watch_mode", {})
    assert result["ok"] is True
    assert result["state"] == "watching"
    assert result["say"] == "Watching the laptop, Sir."
    assert calls == ["launch"]
    monkeypatch.setattr(
        watch_mode,
        "launch",
        lambda: {"ok": False, "error": "Watch mode is switched off."},
    )
    result = bridge.run_phone_tool("watch_mode", {})
    assert result["ok"] is False
    assert "switched off" in result["error"]


def test_suite_blocks_unmocked_guard_launch_and_session_lock():
    with pytest.raises(AssertionError, match="fake security guard"):
        watch_mode.launch()
    with pytest.raises(AssertionError, match="fake session lock"):
        watch_mode.fail_safe_lock()
