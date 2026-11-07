"""Voice controls cross the authenticated bridge boundary to one watch owner."""

import io
import json
import types

import pytest
from jarvis_rt import ToolError

import bridge
import computer_watch
import computer_watch_client
from system.watch_tools import WatchTools


@pytest.fixture
def shared_owner(monkeypatch, tmp_path):
    import action_guard
    from wizard import gate

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(action_guard, "_recent", [])
    monkeypatch.setattr(gate, "bridge_token", lambda: "test-token")
    monkeypatch.setattr(gate, "bridge_host", lambda: "127.0.0.1")
    monkeypatch.setattr(computer_watch, "allowed", lambda: True)
    owner = computer_watch.ComputerWatch(allowed=lambda: True)
    monkeypatch.setattr(owner, "_loop", lambda *_: None)
    for name in ("start", "stop", "status", "briefing", "note_context"):
        monkeypatch.setattr(computer_watch, name, getattr(owner, name))
    requests = []

    def transport(request, timeout):
        assert request.full_url.startswith("http://127.0.0.1:")
        assert request.get_header("Authorization") == "Bearer test-token"
        assert timeout <= 3
        body = json.loads(request.data)
        requests.append(body["args"]["action"])
        result = bridge.run_phone_tool(body["tool"], body["args"])
        return io.BytesIO(json.dumps(result).encode())

    monkeypatch.setattr(computer_watch_client.urllib.request, "urlopen", transport)
    return owner, requests


@pytest.mark.asyncio
async def test_tool_start_fast_status_and_tool_stop_share_owner(shared_owner, monkeypatch):
    import watch_mode

    owner, requests = shared_owner
    launches = []
    monkeypatch.setattr(
        watch_mode,
        "launch",
        lambda: launches.append("launch") or {"ok": True, "state": "watching"},
    )
    computer_watch_client.note_context("Waiting for export")
    computer_watch_client._transport.submit(lambda: None).result(timeout=2)
    tools = WatchTools()
    context = types.SimpleNamespace(
        speech_handle=types.SimpleNamespace(id="watch-owner")
    )
    result = await tools.start_watch_mode(context)
    assert result["say"] == "Watching the laptop, Sir."
    assert launches == ["launch"]
    # The card launch bypasses the bridge: no start crosses to the owner.
    assert requests == ["context"]
    assert "Waiting for export" in owner._context
    # Fast bridge status and the tool status/stop still share the same owner.
    assert (
        bridge.run_phone_tool("computer_watch", {"action": "status"})["active"] is False
    )
    assert (await tools.watch_mode_status(context))["active"] is False
    await tools.stop_watch_mode(context)
    assert owner.status()["active"] is False
    assert requests == ["context", "status", "stop"]


@pytest.mark.asyncio
async def test_bridge_outage_never_starts_agent_local_observer(
    shared_owner, monkeypatch
):
    import watch_mode

    owner, _ = shared_owner
    launches = []
    monkeypatch.setattr(
        watch_mode,
        "launch",
        lambda: launches.append("launch") or {"ok": True, "state": "watching"},
    )
    monkeypatch.setattr(
        computer_watch,
        "start",
        lambda *a, **k: pytest.fail("agent observer started"),
    )

    def offline(*_, **__):
        raise OSError("offline")

    monkeypatch.setattr(computer_watch_client.urllib.request, "urlopen", offline)
    result = await WatchTools().start_watch_mode(types.SimpleNamespace())
    assert result["say"] == "Watching the laptop, Sir."
    assert launches == ["launch"]
    assert owner.status()["active"] is False


def test_missing_bridge_authentication_does_not_send_context(monkeypatch):
    from wizard import gate

    monkeypatch.setattr(gate, "bridge_token", lambda: "")
    monkeypatch.setattr(
        computer_watch_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("unauthenticated request"),
    )
    assert computer_watch_client.request("context", context="private")["ok"] is False


@pytest.mark.parametrize("action", ["context", "start", "status", "briefing"])
def test_denied_caller_never_forwards_context_or_control(
    shared_owner, monkeypatch, action
):
    import screen_understanding

    _, requests = shared_owner
    monkeypatch.setattr(screen_understanding, "local_owner_authorized", lambda: False)
    assert computer_watch_client.request(action, context="private")["ok"] is False
    assert requests == []
