""" "Hey Jarvis" always wins: chess goes deaf, never mutes the wake client, resumes by itself."""

import asyncio
import json
import time

import numpy as np
import pytest
from test_chess_voice_engine import FakeVoice, fake_speaker  # noqa: F401

from games.chess.commentary import Cancelled
from games.chess.engine import MiniEngine
from games.chess.game import ChessGame
from games.chess.settings import ChessSettings
from games.voice.engine import VoiceEngine
from games.voice.wake_watch import WakeWatcher, query_status


class SlowSTT:
    def __init__(self, text, delay=0.0):
        self.text, self.delay = text, delay

    def load(self):
        return 0.0

    async def transcribe(self, audio):
        await asyncio.sleep(self.delay)
        return self.text


class U:
    audio = np.zeros(8000, dtype=np.float32)

    def __init__(self):
        self.start = self.end = time.monotonic()


async def build(
    tmp_path, text="pawn to e4", delay=0.0, watcher=None, time_control="untimed"
):
    game = ChessGame(
        ChessSettings(commentary_muted=False, announce_moves=False, difficulty="novice"),
        engine=MiniEngine(1),
        save_dir=tmp_path,
        autosave=False,
    )
    sp, sink = fake_speaker()
    eng = VoiceEngine(
        game,
        stt=SlowSTT(text, delay),
        speaker=sp,
        capture=None,
        watcher=watcher,
        chime=False,
    )
    await game.new_game(time_control=time_control)
    await eng.start(load_models=False)
    return eng, game, sp, sink


async def test_wake_mid_utterance_plays_no_move(tmp_path):
    eng, game, _sp, _sink = await build(tmp_path, "pawn to e4", delay=0.3)
    await eng._q.put(U())
    await asyncio.sleep(0.1)  # whisper is still working on it
    eng.on_wake()
    await asyncio.sleep(0.6)
    assert game.state.move_count == 0
    assert eng.paused and game.jarvis_active
    await eng.stop()
    await game.close()


async def test_wake_mid_piper_stops_speech_under_200ms(tmp_path):
    eng, game, sp, _sink = await build(tmp_path)
    await eng.say(
        "A long explanation that Sir will hear only a part of, because Jarvis is calling."
    )
    await asyncio.sleep(0.4)
    assert sp.speaking
    t0 = time.monotonic()
    eng.on_wake()
    while sp.speaking and time.monotonic() - t0 < 1:
        await asyncio.sleep(0.005)
    assert time.monotonic() - t0 < 0.2
    assert not game.speaking
    assert eng._speech_q.empty()
    await eng.stop()
    await game.close()


async def test_hey_jarvis_transcribed_by_chess_is_discarded_and_pauses(tmp_path):
    woke = []
    eng, game, _sp, sink = await build(
        tmp_path, "Hey Jarvis, what's the weather", watcher=None
    )
    await eng._q.put(U())
    await asyncio.sleep(0.3)
    assert eng.paused and game.jarvis_active
    assert game.state.move_count == 0 and game.log == [] and not sink.chunks
    assert game.banner == "JARVIS ACTIVE — chess listening paused"
    assert game.mic_state == "paused"
    _ = woke
    await eng.stop()
    await game.close()


async def test_transcript_backstop_uses_the_watcher_when_present(tmp_path):
    events = []
    watcher = WakeWatcher(
        lambda: events.append("wake"), lambda: events.append("resume")
    )
    eng, game, _sp, _sink = await build(
        tmp_path, "OK Jarvis what time is it", watcher=watcher
    )
    await eng._q.put(U())
    await asyncio.sleep(0.3)
    assert watcher.paused and eng.paused
    await eng.stop()
    await game.close()


async def test_nothing_said_while_paused_becomes_a_move(tmp_path):
    eng, game, _sp, _sink = await build(tmp_path, "pawn to e4")
    eng.on_wake()
    for _ in range(3):
        eng.on_frame(np.ones(512, dtype=np.float32) * 0.5, time.monotonic())
    await eng._q.put(U())
    await asyncio.sleep(0.3)
    assert game.state.move_count == 0
    r = await game.handle_text("pawn to e4")
    assert not r.ok and r.kind == "deaf"
    await eng.stop()
    await game.close()


async def test_clock_is_paused_while_jarvis_is_active(tmp_path):
    from games.chess.state import Clock

    t = [100.0]
    game = ChessGame(
        ChessSettings(commentary_muted=False, announce_moves=False),
        engine=MiniEngine(1),
        save_dir=tmp_path,
        autosave=False,
        now=lambda: t[0],
    )
    await game.new_game(time_control="10+0")
    await game.handle_text("e4")
    await game.drain(5)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if game.state.board.turn == game.state.human_colour:
            break
    t[0] += 5
    game.jarvis_wake()
    left = game.state.time_left(game.state.human_colour)
    t[0] += 600  # a long chat with Jarvis
    assert game.state.time_left(game.state.human_colour) == pytest.approx(left)
    assert game.snapshot()["clock"]["paused"] and game.status() == "JARVIS ACTIVE"
    game.jarvis_done()
    t[0] += 3
    assert game.state.time_left(game.state.human_colour) == pytest.approx(left - 3)
    assert not game.snapshot()["clock"]["paused"]
    assert Clock  # imported for clarity
    await game.close()


# ------------------------------------------------------------------ the sockets
class FakeSocketServer:
    """A Unix socket server recording what it receives (wake.sock stand-in)."""

    def __init__(self, path, reply=None):
        self.path, self.received, self.reply = (
            path,
            [],
            reply or {"ok": True, "muted": False, "in_call": False},
        )
        self.server = None

    async def start(self):
        async def handle(r, w):
            data = await r.read(4096)
            self.received.append(json.loads(data.decode()))
            w.write(json.dumps(self.reply).encode())
            await w.drain()
            w.close()

        self.server = await asyncio.start_unix_server(handle, str(self.path))

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()


async def test_the_wake_client_is_never_sent_a_mute(tmp_path):
    sock = FakeSocketServer(tmp_path / "wake.sock")
    await sock.start()
    watcher = WakeWatcher(
        lambda: None,
        lambda: None,
        status_fn=lambda: query_status(tmp_path / "wake.sock"),
        poll_s=0.03,
        settle_s=0.0,
    )
    eng, game, _sp, _sink = await build(tmp_path, "pawn to e4", watcher=watcher)
    await eng._q.put(U())
    await asyncio.sleep(0.2)
    watcher.wake()  # a whole scripted game with a wake in the middle
    await asyncio.sleep(0.4)
    watcher.resume_now()
    await game.handle_text("quit chess")
    await eng.stop()  # quit
    await game.close()
    assert sock.received, "the status poll should have run"
    assert all(m == {"status": True} for m in sock.received)
    assert not any("mute" in m for m in sock.received)
    await sock.stop()


async def test_crash_path_also_never_mutes(tmp_path):
    sock = FakeSocketServer(tmp_path / "wake.sock")
    await sock.start()
    watcher = WakeWatcher(
        lambda: None,
        lambda: None,
        status_fn=lambda: query_status(tmp_path / "wake.sock"),
        poll_s=0.03,
        settle_s=0.0,
    )
    eng, game, _sp, _sink = await build(tmp_path, "pawn to e4", watcher=watcher)
    watcher.wake()
    await asyncio.sleep(0.2)
    # "crash": tear down without any clean shutdown call
    for t in eng._tasks + watcher._tasks:
        t.cancel()
    await asyncio.sleep(0.05)
    assert not any("mute" in m for m in sock.received)
    await sock.stop()
    await game.close()


async def test_call_ended_resumes_after_the_grace_period(tmp_path):
    ev = tmp_path / "wake-events.sock"
    clients = []

    async def handle(r, w):
        clients.append(w)
        await r.read()

    server = await asyncio.start_unix_server(handle, str(ev))
    resumed, woke = [], []
    t0 = [0.0]
    watcher = WakeWatcher(
        lambda: woke.append(time.monotonic()),
        lambda: resumed.append(time.monotonic()),
        events=ev,
        status_fn=lambda: asyncio.sleep(0, {"in_call": True}),
        poll_s=0.5,
    )
    eng, game, _sp, _sink = await build(tmp_path, watcher=watcher)
    real_done = game.jarvis_done
    game.jarvis_done = lambda: (resumed.append(time.monotonic()), real_done())[1]
    for _ in range(100):
        if clients:
            break
        await asyncio.sleep(0.02)
    w = clients[0]
    w.write(json.dumps({"event": "wake", "ts": 1}).encode() + b"\n")
    await w.drain()
    await asyncio.sleep(0.2)
    assert eng.paused and game.jarvis_active
    t_end = time.monotonic()
    w.write(json.dumps({"event": "call_ended", "ts": 2}).encode() + b"\n")
    await w.drain()
    await asyncio.sleep(1.0)
    assert eng.paused  # still inside the 1.5 s grace
    await asyncio.sleep(0.9)
    assert not eng.paused and not game.jarvis_active
    assert 1.4 <= resumed[-1] - t_end <= 2.4
    _ = t0
    await eng.stop()
    await game.close()
    server.close()


async def test_missing_call_ended_resumes_via_the_status_poll(tmp_path):
    states = iter([{"in_call": True}, {"in_call": True}] + [{"in_call": False}] * 50)

    async def status():
        return next(states)

    resumed = []
    watcher = WakeWatcher(
        lambda: None,
        lambda: resumed.append(1),
        status_fn=status,
        poll_s=0.05,
        settle_s=0.05,
        grace=0.1,
    )
    watcher._tasks = [asyncio.ensure_future(watcher._poll())]
    watcher.wake()
    await asyncio.sleep(0.2)
    assert not resumed  # Jarvis still reports in_call
    await asyncio.sleep(0.6)
    assert resumed
    for t in watcher._tasks:
        t.cancel()


async def test_unreachable_wake_sock_does_not_leave_chess_deaf(tmp_path):
    resumed = []

    async def status():
        return None  # Jarvis is not running at all

    watcher = WakeWatcher(
        lambda: None,
        lambda: resumed.append(1),
        status_fn=status,
        poll_s=0.04,
        settle_s=0.05,
        grace=0.05,
    )
    watcher._tasks = [asyncio.ensure_future(watcher._poll())]
    watcher.wake()
    await asyncio.sleep(0.5)
    assert resumed
    for t in watcher._tasks:
        t.cancel()


async def test_after_ten_minutes_only_resume_chess_gets_through(tmp_path):
    status_calls = []

    async def status():
        status_calls.append(1)
        return {"in_call": True}  # we never learn the call ended

    watcher = WakeWatcher(
        lambda: None,
        lambda: None,
        status_fn=status,
        poll_s=0.03,
        settle_s=999,
        deaf_limit=0.1,
    )
    eng, game, _sp, _sink = await build(tmp_path, "pawn to e4", watcher=watcher)
    watcher._tasks.append(asyncio.ensure_future(watcher._poll()))
    watcher.wake()
    await asyncio.sleep(0.4)
    assert eng.resume_only
    assert "Say 'resume chess'" in (game.banner or "")
    # ordinary talk is ignored, "resume chess" resumes
    eng.paused = False  # the engine hears again, but only for the magic words
    eng.stt.text = "pawn to e4"
    await eng._q.put(U())
    await asyncio.sleep(0.2)
    assert game.state.move_count == 0
    eng.stt.text = "resume chess"
    await eng._q.put(U())
    await asyncio.sleep(0.3)
    assert not eng.paused and not eng.resume_only and not watcher.paused
    for t in watcher._tasks:
        t.cancel()
    await eng.stop()
    await game.close()


async def test_space_key_resumes_immediately(tmp_path):
    watcher = WakeWatcher(lambda: None, lambda: None)
    eng, game, _sp, _sink = await build(tmp_path, watcher=watcher)
    watcher.wake()
    assert eng.paused
    eng.manual_resume()
    assert not eng.paused and not game.jarvis_active
    await eng.stop()
    await game.close()


async def test_wake_cancels_in_flight_analysis_and_http(tmp_path):
    import httpx

    from games.chess.commentary import Commentary, OpenRouter, Quota

    release = asyncio.Event()
    hits = []

    async def handler(req):
        hits.append(1)
        await release.wait()
        return httpx.Response(500)

    router = OpenRouter(
        ["m/x:free"],
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        quota=Quota(tmp_path / "q.json"),
        key="sk-or-x",
    )
    game = ChessGame(
        ChessSettings(),
        engine=MiniEngine(1),
        commentary=Commentary(router),
        save_dir=tmp_path,
        autosave=False,
    )
    await game.new_game()
    task = asyncio.ensure_future(router.complete([{"role": "user", "content": "x"}]))
    for _ in range(50):
        if hits:
            break
        await asyncio.sleep(0.01)
    assert hits
    game.jarvis_wake()
    await asyncio.sleep(0.02)
    assert not router._inflight
    with pytest.raises(Cancelled):
        await task
    release.set()
    await game.close()
