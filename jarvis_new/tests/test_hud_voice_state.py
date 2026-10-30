import asyncio
import json
from collections import defaultdict

import pytest

import hud_voice_state as voice


@pytest.fixture(autouse=True)
def hud_home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))


@pytest.mark.parametrize(
    "agent,user,executing,expected",
    [
        ("initializing", "listening", 0, "idle"),
        ("listening", "listening", 0, "listening"),
        ("listening", "away", 0, "listening"),
        ("listening", "speaking", 0, "listening"),
        ("speaking", "listening", 0, "speaking"),
        ("speaking", "speaking", 0, "speaking"),
        ("thinking", "speaking", 0, "thinking"),
        ("speaking", "speaking", 2, "thinking"),
        ("listening", "away", 1, "thinking"),
        ("closed", "speaking", 2, "idle"),
        ("disconnected", "speaking", 0, "idle"),
    ],
)
def test_canonical_precedence(agent, user, executing, expected):
    assert voice.display_state(agent, user, executing) == expected


def test_long_speech_and_quiet_call_are_live_until_lease_or_close(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(voice.time, "time", lambda: now[0])
    p = voice.VoiceStatePublisher("desktop")
    p.update(agent="speaking")
    for _ in range(100):
        now[0] += 2
        p.update()
        assert voice.read("desktop") == "speaking"
        assert voice.read_snapshot("desktop")["live"]
    p.update(agent="listening", user="away")
    assert voice.read("desktop") == "listening"
    now[0] += voice.MAX_AGE_S
    assert voice.read_snapshot("desktop") is None
    p.close()
    assert not voice.read_snapshot("desktop")["live"]
    p.update(agent="speaking")
    assert voice.read("desktop") == "idle"


def test_parallel_rooms_old_owner_and_new_owner():
    old = voice.VoiceStatePublisher("desktop")
    old.update(agent="speaking")
    phone = voice.VoiceStatePublisher("other-room")
    phone.update(agent="thinking")
    assert voice.read("desktop") == "speaking"
    new = voice.VoiceStatePublisher("desktop")
    new.update(agent="listening")
    old.update(agent="thinking")
    old.close()
    phone.close()
    assert voice.read("desktop") == "listening"
    assert not old.owned
    new.close()
    assert voice.read("desktop") == "idle"


def test_corrupt_wrong_room_and_legacy_upgrade(monkeypatch):
    voice.state_path().write_text("broken")
    assert voice.read("a") is None
    voice.state_path().write_text(
        json.dumps(
            {
                "room": "a",
                "state": "idle",
                "agent_state": "listening",
                "user_state": "listening",
                "owner": "legacy",
                "ts": voice.time.time(),
            }
        )
    )
    assert voice.read("a") == "listening"
    assert voice.read("b") is None
    voice.state_path().write_text("[]")
    assert voice.read("a") is None


class Emitter:
    def __init__(self, name="desktop"):
        self.name = name
        self.agent_state = "listening"
        self.user_state = "listening"
        self.connected = True
        self.listeners = defaultdict(list)

    def on(self, event, callback):
        self.listeners[event].append(callback)

    def off(self, event, callback):
        self.listeners[event].remove(callback)

    def emit(self, event):
        for callback in self.listeners[event][:]:
            callback(object())

    def isconnected(self):
        return self.connected


def test_actual_emitter_sequence_tool_overlap_error_and_close():
    session, room = Emitter(), Emitter()
    monitor = voice.VoiceStateMonitor(session, room)
    assert voice.read("desktop") == "listening"
    session.user_state = "speaking"
    session.emit("user_state_changed")
    assert voice.read("desktop") == "listening"
    session.agent_state = "speaking"
    session.emit("agent_state_changed")
    with voice.execution_scope(session):
        assert voice.read("desktop") == "thinking"
        try:
            with voice.execution_scope(session):
                session.agent_state = "listening"  # interruption / concurrent ack end
                session.emit("agent_state_changed")
                raise RuntimeError("tool failed")
        except RuntimeError:
            pass
        assert voice.read("desktop") == "thinking"
    assert voice.read("desktop") == "listening"
    session.emit("close")
    monitor.refresh()  # SDK resets to initializing after close; never reclaims
    assert voice.read("desktop") == "idle"
    assert all(not callbacks for callbacks in session.listeners.values())
    assert all(not callbacks for callbacks in room.listeners.values())
    # A reused worker process can host a new session with its own heartbeat.
    second = voice.VoiceStateMonitor(Emitter(), room)
    assert voice.read("desktop") == "listening"
    monitor.close()
    assert voice.read("desktop") == "listening"
    room.emit("disconnected")
    assert voice.read("desktop") == "idle"
    assert second.publisher.closed


def test_heartbeat_stops_on_session_close_and_disconnect(monkeypatch):
    async def run():
        monkeypatch.setattr(voice, "HEARTBEAT_S", 0.001)
        session, room = Emitter(), Emitter()
        monitor = voice.VoiceStateMonitor(session, room)
        task = monitor.start()
        await asyncio.sleep(0.003)
        session.emit("close")
        with pytest.raises(asyncio.CancelledError):
            await task
        closed = voice.state_path("desktop").read_text()
        await asyncio.sleep(0.003)
        assert voice.state_path("desktop").read_text() == closed
        second = voice.VoiceStateMonitor(Emitter(), room)
        task = second.start()
        await asyncio.sleep(0.003)
        room.connected = False
        await task
        assert voice.read("desktop") == "idle"

    asyncio.run(run())


def test_bridge_liveness_is_session_owned_and_supports_long_calls(
    monkeypatch, tmp_path
):
    import os
    import urllib.request

    import bridge

    def room_payload(server):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_address[1]}/room"
        ) as reply:
            return json.load(reply)

    room_file = tmp_path / "hud_room"
    room_file.write_text("desktop")
    server, _ = bridge._run_in_thread()
    try:
        assert not room_payload(server)["call_live"]  # file presence is insufficient
        p = voice.VoiceStatePublisher("desktop")
        p.update(agent="listening")
        body = room_payload(server)
        assert body["call_live"] and body["agent_state"] == "listening"
        assert body["voice_owner"] == p.owner
        old = voice.time.time() - 3600
        os.utime(room_file, (old, old))
        assert room_payload(server)[
            "call_live"
        ]  # healthy lease outranks old room mtime
        other = voice.VoiceStatePublisher("another-room")
        other.update(agent="thinking")
        assert room_payload(server)["agent_state"] == "listening"
        p.update(agent="speaking", executing=1)
        assert room_payload(server)["agent_state"] == "thinking"
        p.close()
        assert not room_payload(server)["call_live"]
        room_file.write_text("desktop")
        new = voice.VoiceStatePublisher("desktop")
        new.update(agent="speaking")
        p.close()
        assert room_payload(server)["agent_state"] == "speaking"
        data = json.loads(voice.state_path("desktop").read_text())
        data["ts"] -= voice.MAX_AGE_S + 1
        voice.state_path("desktop").write_text(json.dumps(data))
        assert not room_payload(server)["call_live"]
        assert room_payload(server)["agent_state"] is None
    finally:
        server.shutdown()
        server.server_close()


def test_monitor_survives_real_startup_order_before_ctx_connect(monkeypatch):
    async def run():
        monkeypatch.setattr(voice, "HEARTBEAT_S", 0.001)
        session, room = Emitter(), Emitter()
        session.agent_state = "initializing"
        room.connected = False
        monitor = voice.VoiceStateMonitor(session, room)
        task = monitor.start()
        await asyncio.sleep(0.003)
        assert not monitor.publisher.closed
        assert not voice.read_snapshot("desktop")["live"]
        room.connected = True  # ctx.connect completes after monitor installation
        session.agent_state = "listening"  # session.start completes
        session.emit("agent_state_changed")
        await asyncio.sleep(0.003)
        assert voice.read_snapshot("desktop")["live"]
        assert voice.read("desktop") == "listening"
        room.connected = False
        await task
        assert voice.read("desktop") == "idle"

    asyncio.run(run())


def test_installed_sdk_events_reach_authenticated_room_handler(monkeypatch, tmp_path):
    """Exercise real SDK emitters and HTTP; no room join, model, mic, or audio."""
    import urllib.error
    import urllib.request

    import bridge
    from jarvis_rt import AgentSession
    from jarvis_rt.events import EventEmitter

    monkeypatch.delenv("JARVIS_BRIDGE_TOKEN", raising=False)
    (tmp_path / "bridge_token").write_text("isolated-sdk-test-token\n")
    (tmp_path / "hud_room").write_text("offline-sdk-room")
    token = bridge.resolve_token()
    server, _ = bridge._run_in_thread(token=token)
    url = f"http://127.0.0.1:{server.server_address[1]}/room"

    def payload():
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return json.load(response)

    async def run():
        # Seed SDK room metadata only; deliberately never call connect/start.
        class Room(EventEmitter):
            name = "offline-sdk-room"

            def isconnected(self):
                return False

        room = Room()
        session = AgentSession()
        monitor = voice.VoiceStateMonitor(session, room)
        try:
            assert not payload()["call_live"]
            for state in ("listening", "speaking", "thinking", "listening"):
                session._set_agent_state(state)
                body = payload()
                assert body["call_live"]
                assert body["agent_state"] == state
                assert body["voice_owner"] == monitor.publisher.owner
            with voice.execution_scope(session):
                session._set_agent_state("speaking")
                assert payload()["agent_state"] == "thinking"
            assert payload()["agent_state"] == "speaking"
            session.emit("close", None)
            assert not payload()["call_live"]
            assert payload()["agent_state"] == "idle"
        finally:
            monitor.close()

    try:
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(url, timeout=2)
        assert denied.value.code == 401
        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()


def test_sdk_state_owns_hud_without_custom_turn_manager():
    session, room = Emitter(), Emitter()
    monitor = voice.VoiceStateMonitor(session, room)
    for state in ("speaking", "listening", "thinking"):
        session.agent_state = state
        session.emit("agent_state_changed")
        assert voice.read("desktop") == state
    monitor.close()
    assert voice.read("desktop") == "idle"
