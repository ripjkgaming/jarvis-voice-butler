import asyncio

import pytest
from websockets.asyncio.server import serve as ws_serve

from jarvis_rt.transport.client import VoiceClient, VoiceClosedError
from jarvis_rt.transport.frames import encode_text
from jarvis_rt.transport.server import VoiceHub
from jarvis_rt.transport.tokens import mint_call_token

SECRET = b"c" * 32
ROOM = "jarvis-2"


def _token(identity="u", mode="speaker", now=None):
    return mint_call_token(ROOM, identity, mode, 60, None, SECRET, now=now)


async def _serve_hub(hub, record=None):
    async def _handler(ws):
        await hub.handle(ws, ws.request.path)
        if record is not None:
            record.append(ws.close_code)

    server = await ws_serve(_handler, "127.0.0.1", 0, max_size=1 << 20)
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}/voice"


async def _wait_until(predicate, timeout=3.0):
    async def _loop():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_loop(), timeout)


async def test_terminal_code_4001_is_not_retried():
    hub = VoiceHub(SECRET)
    server, base = await _serve_hub(hub)
    calls = []

    def factory():
        calls.append(1)
        return "not.valid"

    client = VoiceClient(base, factory, backoff_base=0.01)
    try:
        with pytest.raises(VoiceClosedError) as exc:
            await asyncio.wait_for(client.run(), 5)
        assert exc.value.code == 4001
        assert len(calls) == 1
    finally:
        server.close()
        await server.wait_closed()


async def test_terminal_code_4003_is_not_retried():
    hub = VoiceHub(SECRET)
    server, base = await _serve_hub(hub)
    first = VoiceClient(base, _token("first"), backoff_base=0.01)
    task = first.start()
    try:
        await first.wait_connected(5)
        calls = []

        def factory():
            calls.append(1)
            return _token("second")

        second = VoiceClient(base, factory, backoff_base=0.01)
        with pytest.raises(VoiceClosedError) as exc:
            await asyncio.wait_for(second.run(), 5)
        assert exc.value.code == 4003
        assert len(calls) == 1
    finally:
        await first.close()
        await asyncio.wait_for(task, 5)
        server.close()
        await server.wait_closed()


async def test_non_terminal_drop_is_retried_with_fresh_token():
    attempts = []

    async def _handler(ws):
        attempts.append(1)
        if len(attempts) == 1:
            await ws.close(1011, "try again")
            return
        await ws.send(
            encode_text("hello", {"identity": "u", "room": ROOM, "mode": "speaker"})
        )
        async for _ in ws:
            pass

    server = await ws_serve(_handler, "127.0.0.1", 0)
    base = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/voice"
    tokens = []

    def factory():
        tokens.append(1)
        return f"t{len(tokens)}"

    client = VoiceClient(base, factory, backoff_base=0.01)
    task = client.start()
    try:
        await client.wait_connected(5)
        assert len(tokens) == 2
        assert client.hello["identity"] == "u"
    finally:
        await client.close()
        await asyncio.wait_for(task, 5)
        server.close()
        await server.wait_closed()


async def test_mic_and_audio_round_trip_in_order():
    hub = VoiceHub(SECRET)
    server, base = await _serve_hub(hub)
    room = hub.room(ROOM)
    client = VoiceClient(base, _token("speaker"), backoff_base=0.01)
    task = client.start()
    try:
        await client.wait_connected(5)
        mic_chunks = [bytes([n]) * 1024 for n in range(5)]
        for chunk in mic_chunks:
            assert await client.send_mic(chunk)
        got_mic = []
        async for item in room.mic_frames():
            got_mic.append(item)
            if len(got_mic) == 5:
                break
        assert got_mic == mic_chunks

        audio = [b"\x01\x00" * 200, b"\x02\x00" * 200, b"\x03\x00" * 200]
        for chunk in audio:
            room.send_audio(chunk)
        got_audio = []
        async for chunk in client.audio_frames():
            got_audio.append(chunk)
            if len(got_audio) == 3:
                break
        assert got_audio == audio
    finally:
        await client.close()
        await asyncio.wait_for(task, 5)
        server.close()
        await server.wait_closed()


async def test_on_topic_state_and_flush_callbacks():
    hub = VoiceHub(SECRET)
    server, base = await _serve_hub(hub)
    room = hub.room(ROOM)
    client = VoiceClient(base, _token("obs", mode="observer"), backoff_base=0.01)
    seen = []
    flushed = asyncio.Event()
    client.on_topic("state", seen.append)
    client.on_topic("flush", lambda _payload: flushed.set())
    task = client.start()
    try:
        await client.wait_connected(5)
        await _wait_until(lambda: len(room.observers) == 1)
        room.send_text("state", {"agent": "idle"})
        await _wait_until(lambda: bool(seen))
        assert seen == [{"agent": "idle"}]
        room.flush_playback()
        await asyncio.wait_for(flushed.wait(), 3)
    finally:
        await client.close()
        await asyncio.wait_for(task, 5)
        server.close()
        await server.wait_closed()


async def test_flush_clears_client_audio_queue():
    hub = VoiceHub(SECRET)
    server, base = await _serve_hub(hub)
    room = hub.room(ROOM)
    client = VoiceClient(base, _token("obs", mode="observer"), backoff_base=0.01)
    task = client.start()
    try:
        await client.wait_connected(5)
        await _wait_until(lambda: len(room.observers) == 1)
        client._push_audio(b"\x00\x00")  # Simulate queued playback.
        room.flush_playback()
        await _wait_until(lambda: not client._audio and client.connected)
        await asyncio.sleep(0.05)
        assert len(client._audio) == 0
    finally:
        await client.close()
        await asyncio.wait_for(task, 5)
        server.close()
        await server.wait_closed()


async def test_send_chat_reaches_server_callback():
    hub = VoiceHub(SECRET)
    server, base = await _serve_hub(hub)
    room = hub.room(ROOM)
    texts = []
    room.on_chat(texts.append)
    client = VoiceClient(base, _token("speaker"), backoff_base=0.01)
    task = client.start()
    try:
        await client.wait_connected(5)
        assert await client.send_chat("  hello jarvis  ")
        await _wait_until(lambda: bool(texts))
        assert texts == ["hello jarvis"]
        with pytest.raises(ValueError):
            await client.send_chat("   ")
    finally:
        await client.close()
        await asyncio.wait_for(task, 5)
        server.close()
        await server.wait_closed()


async def test_close_sends_4010_and_stops_run():
    hub = VoiceHub(SECRET)
    closes = []
    server, base = await _serve_hub(hub, record=closes)
    client = VoiceClient(base, _token("speaker"), backoff_base=0.01)
    task = client.start()
    await client.wait_connected(5)
    await client.close()
    await asyncio.wait_for(task, 5)
    await _wait_until(lambda: bool(closes))
    assert closes == [4010]
    server.close()
    await server.wait_closed()


async def test_unconnected_sends_return_false():
    client = VoiceClient("ws://127.0.0.1:9/voice", _token("x"), backoff_base=0.01)
    assert await client.send_mic(b"\x00\x00") is False
    assert await client.send_chat("hi") is False
