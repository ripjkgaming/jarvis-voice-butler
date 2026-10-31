import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from jarvis_rt.transport.client import VoiceClient
from jarvis_rt.transport.tokens import mint_call_token
from jarvis_rt.worker import AgentServer, get_job_context


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    return tmp_path


def test_job_runs_per_speaker_room_and_shutdown_callbacks_once(home):
    from jarvis_rt.transport.tokens import load_secret

    async def run():
        server = AgentServer()
        seen = {}

        @server.rtc_session(agent_name="my-agent")
        async def entry(ctx):
            seen["ctx"] = get_job_context()
            seen["room"] = ctx.room.name
            seen["who"] = (await ctx.wait_for_participant("jarvis-master")).attributes
            ctx.add_shutdown_callback(lambda: seen.setdefault("down", []).append(1))
            await asyncio.sleep(0.2)

        listener = await server.serve(port=0)
        port = listener.sockets[0].getsockname()[1]
        secret = load_secret(home)
        tok = mint_call_token(
            "jarvis-1", "jarvis-master", "speaker", 60, {"jarvis.wake": "wake"}, secret
        )
        client = VoiceClient(f"ws://127.0.0.1:{port}/voice", tok)
        client.start()
        await client.wait_connected(5)
        await asyncio.sleep(0.5)
        await client.close()
        listener.close()
        return seen

    seen = asyncio.run(run())
    assert seen["room"] == "jarvis-1" and seen["ctx"].room.name == "jarvis-1"
    assert seen["who"]["jarvis.wake"] == "wake"
    assert seen["down"] == [1]


def test_room_io_end_to_end_mic_audio_barge_and_captions(home):
    """mic PCM in -> backend; agent audio -> client; interrupt -> flush; captions paced."""
    from test_rt_session import FakeBackend

    from jarvis_rt import Agent, AgentSession
    from jarvis_rt.room_io import RoomOptions
    from jarvis_rt.transport.tokens import load_secret

    async def run():
        server = AgentServer()
        out = {}

        @server.rtc_session(agent_name="my-agent")
        async def entry(ctx):
            be = FakeBackend()
            s = AgentSession(backend=be)
            await s.start(
                Agent(instructions="x"), room=ctx.room, room_options=RoomOptions()
            )
            out["be"], out["s"] = be, s
            await asyncio.sleep(1.0)
            await s.aclose()

        listener = await server.serve(port=0)
        port = listener.sockets[0].getsockname()[1]
        tok = mint_call_token(
            "jarvis-2", "jarvis-master", "speaker", 60, {}, load_secret(home)
        )
        client = VoiceClient(f"ws://127.0.0.1:{port}/voice", tok)
        client.start()
        await client.wait_connected(5)
        flushes, caps = [], []
        client.on_topic("flush", flushes.append)
        client.on_topic("caption", caps.append)
        await client.send_mic(b"\x01" * 1024)
        await asyncio.sleep(0.15)
        s, be = out["s"], out["be"]
        s.backend_event("agent_audio", pcm24k=b"\x02" * 960)
        s.backend_event("agent_text", text="hello sir")
        await asyncio.sleep(0.15)
        got_audio = [f async for f in _take(client, 1)]
        s.backend_event("user_speech_started")
        s.backend_event("interrupted")
        await asyncio.sleep(0.2)
        await client.close()
        listener.close()
        return be, got_audio, flushes, caps

    be, audio, flushes, _caps = asyncio.run(run())
    assert ("audio", 1024) in be.log
    assert audio and audio[0] == b"\x02" * 960
    assert flushes, "interrupt must flush the client playback queue"


async def _take(client, n):
    out = []
    it = client.audio_frames()
    try:
        while len(out) < n:
            out.append(await asyncio.wait_for(it.__anext__(), 1.0))
    except (asyncio.TimeoutError, StopAsyncIteration):
        pass
    yield_all = out
    for x in yield_all:
        yield x


def test_active_call_file_tracks_job_lifetime(home):
    import json

    from jarvis_rt.transport.tokens import load_secret

    async def run():
        server = AgentServer()
        seen = {}

        @server.rtc_session(agent_name="my-agent")
        async def entry(ctx):
            seen["during"] = json.loads((home / "voice_active.json").read_text())
            await asyncio.sleep(0.05)

        listener = await server.serve(port=0)
        port = listener.sockets[0].getsockname()[1]
        tok = mint_call_token(
            "jarvis-3", "jarvis-master", "speaker", 60, {}, load_secret(home)
        )
        client = VoiceClient(f"ws://127.0.0.1:{port}/voice", tok)
        client.start()
        await client.wait_connected(5)
        await asyncio.sleep(0.5)
        await client.close()
        for _ in range(100):
            after = json.loads((home / "voice_active.json").read_text())
            if not after["active"]:
                break
            await asyncio.sleep(0.05)
        listener.close()
        return seen, after

    seen, after = asyncio.run(run())
    assert seen["during"]["active"] is True and seen["during"]["rooms"] == ["jarvis-3"]
    assert after["active"] is False


def _connect(server, listener, home, room, identity="jarvis-master"):
    from jarvis_rt.transport.tokens import load_secret

    port = listener.sockets[0].getsockname()[1]
    tok = mint_call_token(room, identity, "speaker", 60, {}, load_secret(home))
    return VoiceClient(f"ws://127.0.0.1:{port}/voice", tok)


def test_blocked_entry_is_cancelled_when_speaker_leaves(home):
    """A job stuck mid-setup must not keep the call marked active after hangup."""

    async def run():
        server = AgentServer()
        cleaned = []

        @server.rtc_session(agent_name="my-agent")
        async def entry(ctx):
            ctx.add_shutdown_callback(lambda: cleaned.append(1))
            await asyncio.Event().wait()

        listener = await server.serve(port=0)
        client = _connect(server, listener, home, "jarvis-2")
        client.start()
        await client.wait_connected(5)
        await asyncio.sleep(0.2)
        assert server.active_call_exists()
        await client.close()
        for _ in range(100):
            if not server._jobs:
                break
            await asyncio.sleep(0.05)
        listener.close()
        return server.active_call_exists(), cleaned

    active, cleaned = asyncio.run(run())
    assert active is False
    assert cleaned == [1]


def test_returned_entry_keeps_call_until_speaker_leaves(home):
    """Entry returning after session start must not end the call by itself."""

    async def run():
        server = AgentServer()
        cleaned = []

        @server.rtc_session(agent_name="my-agent")
        async def entry(ctx):
            ctx.add_shutdown_callback(lambda: cleaned.append(1))

        listener = await server.serve(port=0)
        client = _connect(server, listener, home, "jarvis-3")
        client.start()
        await client.wait_connected(5)
        await asyncio.sleep(0.3)
        mid = (server.active_call_exists(), list(cleaned))
        await client.close()
        for _ in range(100):
            if not server._jobs:
                break
            await asyncio.sleep(0.05)
        listener.close()
        return mid, server.active_call_exists(), cleaned

    mid, active, cleaned = asyncio.run(run())
    assert mid == (True, [])
    assert active is False and cleaned == [1]


def test_stale_active_file_from_dead_worker_is_ignored(home):
    import json

    from mint_token import active_call_exists

    (home / "voice_active.json").write_text(
        json.dumps({"active": True, "rooms": ["r"], "pid": 2**22 + 12345})
    )
    assert active_call_exists(home) is False
    (home / "voice_active.json").write_text(
        json.dumps({"active": True, "rooms": ["r"], "pid": __import__("os").getpid()})
    )
    assert active_call_exists(home) is True


def test_call_not_active_once_speaker_left_even_if_teardown_hangs(home):
    """A stuck entry or shutdown callback must not make wake think a call is live."""

    async def run():
        server = AgentServer()
        server.teardown_timeout = 0.3

        @server.rtc_session(agent_name="my-agent")
        async def entry(ctx):
            async def hang():
                await asyncio.sleep(5)

            ctx.add_shutdown_callback(hang)
            swallowed = 0
            while swallowed < 2:  # ignores cancellation, then lets the loop exit
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    swallowed += 1

        listener = await server.serve(port=0)
        client = _connect(server, listener, home, "jarvis-4")
        client.start()
        await client.wait_connected(5)
        await asyncio.sleep(0.2)
        assert server.active_call_exists()
        await client.close()
        await asyncio.sleep(0.2)
        inactive_fast = not server.active_call_exists()
        for _ in range(100):
            if "jarvis-4" not in server._jobs:
                break
            await asyncio.sleep(0.05)
        gone = "jarvis-4" not in server._jobs
        listener.close()
        return inactive_fast, gone

    inactive_fast, gone = asyncio.run(run())
    assert inactive_fast is True
    assert gone is True
