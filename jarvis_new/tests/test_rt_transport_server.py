import asyncio
import json
import time

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from jarvis_rt.transport import frames
from jarvis_rt.transport.frames import (
    BARGE_TOPIC,
    KIND_AUDIO,
    KIND_MIC,
    decode_binary,
    decode_text,
    encode_binary,
    encode_text,
)
from jarvis_rt.transport.server import VoiceHub
from jarvis_rt.transport.tokens import MemoryNonceLedger, mint_call_token

SECRET = b"k" * 32
ROOM = "jarvis-1"


def _token(identity="u", mode="speaker", room=ROOM, ttl=60, now=None):
    return mint_call_token(room, identity, mode, ttl, None, SECRET, now=now)


async def _start(hub):
    server = await hub.serve("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}/voice?token="


async def _recv_topic(ws, topic, timeout=3.0):
    """Return the payload of the next text frame on `topic` (skips others)."""

    async def _loop():
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                continue
            decoded = decode_text(msg)
            if decoded and decoded[0] == topic:
                return decoded[1]

    return await asyncio.wait_for(_loop(), timeout)


async def _recv_binary(ws, timeout=3.0):
    """Next binary frame, decoded as (kind, payload); text frames skipped."""

    async def _loop():
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                return decode_binary(msg)

    return await asyncio.wait_for(_loop(), timeout)


async def _close_code(ws, timeout=3.0):
    with pytest.raises(ConnectionClosed) as exc:
        await asyncio.wait_for(_drain(ws), timeout)
    return exc.value.rcvd.code if exc.value.rcvd else None


async def _drain(ws):
    while True:
        await ws.recv()


async def _wait_until(predicate, timeout=3.0):
    async def _loop():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_loop(), timeout)


@pytest.fixture
async def hub():
    return VoiceHub(SECRET, clock=time.time)


@pytest.fixture
async def served(hub):
    server, base = await _start(hub)
    yield hub, base
    server.close()
    await server.wait_closed()


async def test_hello_frame_on_accept(served):
    hub, base = served
    async with connect(base + _token("alice")) as ws:
        hello = await _recv_topic(ws, "hello")
    assert hello == {"identity": "alice", "room": ROOM, "mode": "speaker"}


async def test_missing_or_garbage_token_closes_4001(served):
    _, base = served
    async with connect(base.rstrip("=?")) as ws:
        assert await _close_code(ws) == 4001
    async with connect(base + "garbage") as ws:
        assert await _close_code(ws) == 4001


async def test_tampered_signature_closes_4001(served):
    _, base = served
    token = _token()
    body, sig = token.split(".")
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    async with connect(base + f"{body}.{flipped}") as ws:
        assert await _close_code(ws) == 4001


async def test_expired_token_closes_4001(served):
    _, base = served
    async with connect(base + _token(now=time.time() - 1000, ttl=60)) as ws:
        assert await _close_code(ws) == 4001


async def test_replayed_nonce_closes_4002():
    hub = VoiceHub(SECRET, ledger=MemoryNonceLedger())
    server, base = await _start(hub)
    try:
        token = _token("alice")
        async with connect(base + token) as ws:
            await _recv_topic(ws, "hello")
        async with connect(base + token) as ws:
            assert await _close_code(ws) == 4002
    finally:
        server.close()
        await server.wait_closed()


async def test_single_active_speaker_and_reuse_after_leave(served):
    hub, base = served
    first = await connect(base + _token("a"))
    await _recv_topic(first, "hello")
    async with connect(base + _token("b")) as second:
        assert await _close_code(second) == 4003
    await first.close()
    room = hub.room(ROOM)
    await _wait_until(lambda: room.speaker is None)
    async with connect(base + _token("c")) as third:
        assert (await _recv_topic(third, "hello"))["identity"] == "c"


async def test_duplicate_identity_closes_4012(served):
    _, base = served
    first = await connect(base + _token("dup"))
    await _recv_topic(first, "hello")
    async with connect(base + _token("dup", mode="observer")) as second:
        assert await _close_code(second) == 4012
    await first.close()


async def test_observer_cannot_send_binary_or_chat(served):
    _, base = served
    async with connect(base + _token("obs", mode="observer")) as ws:
        await ws.send(encode_binary(KIND_MIC, b"\x00\x00"))
        assert await _close_code(ws) == 4004
    async with connect(base + _token("obs2", mode="observer")) as ws:
        await ws.send(encode_text("lk.chat", {"text": "hi"}))
        assert await _close_code(ws) == 4004


async def test_observer_ping_gets_pong_and_limit_is_enforced():
    hub = VoiceHub(SECRET, max_observers=2)
    server, base = await _start(hub)
    try:
        observers = [await connect(base + _token(f"o{i}", mode="observer")) for i in range(2)]
        for ws in observers:
            await _recv_topic(ws, "hello")
        await observers[0].send(encode_text("ping", None))
        assert await _recv_topic(observers[0], "pong") is None
        async with connect(base + _token("o3", mode="observer")) as ws:
            assert await _close_code(ws) == 1013
        for ws in observers:
            await ws.close()
    finally:
        server.close()
        await server.wait_closed()


async def test_mic_frames_arrive_in_order(served):
    hub, base = served
    room = hub.room(ROOM)
    frames_sent = [bytes([i]) * 1024 for i in range(5)]
    async with connect(base + _token("mic")) as ws:
        await _recv_topic(ws, "hello")
        for chunk in frames_sent:
            await ws.send(encode_binary(KIND_MIC, chunk))
        got = []
        async for item in room.mic_frames():
            got.append(item)
            if len(got) == len(frames_sent):
                break
    assert got == frames_sent


async def test_barge_packet_bounds(served):
    hub, base = served
    room = hub.room(ROOM)
    with pytest.raises(ValueError):
        await room.local_participant.publish_data(b"x" * 257, topic=BARGE_TOPIC)
    async with connect(base + _token("obs", mode="observer")) as ws:
        await _recv_topic(ws, "hello")
        packet = {"kind": "reply_start", "user_seq": 2, "reply_seq": 1}
        await room.local_participant.publish_data(
            json.dumps(packet).encode(), topic=BARGE_TOPIC
        )
        assert await _recv_topic(ws, BARGE_TOPIC) == packet
    with pytest.raises(ValueError):
        await room.local_participant.publish_data(
            json.dumps({"kind": "user_start", "user_seq": 1, "pad": "x" * 300}).encode(),
            topic=BARGE_TOPIC,
        )


async def test_garbage_frames_dropped_connection_survives(served):
    hub, base = served
    room = hub.room(ROOM)
    frames.dropped.clear()
    async with connect(base + _token("mic"), max_size=None) as ws:
        await _recv_topic(ws, "hello")
        await ws.send(b"")
        await ws.send(bytes([KIND_MIC]) + b"\x00" * (64 * 1024))
        await ws.send(b"not a frame at all")
        await ws.send("{not json")
        await ws.send(encode_binary(KIND_MIC, b"\x01\x00"))
        got = await asyncio.wait_for(room.mic_frames().__anext__(), 3)
    assert got == b"\x01\x00"
    assert frames.dropped["oversize"] >= 1


async def test_audio_order_then_text(served):
    hub, base = served
    room = hub.room(ROOM)
    async with connect(base + _token("obs", mode="observer")) as ws:
        await _recv_topic(ws, "hello")
        await _wait_until(lambda: len(room.observers) == 1)
        room.send_audio(b"\x01\x00" * 50)
        room.send_audio(b"\x02\x00" * 50)
        room.send_text("state", {"agent": "speaking"})
        first = await _recv_binary(ws)
        second = await _recv_binary(ws)
        state = await _recv_topic(ws, "state")
    assert first == (KIND_AUDIO, b"\x01\x00" * 50)
    assert second == (KIND_AUDIO, b"\x02\x00" * 50)
    assert state == {"agent": "speaking"}


async def test_flush_emits_frame_and_drops_queued_audio(served):
    hub, base = served
    room = hub.room(ROOM)
    async with connect(base + _token("obs", mode="observer")) as ws:
        await _recv_topic(ws, "hello")
        await _wait_until(lambda: len(room.observers) == 1)
        # One second of 24 kHz audio per chunk: only the first goes out before pacing.
        for _ in range(10):
            room.send_audio(b"\x00\x00" * 24000)
        await asyncio.sleep(0.05)
        room.flush_playback()
        assert await _recv_topic(ws, "flush") is None
        late = []
        try:
            while True:
                msg = await asyncio.wait_for(ws.recv(), 0.5)
                if isinstance(msg, bytes):
                    late.append(msg)
        except asyncio.TimeoutError:
            pass
    assert len(late) == 0


async def test_slow_client_does_not_stall_producer_or_others():
    hub = VoiceHub(SECRET, send_timeout=0.5, telemetry_limit=50)
    server, base = await _start(hub)
    room = hub.room(ROOM)
    try:
        slow = await connect(base + _token("slow", mode="observer"), max_size=None)
        fast = await connect(base + _token("fast", mode="observer"))
        await _recv_topic(fast, "hello")
        await _wait_until(lambda: len(room.observers) == 2)
        payload = "x" * 8000
        started = time.monotonic()
        for n in range(1000):
            room.send_text("caption", {"n": n, "text": payload})
        room.send_text("state", {"done": True})
        assert time.monotonic() - started < 2.0
        assert hub.stats["dropped_telemetry"] > 0
        assert await _recv_topic(fast, "state", timeout=5) == {"done": True}
        await slow.close()
    finally:
        await fast.close()
        server.close()
        await server.wait_closed()


async def test_room_close_sends_4011(served):
    hub, base = served
    room = hub.room(ROOM)
    async with connect(base + _token("obs", mode="observer")) as ws:
        await _recv_topic(ws, "hello")
        await room.close()
        assert await _close_code(ws) == 4011
    assert room.isconnected() is False
