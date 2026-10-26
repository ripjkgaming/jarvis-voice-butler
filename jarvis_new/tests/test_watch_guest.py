"""Watch guest enforcement uses isolated state and no real device calls."""

import pytest

import bridge
import watch_mode


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))


def test_client_cannot_override_persistent_guest(monkeypatch):
    watch_mode.enter_guest()
    monkeypatch.setattr(
        bridge, "_run_voice_action", lambda *a: pytest.fail("owner tool executed")
    )
    code, body = bridge.handle_route({"text": "lock the laptop", "guest": False})
    assert code == 200
    assert body["action"]["denied"] == "guest mode"


def test_chat_uses_guest_persona_without_client_flag(monkeypatch):
    watch_mode.enter_guest()
    seen = []
    monkeypatch.setattr(
        bridge,
        "_gemini_reply",
        lambda text, guest=False: (seen.append(guest) or "Guest reply", None),
    )
    bridge.handle_chat({"text": "hello", "guest": False})
    assert seen == [True]


def test_watchdog_locks_on_process_failure_but_never_after_owner_stop(monkeypatch):
    watch_mode.enter_guest()
    calls = []
    monkeypatch.setattr(watch_mode, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(
        watch_mode, "fail_safe_lock", lambda: calls.append("lock") or True
    )
    watch_mode.guard_watch(123)
    assert calls == ["lock"]
    assert watch_mode.guest_path().exists()
    watch_mode.restore_owner()
    watch_mode.guard_watch(123)
    assert calls == ["lock"]


def test_voice_wrapper_blocks_owner_tools_when_guest(monkeypatch):
    import asyncio

    import agent

    watch_mode.enter_guest()

    async def private():
        pytest.fail("private tool executed")

    tool = __import__("types").SimpleNamespace(id="read_clipboard", _func=private)
    agent._wrap_tools_with_timing([tool])
    with pytest.raises(Exception, match="Guest mode"):
        asyncio.run(tool._func())


def test_owner_endpoint_requires_explicit_authenticated_control(monkeypatch):
    import json
    import urllib.error
    import urllib.request

    watch_mode.enter_guest()
    monkeypatch.setattr(watch_mode, "is_running", lambda: False)
    server, _ = bridge._run_in_thread(token="test-owner-token")
    try:

        def post(body, token):
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/watch/owner",
                data=json.dumps(body).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + token,
                },
            )
            return urllib.request.urlopen(request, timeout=3)

        with pytest.raises(urllib.error.HTTPError) as error:
            post({"mode": "owner"}, "wrong-token")
        assert error.value.code == 401
        assert watch_mode.guest_required()
        with pytest.raises(urllib.error.HTTPError) as error:
            post({"mode": "owner", "guest": False}, "test-owner-token")
        assert error.value.code == 403
        assert watch_mode.guest_required()
        with post({"mode": "owner"}, "test-owner-token") as response:
            assert json.load(response)["mode"] == "owner"
        assert not watch_mode.guest_required()
    finally:
        server.shutdown()
        server.server_close()
