"""Data topics end-to-end over a real hub (port 0): inbound speaker topics,
observer rejection, unknown-topic accounting and agent->client broadcasts."""

import asyncio
import json
import time

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from jarvis_rt.transport import frames
from jarvis_rt.transport.client import VoiceClient
from jarvis_rt.transport.frames import BARGE_TOPIC, decode_text, encode_text
from jarvis_rt.transport.server import VoiceHub
from jarvis_rt.transport.tokens import mint_call_token

SECRET = b"t" * 32
ROOM = "jarvis-topics"


def _token(identity="u", mode="speaker"):
    return mint_call_token(ROOM, identity, mode, 60, None, SECRET, now=time.time())


async def _start(hub):
    server = await hub.serve("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}/voice?token="


async def _recv_topic(ws, topic, timeout=3.0):
    async def _loop():
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                continue
            decoded = decode_text(msg)
            if decoded and decoded[0] == topic:
                return decoded[1]

    return await asyncio.wait_for(_loop(), timeout)


async def _close_code(ws, timeout=3.0):
    async def _drain():
        while True:
            await ws.recv()

    with pytest.raises(ConnectionClosed) as exc:
        await asyncio.wait_for(_drain(), timeout)
    return exc.value.rcvd.code if exc.value.rcvd else None


async def _wait_until(predicate, timeout=3.0):
    async def _loop():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_loop(), timeout)


@pytest.fixture
async def served():
    hub = VoiceHub(SECRET, clock=time.time)
    server, base = await _start(hub)
    yield hub, base
    server.close()
    await server.wait_closed()


async def test_speaker_turn_frame_reaches_job_callback(served):
    hub, base = served
    room = hub.room(ROOM)
    got = []
    room.on_topic("jarvis.turn", got.append)
    turn = {"type": "jarvis_turn", "event": "cancel_all", "client_id": "phone"}
    async with connect(base + _token("spk")) as ws:
        await _recv_topic(ws, "hello")
        await ws.send(encode_text("jarvis.turn", turn))
        await _wait_until(lambda: got)
    assert got == [turn]


async def test_turn_frame_without_jarvis_turn_type_is_dropped(served):
    hub, base = served
    room = hub.room(ROOM)
    got = []
    room.on_topic("jarvis.turn", got.append)
    async with connect(base + _token("spk")) as ws:
        await _recv_topic(ws, "hello")
        await ws.send(encode_text("jarvis.turn", {"type": "other"}))
        await ws.send(encode_text("jarvis.turn", "not-a-dict"))
        await _wait_until(lambda: hub.stats["dropped_malformed"] >= 2)
    assert got == []


async def test_speaker_valid_barge_delivered_oversize_dropped(served):
    hub, base = served
    room = hub.room(ROOM)
    got = []
    room.on_topic(BARGE_TOPIC, got.append)
    frames.dropped.clear()
    valid = {"kind": "user_start", "user_seq": 3}
    oversize = {"kind": "user_start", "user_seq": 4, "pad": "x" * 300}
    async with connect(base + _token("spk")) as ws:
        await _recv_topic(ws, "hello")
        # Raw oversize text is rejected by the frame decoder before dispatch.
        await ws.send(json.dumps({"topic": BARGE_TOPIC, "payload": oversize}))
        await ws.send(encode_text(BARGE_TOPIC, valid))
        await _wait_until(lambda: got)
    assert got == [valid]
    assert frames.dropped["malformed"] >= 1


async def test_observer_send_closes_4004(served):
    _hub, base = served
    async with connect(base + _token("obs", mode="observer")) as ws:
        await _recv_topic(ws, "hello")
        await ws.send(encode_text("jarvis.turn", {"type": "jarvis_turn"}))
        assert await _close_code(ws) == 4004
    async with connect(base + _token("obs2", mode="observer")) as ws:
        await _recv_topic(ws, "hello")
        await ws.send(encode_text(BARGE_TOPIC, {"kind": "user_start", "user_seq": 1}))
        assert await _close_code(ws) == 4004


async def test_unknown_topic_is_dropped_and_counted(served):
    hub, base = served
    room = hub.room(ROOM)
    seen = []
    room.on_topic("jarvis.turn", seen.append)
    hub.stats.clear()
    async with connect(base + _token("spk")) as ws:
        await _recv_topic(ws, "hello")
        await ws.send(encode_text("jarvis-tasks", {"kind": "spoof"}))
        await ws.send(encode_text("made.up", 1))
        await _wait_until(lambda: hub.stats["dropped_forbidden_topic"] == 2)
    assert seen == []


def test_on_topic_rejects_outbound_only_topics():
    hub = VoiceHub(SECRET)
    with pytest.raises(ValueError):
        hub.room(ROOM).on_topic("jarvis-tasks", lambda _p: None)


async def test_tasks_broadcast_reaches_speaker_and_observer(served):
    hub, base = served
    room = hub.room(ROOM)
    payload = json.dumps({"kind": "tool_start", "tool": "nmap_scan"})
    async with connect(base + _token("spk")) as spk:
        await _recv_topic(spk, "hello")
        async with connect(base + _token("obs", mode="observer")) as obs:
            await _recv_topic(obs, "hello")
            await _wait_until(lambda: len(room.observers) == 1)
            await room.local_participant.publish_data(payload, topic="jarvis-tasks")
            assert await _recv_topic(spk, "jarvis-tasks") == json.loads(payload)
            assert await _recv_topic(obs, "jarvis-tasks") == json.loads(payload)


async def test_barge_ack_publish_reaches_observer(served):
    hub, base = served
    room = hub.room(ROOM)
    packet = {"kind": "reply_start", "user_seq": 2, "reply_seq": 1}
    async with connect(base + _token("obs", mode="observer")) as obs:
        await _recv_topic(obs, "hello")
        await _wait_until(lambda: len(room.observers) == 1)
        await room.local_participant.publish_data(
            json.dumps(packet).encode(), topic=BARGE_TOPIC
        )
        assert await _recv_topic(obs, BARGE_TOPIC) == packet


async def test_client_send_topic_delivers_turn_to_job(served):
    hub, base = served
    room = hub.room(ROOM)
    got = []
    room.on_topic("jarvis.turn", got.append)
    turn = {"type": "jarvis_turn", "event": "external_seed", "text": "hi"}
    client = VoiceClient(base.split("?")[0], lambda: _token("spk"), backoff_base=0.01)
    task = client.start()
    try:
        await client.wait_connected(timeout=3)
        assert await client.send_topic("jarvis.turn", turn) is True
        await _wait_until(lambda: got)
    finally:
        await client.close()
        await asyncio.wait({task}, timeout=3)
    assert got == [turn]
