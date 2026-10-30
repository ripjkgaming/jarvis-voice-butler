import pytest

import watch_control


@pytest.fixture(autouse=True)
def isolated_native_package(monkeypatch):
    import watch_capture_package

    monkeypatch.delenv("JARVIS_WATCH_CAPTURE_HELPER", raising=False)
    monkeypatch.setattr(watch_capture_package, "installed_helper", lambda: None)


def test_disclosure_does_not_capture_and_confirmation_is_scoped():
    calls = []
    target = {"id": "10", "app": "Terminal", "pid": 123, "title": "Tests"}
    now = [100.0]
    controls = watch_control.WatchControls(
        owner=lambda: True,
        target=lambda: dict(target),
        clock=lambda: now[0],
        start=lambda **kw: calls.append(kw) or {"ok": True, "active": True},
    )
    disclosure = controls.handle("watch the computer", "voice-one")
    assert "Gemini" in disclosure["say"]
    assert calls == []
    assert not controls.handle("confirm visual watch", "voice-two")["ok"]
    assert controls.handle("watch the computer", "voice-one")["confirmation_required"]
    assert controls.handle("confirm visual watch", "voice-one")["ok"]
    assert len(calls) == 1
    assert calls[0]["consent"].provider == "gemini"
    assert not controls.handle("confirm visual watch", "voice-one")["ok"]


def test_changed_target_expiry_and_owner_loss_revoke():
    target = {"id": "10", "app": "Terminal", "pid": 123}
    now = [100.0]
    owner = [True]
    calls = []
    controls = watch_control.WatchControls(
        owner=lambda: owner[0],
        target=lambda: dict(target),
        clock=lambda: now[0],
        start=lambda **kw: calls.append(kw),
    )
    controls.handle("watch the computer", "owner")
    target["id"] = "11"
    assert not controls.handle("confirm visual watch", "owner")["ok"]
    controls.handle("watch the computer", "owner")
    now[0] += 121
    assert not controls.handle("confirm visual watch", "owner")["ok"]
    controls.handle("watch the computer", "owner")
    owner[0] = False
    assert not controls.handle("confirm visual watch", "owner")["ok"]
    owner[0] = True
    assert not controls.handle("confirm visual watch", "owner")["ok"]
    assert calls == []


def test_negation_never_confirms():
    assert watch_control.confirmation_command("do not confirm visual watch") is False
    assert watch_control.confirmation_command("Jarvis, confirm visual watch") is True


def test_bridge_trusted_route_proposes_but_model_tool_cannot_confirm(monkeypatch):
    import bridge
    import computer_watch

    controls = watch_control.WatchControls(
        owner=lambda: True, target=lambda: {"id": "test", "app": "Terminal", "pid": 123}
    )
    monkeypatch.setattr(watch_control, "controls", controls)
    monkeypatch.setattr(bridge, "_effective_guest", lambda _: False)
    monkeypatch.setattr(computer_watch, "allowed", lambda: True)
    code, result = bridge.handle_route({"text": "watch the computer"})
    assert code == 200 and result["confirmation_required"]
    assert (
        bridge.run_phone_tool("computer_watch", {"action": "confirm", "consent": True})[
            "ok"
        ]
        is False
    )
    code, result = bridge.handle_chat({"text": "stop watching"})
    assert code == 200 and result["ok"]


def test_client_trusted_control_uses_distinct_endpoint(monkeypatch):
    import io
    import json

    import computer_watch_client
    import screen_understanding
    from wizard import gate

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(screen_understanding, "local_owner_authorized", lambda: True)
    monkeypatch.setattr(gate, "bridge_token", lambda: "isolated-token")
    calls = []

    def transport(request, timeout):
        calls.append((request.full_url, json.loads(request.data)))
        return io.BytesIO(b'{"ok":true}')

    monkeypatch.setattr(computer_watch_client.urllib.request, "urlopen", transport)
    assert computer_watch_client.trusted_user_control(
        "confirm visual watch", "voice-test"
    )["ok"]
    assert calls[0][0].endswith("/watch-control")
    assert calls[0][1]["text"] == "confirm visual watch"
    assert calls[0][1]["session_id"] == "voice-test"
    assert "transport_epoch" in calls[0][1]


def test_exact_owner_command_sequence_is_consumed_without_model(monkeypatch):
    import bridge
    import computer_watch

    starts = []
    controls = watch_control.WatchControls(
        owner=lambda: True,
        target=lambda: {"id": "123", "app": "Terminal", "pid": 777},
        start=lambda **kw: (
            starts.append(kw)
            or {"ok": True, "active": True, "screen_observation": True}
        ),
    )
    monkeypatch.setattr(watch_control, "controls", controls)
    monkeypatch.setattr(bridge, "_effective_guest", lambda _: False)
    monkeypatch.setattr(
        bridge,
        "_match_voice_tool",
        lambda _: (_ for _ in ()).throw(
            AssertionError("ordinary model/tool route used")
        ),
    )
    monkeypatch.setattr(
        computer_watch,
        "status",
        lambda: {"active": True, "state": "watching", "screen_observation": True},
    )
    monkeypatch.setattr(computer_watch, "stop", lambda: {"ok": True, "active": False})
    assert bridge.handle_route({"text": "watch the computer"})[1][
        "confirmation_required"
    ]
    assert starts == []
    assert bridge.handle_chat({"text": "confirm visual watch"})[1]["ok"]
    assert starts[0]["expected_target"] == ("123", "Terminal", 777)
    assert bridge.handle_route({"text": "watch mode status"})[1]["screen_observation"]
    assert bridge.handle_chat({"text": "stop watching"})[1]["active"] is False


def test_authenticated_stop_survives_owner_loss(monkeypatch):
    import computer_watch

    calls = []
    monkeypatch.setattr(
        computer_watch,
        "stop",
        lambda: (
            calls.append("stopped") or {"ok": True, "active": False, "task": "private"}
        ),
    )
    controls = watch_control.WatchControls(owner=lambda: False)
    result = controls.handle("stop watching", "any-authenticated-session")
    assert result["ok"] and calls == ["stopped"]
    assert "task" not in result


def test_stop_cancels_queued_start_and_confirmation(monkeypatch):
    import threading

    import computer_watch_client

    entered, release = threading.Event(), threading.Event()
    calls = []

    def transport(action, **kwargs):
        if action == "context":
            entered.set()
            release.wait(2)
        calls.append((action, kwargs.get("user_text", "")))
        return {"ok": True}

    monkeypatch.setattr(computer_watch_client, "_request", transport)
    monkeypatch.setattr(
        __import__("screen_understanding"), "local_owner_authorized", lambda: True
    )
    computer_watch_client.note_context("blocking test context")
    assert entered.wait(1)
    first = threading.Thread(target=lambda: computer_watch_client.request("start"))
    second = threading.Thread(
        target=lambda: computer_watch_client.trusted_user_control(
            "confirm visual watch", "test-owner"
        )
    )
    first.start()
    second.start()
    # Acquire the lock after submitters have had an explicit scheduling turn.
    import time

    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        with computer_watch_client._control_lock:
            if len(computer_watch_client._pending_controls) == 2:
                break
        time.sleep(0.001)
    assert computer_watch_client.stop()["ok"]
    first.join(1)
    second.join(1)
    assert not first.is_alive() and not second.is_alive()
    assert calls == [("stop", "")]
    release.set()
    computer_watch_client._transport.submit(lambda: None).result(timeout=2)
    assert all(action not in {"start", "user_control"} for action, _ in calls)


def test_transport_epoch_refuses_late_context_and_confirmation():
    fields = {"transport_id": "isolated-source".ljust(32, "0"), "transport_epoch": 0}
    assert watch_control.accept_transport(fields)
    assert watch_control.accept_transport({**fields, "transport_epoch": 1})
    assert not watch_control.accept_transport(fields)


def test_stop_during_confirmation_target_probe_invalidates_disclosed_epoch(monkeypatch):
    import computer_watch

    starts = []
    window = {"id": "window", "app": "Terminal", "pid": 123}
    controls = watch_control.WatchControls(
        owner=lambda: True, target=lambda: window, start=lambda **kw: starts.append(kw)
    )
    monkeypatch.setattr(computer_watch, "stop", lambda: {"ok": True})
    controls.handle("watch the computer", "owner")

    def target_during_stop():
        controls.handle("stop watching", "owner")
        return window

    controls.target = target_during_stop
    result = controls.handle("confirm visual watch", "owner")
    assert not result["ok"] and starts == []


@pytest.mark.asyncio
async def test_sdk_user_control_consumes_turn_and_replay_before_model(monkeypatch):
    from types import SimpleNamespace

    import agent
    import computer_watch_client
    import watch_mode
    from jarvis_rt import StopResponse

    launches, spoken = [], []

    async def say(text, **kwargs):
        spoken.append((text, kwargs))

    monkeypatch.setattr(agent.phone_session, "private", lambda: False)
    monkeypatch.setattr(agent.phone_session, "guest_required", lambda: False)
    monkeypatch.setattr(
        watch_mode,
        "launch",
        lambda: (
            launches.append("watch the computer") or {"ok": True, "state": "watching"}
        ),
    )
    monkeypatch.setattr(
        computer_watch_client,
        "trusted_user_control",
        lambda *a, **k: pytest.fail("legacy visual consent path"),
    )
    fake = SimpleNamespace(session=SimpleNamespace(say=say))
    message = SimpleNamespace(
        role="user", id="fresh-watch-turn", text_content="watch the computer"
    )
    with pytest.raises(StopResponse):
        await agent.Assistant.on_user_turn_completed(fake, None, message)
    with pytest.raises(StopResponse):
        await agent.Assistant.on_user_turn_completed(fake, None, message)
    assert launches == ["watch the computer"]
    assert spoken == [("Watching the laptop, Sir.", {"add_to_chat_ctx": False})]


def test_native_disclosure_names_verified_target_and_confirms_after_switch():
    candidate = {
        "id": "selected",
        "app": "Terminal",
        "pid": 123,
        "title": "stale title",
    }
    inspected, starts = [], []

    def inspect(target):
        inspected.append(dict(target))
        return {
            "id": "selected",
            "app": "Terminal",
            "pid": 123,
            "title": "Fresh build output",
        }

    controls = watch_control.WatchControls(
        owner=lambda: True,
        target=lambda: candidate,
        inspect_target=inspect,
        capture_policy=lambda: "pinned_window",
        start=lambda **kw: starts.append(kw) or {"ok": True},
    )
    disclosure = controls.handle("watch the computer", "owner")
    assert "Fresh build output" in disclosure["say"]
    assert "stale title" not in disclosure["say"]
    assert "even while switching" in disclosure["say"]
    candidate = {"id": "other", "app": "Editor", "pid": 456}
    assert controls.handle("confirm visual watch", "owner")["ok"]
    assert starts[0]["expected_target"] == ("selected", "Terminal", 123)
    assert starts[0]["consent"].capture_policy == "pinned_window"
    assert inspected[1]["id"] == "selected"


def test_native_uuid_braces_and_case_match_verified_disclosure():
    canonical = "00112233-4455-6677-8899-aabbccddeeff"
    candidate = {"id": "{" + canonical.upper() + "}", "app": "Terminal", "pid": 123}
    starts = []
    controls = watch_control.WatchControls(
        owner=lambda: True,
        target=lambda: candidate,
        inspect_target=lambda _: {
            "id": canonical,
            "app": "Terminal",
            "pid": 123,
            "title": "Fresh named task",
        },
        capture_policy=lambda: "pinned_window",
        start=lambda **kw: starts.append(kw) or {"ok": True},
    )
    assert controls.handle("watch the computer", "owner")["confirmation_required"]
    assert controls.handle("confirm visual watch", "owner")["ok"]
    assert starts[0]["expected_target"][0] == canonical


def test_foreground_grant_cannot_change_to_native_policy_without_new_disclosure():
    policy = ["foreground_window"]
    controls = watch_control.WatchControls(
        owner=lambda: True,
        target=lambda: {"id": "selected", "app": "Terminal", "pid": 123},
        capture_policy=lambda: policy[0],
        start=lambda **_: pytest.fail("reused foreground grant"),
    )
    controls.handle("watch the computer", "owner")
    policy[0] = "pinned_window"
    assert not controls.handle("confirm visual watch", "owner")["ok"]


def test_spoken_disclosure_never_recites_confirmation_token():
    controls = watch_control.WatchControls(
        owner=lambda: True,
        capture_policy=lambda: "pinned_window",
        target=lambda: {"id": "window", "app": "Terminal", "pid": 123},
        inspect_target=lambda _: {
            "id": "window",
            "app": "Terminal",
            "pid": 123,
            "title": "Confirm visual watch test",
        },
    )
    result = controls.handle("watch the computer", "owner")
    assert "confirm visual watch" in result["say"].lower()
    assert "confirm visual watch" not in result["spoken_disclosure"].lower()
    assert "Gemini" in result["spoken_disclosure"]


@pytest.mark.asyncio
async def test_voice_confirmation_during_speech_is_consumed_without_grant(monkeypatch):
    from types import SimpleNamespace

    import agent
    import computer_watch_client
    from jarvis_rt import StopResponse

    monkeypatch.setattr(agent.phone_session, "private", lambda: False)
    monkeypatch.setattr(agent.phone_session, "guest_required", lambda: False)
    monkeypatch.setattr(
        computer_watch_client,
        "trusted_user_control",
        lambda *_: pytest.fail("TTS authorized capture"),
    )
    fake = SimpleNamespace(session=SimpleNamespace(agent_state="speaking"))
    with pytest.raises(StopResponse):
        await agent.Assistant.on_user_turn_completed(
            fake,
            None,
            SimpleNamespace(
                role="user", id="echo-turn", text_content="confirm visual watch"
            ),
        )
