import asyncio
import threading

import pytest

import wake_client


class SyntheticHub:
    """VoiceClient stand-in. ``outcome`` picks how the hub answers the join:
    "ok", "refused" (fatal close 4001 before accept), "hang", or an exception
    instance raised from wait_connected."""

    outcome = "ok"
    events: list | None = None
    entered: asyncio.Event | None = None

    def __init__(self, url, token, **kwargs):
        self.url, self.token = url, token
        self.last_close_code = None
        self.topics = {}
        self._closed = None

    def on_topic(self, topic, callback):
        self.topics.setdefault(topic, []).append(callback)

    def start(self):
        self._closed = asyncio.get_running_loop().create_future()
        return self._closed

    async def wait_connected(self, timeout=None):
        if SyntheticHub.entered is not None:
            SyntheticHub.entered.set()
        outcome = SyntheticHub.outcome
        if outcome == "hang":
            await asyncio.sleep(100)
        if outcome == "refused":
            self.last_close_code = 4001
            self._closed.set_result(None)
            await asyncio.sleep(100)
        if isinstance(outcome, BaseException):
            raise outcome

    async def send_mic(self, pcm):
        return True

    async def send_chat(self, text):
        return True

    async def audio_frames(self):
        if False:
            yield b""

    async def close(self):
        SyntheticHub.events.append("disconnected")
        if self._closed is not None and not self._closed.done():
            self._closed.set_result(None)


@pytest.fixture
def join_fixture(monkeypatch):
    events = []
    SyntheticHub.events = events
    SyntheticHub.outcome = "refused"
    SyntheticHub.entered = None
    client = wake_client.WakeClient.__new__(wake_client.WakeClient)
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
    monkeypatch.setattr(wake_client, "VoiceClient", SyntheticHub)
    client._agent_name = "synthetic-agent"

    async def idle():
        try:
            await asyncio.sleep(100)
        finally:
            events.append("check_drained")

    monkeypatch.setattr(wake_client, "active_call_exists", idle)
    return client, events, SyntheticHub


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
    assert "call connection failed" in caplog.text


async def test_transport_failure_does_not_auto_retry(join_fixture):
    client, events, _ = join_fixture
    await client._summon_with_rewake(
        mic_queue=asyncio.Queue(), preserve_join_audio=True
    )
    assert events.count("disconnected") == 1


async def test_join_cancellation_propagates_after_cleanup(join_fixture, monkeypatch):
    client, events, hub_type = join_fixture
    hub_type.outcome = "hang"
    hub_type.entered = asyncio.Event()
    task = asyncio.create_task(client._summon_session())
    await asyncio.wait_for(hub_type.entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "disconnected" in events
    assert "check_drained" in events
    assert not client._in_call


async def test_unexpected_join_bug_still_raises(join_fixture):
    client, events, hub_type = join_fixture
    hub_type.outcome = ValueError("synthetic programmer error")
    with pytest.raises(ValueError):
        await client._summon_session()
    assert "disconnected" in events
