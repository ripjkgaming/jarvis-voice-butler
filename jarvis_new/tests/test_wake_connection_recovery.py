import asyncio
import threading

import pytest
import wake_client
from livekit import rtc


@pytest.fixture
def join_fixture(monkeypatch):
    events = []
    client = wake_client.WakeClient.__new__(wake_client.WakeClient)
    client._creds = {
        "LIVEKIT_URL": "wss://synthetic.invalid",
        "LIVEKIT_API_KEY": "synthetic",
        "LIVEKIT_API_SECRET": "synthetic",
    }
    client._mic_lock = threading.Lock()
    client._pending_text = "synthetic opening"
    client._pending_announce = ""
    client._in_call = False
    client._rewake = None
    client._listen_queue_live = True
    client._mute_epoch = 0
    for name in ("clear_hud_room", "clear_hud_waking"):
        monkeypatch.setattr(wake_client, name, lambda n=name: events.append(n))
    monkeypatch.setattr(wake_client, "hud_stage", lambda stage: None)
    monkeypatch.setattr(
        wake_client, "publish_hud_room", lambda *a: events.append("published")
    )
    monkeypatch.setattr(
        wake_client, "mint_summon_token", lambda **kw: "synthetic-token"
    )
    client._agent_name = "synthetic-agent"

    async def idle(creds):
        try:
            await asyncio.sleep(100)
        finally:
            events.append("check_drained")

    monkeypatch.setattr(wake_client, "active_call_exists", idle)

    class Room:
        def on(self, *args):
            return lambda callback: callback

        async def connect(self, *args):
            await asyncio.sleep(0)
            raise rtc.ConnectError("synthetic-private-sentinel")

        async def disconnect(self):
            events.append("disconnected")

    monkeypatch.setattr(rtc, "Room", Room)
    return client, events, Room


async def test_transport_failure_returns_to_listener_without_replaying(
    join_fixture, caplog
):
    client, events, _ = join_fixture
    assert await client._summon_session() is False
    assert client._pending_text == ""
    assert not client._in_call
    assert client._rewake is None
    assert client._listen_queue_live
    assert set(events) == {
        "clear_hud_room",
        "clear_hud_waking",
        "check_drained",
        "disconnected",
    }
    assert "synthetic-private-sentinel" not in caplog.text
    assert "call connection failed" in caplog.text


async def test_transport_failure_does_not_auto_retry(join_fixture):
    client, events, _ = join_fixture
    await client._summon_with_rewake(
        mic_queue=asyncio.Queue(), preserve_join_audio=True
    )
    assert events.count("disconnected") == 1


async def test_join_cancellation_propagates_after_cleanup(join_fixture, monkeypatch):
    client, events, room_type = join_fixture
    entered = asyncio.Event()

    async def connect(self, *args):
        entered.set()
        await asyncio.sleep(100)

    monkeypatch.setattr(room_type, "connect", connect)
    task = asyncio.create_task(client._summon_session())
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "disconnected" in events
    assert "check_drained" in events
    assert not client._in_call


async def test_unexpected_join_bug_still_raises(join_fixture, monkeypatch):
    client, events, room_type = join_fixture

    async def connect(self, *args):
        raise ValueError("synthetic programmer error")

    monkeypatch.setattr(room_type, "connect", connect)
    with pytest.raises(ValueError):
        await client._summon_session()
    assert "disconnected" in events
