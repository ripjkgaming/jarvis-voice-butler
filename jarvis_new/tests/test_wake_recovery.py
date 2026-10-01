"""Call-level audio failure recovery with fake RTC and no real devices/services."""

import asyncio
import threading
from types import SimpleNamespace

import pytest


@pytest.fixture
def call_harness(monkeypatch):
    from livekit import rtc

    import wake_client

    events = []
    playback_started = asyncio.Event()
    mic_started = asyncio.Event()
    playback_closing = asyncio.Event()
    release_playback = asyncio.Event()
    release_mic = asyncio.Event()
    owned_tasks = []
    behavior = {"playback": "wait"}
    connecting = asyncio.Event()

    class Source:
        async def aclose(self):
            events.append("source_closed")

    class Room:
        def __init__(self):
            self.handlers = {}
            self.local_participant = self

        def on(self, name):
            def register(callback):
                self.handlers[name] = callback
                return callback

            return register

        async def connect(self, *args):
            connecting.set()
            if behavior.get("connect") == "error":
                raise OSError("synthetic connection failure")
            if behavior.get("connect") == "wait":
                await asyncio.Event().wait()
            events.append("connected")

        async def publish_track(self, *args):
            if behavior.get("no_agent"):
                return
            self.handlers["track_subscribed"](
                SimpleNamespace(kind=rtc.TrackKind.KIND_AUDIO), None, None
            )

        async def disconnect(self):
            events.append("disconnected")
            self.handlers["disconnected"]()

    room = Room()
    monkeypatch.setattr(rtc, "Room", lambda: room)
    monkeypatch.setattr(rtc, "AudioSource", lambda *args: Source())
    monkeypatch.setattr(
        rtc,
        "LocalAudioTrack",
        SimpleNamespace(create_audio_track=lambda *args: object()),
    )
    for name in (
        "hud_stage",
        "clear_hud_room",
        "clear_hud_waking",
        "publish_hud_room",
        "summon_overlay",
    ):
        monkeypatch.setattr(
            wake_client, name, lambda *args, _name=name: events.append(_name)
        )
    monkeypatch.setattr(wake_client, "mint_summon_token", lambda **kwargs: "fake-token")

    async def idle(*args):
        return False

    monkeypatch.setattr(wake_client, "active_call_exists", idle)
    client = wake_client.WakeClient.__new__(wake_client.WakeClient)
    client._creds = {
        "LIVEKIT_URL": "unused",
        "LIVEKIT_API_KEY": "unused",
        "LIVEKIT_API_SECRET": "unused",
    }
    client._agent_name = "fake-agent"
    client._mic_lock = threading.Lock()
    client._pending_text = client._pending_announce = ""
    client._muted = True
    client._in_call = False
    client._listen_queue_live = True

    async def mic(source):
        owned_tasks.append(asyncio.current_task())
        mic_started.set()
        try:
            await release_mic.wait()
            if behavior.get("mic") == "error":
                raise OSError("synthetic microphone failure")
            if behavior.get("mic") == "cancel":
                raise asyncio.CancelledError
            if behavior.get("mic") == "rewake":
                client._rewake.set()
        finally:
            events.append("mic_closed")

    async def queue_mic(source, *args):
        await mic(source)

    async def play(track):
        owned_tasks.append(asyncio.current_task())
        playback_started.set()
        try:
            await release_playback.wait()
            if behavior["playback"] == "error":
                raise OSError("synthetic device failure")
        finally:
            events.append("playback_closed")
            playback_closing.set()
            if behavior.get("cleanup_wait"):
                await asyncio.Event().wait()

    monkeypatch.setattr(client, "_pump_mic", mic)
    monkeypatch.setattr(client, "_pump_queue", queue_mic)
    monkeypatch.setattr(client, "_play_agent", play)
    return SimpleNamespace(
        client=client,
        room=room,
        events=events,
        mic_started=mic_started,
        playback_started=playback_started,
        playback_closing=playback_closing,
        release=release_playback,
        release_mic=release_mic,
        behavior=behavior,
        tasks=owned_tasks,
        connecting=connecting,
    )


@pytest.mark.parametrize("before_agent", [True, False], ids=["joining", "active"])
@pytest.mark.parametrize("queued_mic", [False, True], ids=["device", "queue"])
@pytest.mark.parametrize("outcome", ["error", "complete", "cancel"])
async def test_microphone_end_cleans_call_promptly(
    call_harness, before_agent, queued_mic, outcome
):
    h = call_harness
    h.behavior.update(no_agent=before_agent, mic=outcome)
    call = asyncio.create_task(
        h.client._summon_session(mic_queue=asyncio.Queue() if queued_mic else None)
    )
    try:
        await asyncio.wait_for(h.mic_started.wait(), 1)
        if not before_agent:
            await asyncio.wait_for(h.playback_started.wait(), 1)
        h.release_mic.set()
        done, _ = await asyncio.wait({call}, timeout=0.2)
        assert call in done, "ended microphone must not leave a call waiting for input"
        assert await call is False
        assert all(task.done() for task in h.tasks)
        assert h.events.count("mic_closed") == 1
        assert h.events.count("playback_closed") == int(not before_agent)
        assert h.events.count("source_closed") == 1
        assert "clear_hud_room" in h.events
        assert "clear_hud_waking" in h.events
        assert h.events[-1] == "disconnected"
        assert h.client._listen_queue_live is True
        assert h.client._in_call is False and h.client._rewake is None
        assert h.client.muted is True
    finally:
        call.cancel()
        for task in h.tasks:
            task.cancel()
        await asyncio.wait_for(
            asyncio.gather(call, *h.tasks, return_exceptions=True), 1
        )


@pytest.mark.parametrize("before_agent", [True, False], ids=["joining", "active"])
async def test_microphone_rewake_still_requests_a_fresh_call(
    call_harness, before_agent
):
    h = call_harness
    h.behavior.update(no_agent=before_agent, mic="rewake")
    call = asyncio.create_task(h.client._summon_session())
    try:
        await asyncio.wait_for(h.mic_started.wait(), 1)
        if not before_agent:
            await asyncio.wait_for(h.playback_started.wait(), 1)
        h.release_mic.set()
        done, _ = await asyncio.wait({call}, timeout=0.2)
        assert call in done, "microphone rewake must leave the old call promptly"
        assert await call is True
        assert all(task.done() for task in h.tasks)
        assert h.events[-1] == "disconnected"
    finally:
        call.cancel()
        for task in h.tasks:
            task.cancel()
        await asyncio.wait_for(
            asyncio.gather(call, *h.tasks, return_exceptions=True), 1
        )


@pytest.mark.parametrize("outcome", ["error", "complete"])
async def test_playback_failure_or_track_end_returns_to_listening(
    call_harness, outcome
):
    h = call_harness
    h.behavior["playback"] = outcome
    call = asyncio.create_task(h.client._summon_session())
    try:
        await asyncio.wait_for(h.playback_started.wait(), 1)
        await asyncio.wait_for(h.mic_started.wait(), 1)
        h.release.set()
        done, _ = await asyncio.wait({call}, timeout=0.2)
        assert call in done, "ended playback must not leave a silent connected call"
        assert await call is False
        assert all(task.done() for task in h.tasks)
        assert h.events.count("mic_closed") == 1
        assert h.events.count("playback_closed") == 1
        assert h.events.count("source_closed") == 1
        assert h.events[-1] == "disconnected"
        assert h.client._listen_queue_live is True
        assert h.client._in_call is False and h.client._rewake is None
        assert h.client.muted is True
    finally:
        call.cancel()
        for task in h.tasks:
            task.cancel()
        await asyncio.gather(call, *h.tasks, return_exceptions=True)


@pytest.mark.parametrize("end_reason", ["rewake", "disconnect", "cancel"])
async def test_quiet_playback_keeps_call_and_normal_end_cleans_tasks(
    call_harness, end_reason
):
    h = call_harness
    call = asyncio.create_task(h.client._summon_session())
    try:
        await asyncio.wait_for(h.playback_started.wait(), 1)
        await asyncio.sleep(0.02)
        assert not call.done(), "a quiet live track is not a playback failure"
        if end_reason == "rewake":
            h.client._rewake.set()
            assert await asyncio.wait_for(call, 1) is True
        elif end_reason == "disconnect":
            h.room.handlers["disconnected"]()
            assert await asyncio.wait_for(call, 1) is False
        else:
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
        assert all(task.done() for task in h.tasks)
        assert h.events.count("mic_closed") == 1
        assert h.events.count("playback_closed") == 1
        assert h.events.count("source_closed") == 1
        assert h.client._listen_queue_live is True
        assert h.client.muted is True
    finally:
        call.cancel()
        for task in h.tasks:
            task.cancel()
        await asyncio.gather(call, *h.tasks, return_exceptions=True)


async def test_repeated_cancel_still_clears_call_state_and_disconnects(call_harness):
    h = call_harness
    h.behavior["cleanup_wait"] = True
    call = asyncio.create_task(h.client._summon_session())
    await asyncio.wait_for(h.playback_started.wait(), 1)
    call.cancel()
    await asyncio.wait_for(h.playback_closing.wait(), 1)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert all(task.done() for task in h.tasks)
    assert h.client._in_call is False
    assert h.client._listen_queue_live is True
    assert h.client._rewake is None
    assert h.events[-1] == "disconnected"
    assert h.events.count("source_closed") == 1


@pytest.mark.parametrize("outcome", ["error", "cancel"])
async def test_connect_failure_or_cancel_clears_join_state(call_harness, outcome):
    h = call_harness
    h.behavior["connect"] = "error" if outcome == "error" else "wait"
    call = asyncio.create_task(h.client._summon_session())
    await asyncio.wait_for(h.connecting.wait(), 1)
    if outcome == "cancel":
        call.cancel()
    expected = OSError if outcome == "error" else asyncio.CancelledError
    with pytest.raises(expected):
        await call
    assert h.client._listen_queue_live is True
    assert h.client._rewake is None
    assert h.client._in_call is False
    assert h.client.muted is True
    assert "clear_hud_waking" in h.events
    assert "clear_hud_room" in h.events
    assert h.events[-1] == "disconnected"
    assert not h.tasks


async def test_disconnect_during_join_stops_waiting_for_agent(call_harness):
    h = call_harness
    h.behavior["no_agent"] = True
    call = asyncio.create_task(h.client._summon_session())
    try:
        await asyncio.wait_for(h.mic_started.wait(), 1)
        # Current RTC SDK includes a reason with this event.
        h.room.handlers["disconnected"]("synthetic-server-shutdown")
        done, _ = await asyncio.wait({call}, timeout=0.2)
        assert call in done, "disconnected join must not wait the 25s track timeout"
        assert await call is False
        assert all(task.done() for task in h.tasks)
        assert h.client._listen_queue_live is True
        assert h.client._rewake is None and not h.client._in_call
        assert "clear_hud_waking" in h.events
    finally:
        call.cancel()
        await asyncio.gather(call, *h.tasks, return_exceptions=True)


@pytest.mark.parametrize("outcome", ["error", "cancel", "token_error"])
async def test_preflight_failure_or_cancel_clears_join_log(
    call_harness, monkeypatch, outcome
):
    import wake_client

    h = call_harness
    checking = asyncio.Event()

    async def check(*args):
        checking.set()
        if outcome == "cancel":
            await asyncio.Event().wait()
        if outcome == "error":
            raise OSError("synthetic call check failure")
        return False

    def mint(**kwargs):
        raise ValueError("synthetic token failure")

    monkeypatch.setattr(wake_client, "active_call_exists", check)
    if outcome == "token_error":
        monkeypatch.setattr(wake_client, "mint_summon_token", mint)
    call = asyncio.create_task(h.client._summon_session())
    await asyncio.wait_for(checking.wait(), 1)
    if outcome == "cancel":
        call.cancel()
    expected = {
        "cancel": asyncio.CancelledError,
        "error": OSError,
        "token_error": ValueError,
    }[outcome]
    with pytest.raises(expected):
        await call
    assert "clear_hud_waking" in h.events
    assert h.client._listen_queue_live is True and not h.client._in_call
    assert not h.tasks


async def test_agent_join_timeout_cleans_waiters_and_microphone(
    call_harness, monkeypatch
):
    import wake_client

    h = call_harness
    h.behavior["no_agent"] = True
    monkeypatch.setattr(wake_client, "AGENT_JOIN_TIMEOUT", 0.01)
    assert await asyncio.wait_for(h.client._summon_session(), 1) is False
    assert all(task.done() for task in h.tasks)
    assert h.events.count("mic_closed") == 1
    assert h.events.count("source_closed") == 1
    assert h.events[-1] == "disconnected"
    assert not h.client._in_call and h.client._rewake is None
    assert h.client._listen_queue_live is True
