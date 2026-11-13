"""Simulated end-to-end calls: FakeDriver + fake audio + scripted caller speech.

No WhatsApp page, no pulse, no real STT/TTS/LLM. Stores are the real W1 modules
on tmp dirs (conftest). Audio goes through the real Ears (endpointer) path.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from calls import (
    calllog,
    contacts,
    events,
    hud_feed,
    rules,
    runtime,
    store,
)
from calls.wa_call import FakeDriver

LOUD = (np.ones(512) * 12000).astype("<i2").tobytes()
QUIET = bytes(1024)


class LevelVAD:
    def reset(self):
        pass

    def prob(self, frame):
        return 1.0 if float(np.abs(frame).max()) > 0.1 else 0.0


class FakeRoute:
    sink_in, sink_out = "t_in", "t_out"

    def __init__(self):
        self.up = 0
        self.down = 0

    def setup(self):
        self.up += 1
        return True

    def teardown(self):
        self.down += 1
        return True

    def route_streams(self):
        return {}


class FakeBridge:
    def __init__(self):
        self.on_audio = None
        self.played = []
        self.flushed = 0
        self.stopped = False
        self.pending_ms = 0

    async def start(self, on_audio):
        self.on_audio = on_audio

    async def play(self, pcm):
        self.played.append(pcm)

    async def flush(self):
        self.flushed += 1

    async def stop(self):
        self.stopped = True

    def say_to_us(self):
        """Caller speaks one utterance: loud frames then silence."""
        for _ in range(8):
            self.on_audio(LOUD)
        for _ in range(30):
            self.on_audio(QUIET)


@pytest.fixture
def sink():
    got = []
    events.set_sink(got.append)
    yield got
    events.set_sink(None)
    events.set_active(None)


def make_runtime(driver, script, replies, bridge, route, **kw):
    """script: caller utterances in order; replies: LLM replies in order."""
    said = []
    texts = iter(script)

    async def llm(system, history):
        if "Summarise" in system:
            return "Unknown caller asked for payment; escalated to owner."
        return replies.pop(0) if replies else "Okay."

    def synth(text, persona):
        said.append((text, persona))
        return b"\x01\x00" * 480

    rt = runtime.CallRuntime(
        driver=driver,
        llm=llm,
        synth=synth,
        transcribe=lambda audio: next(texts),
        vad_factory=LevelVAD,
        route_factory=lambda: route,
        bridge_factory=lambda r: bridge,
        poll_s=0.02,
        **kw,
    )
    rt.said = said
    return rt


async def until(pred, timeout=5.0):
    t0 = time.monotonic()
    while not pred():
        if time.monotonic() - t0 > timeout:
            raise AssertionError("timeout waiting for condition")
        await asyncio.sleep(0.01)


def kinds(sink, name):
    return [e for e in sink if e.get("event") == name]


async def test_inbound_unknown_answer_escalate_takeover_end(sink):
    rules.add_rule("answer_unknown", duration=3600, message="")
    drv, bridge, route = FakeDriver(), FakeBridge(), FakeRoute()
    rt = make_runtime(
        drv,
        [
            "Hi, I need to talk to someone about a parcel",
            "Please send me the money now",
        ],
        ["Sure, may I take your name?"],
        bridge,
        route,
    )
    await rt.start()
    try:
        drv.ring("+6591234567")
        await until(lambda: rt.session is not None and bridge.on_audio is not None)
        sess = rt.session
        await until(lambda: sess.state == "handling" and rt.said)

        # disclosure is spoken first, default settings: disclose_ai on, record off
        assert "AI assistant" in rt.said[0][0]
        assert "recorded" not in rt.said[0][0]
        st = kinds(sink, "state")[-1]
        assert st["ai_disclosed"] is True and st["recording"] is False
        assert st["caller"]["trust"] == "unknown" and st["direction"] == "in"
        assert route.up == 1 and ("accept", "") in drv.calls

        # caller turn 1 -> real endpointer -> fake stt -> llm -> tts -> bridge.play
        n_played = len(bridge.played)
        bridge.say_to_us()
        await until(lambda: len(bridge.played) > n_played)
        assert rt.said[-1][0] == "Sure, may I take your name?"

        # caller turn 2 asks for money: guard escalates, no model reply spoken
        bridge.say_to_us()
        await until(lambda: kinds(sink, "escalation"))
        assert "payment" in kinds(sink, "escalation")[-1]["reason"]

        # owner takes over through the HUD command queue
        hud_feed.queue_command("takeover")
        await until(lambda: sess.state == "owner")
        assert bridge.flushed >= 1
        assert kinds(sink, "state")[-1]["state"] == "owner"

        # caller speech while owner talks: transcribed but agent silent
        said_before = len(rt.said)
        await sess.on_caller_text("hello?")
        assert len(rt.said) == said_before

        await sess.handle_command("hangup")
        await until(lambda: not rt.busy)
    finally:
        await rt.shutdown()

    # route and bridge torn down
    assert route.down == 1 and bridge.stopped
    # summary in calllog + memory vault
    done = calllog.recent(1)[0]
    assert done["outcome"] == "hangup" and "payment" in done["summary"]
    turns = calllog.get_call(done["id"])["turns"]
    assert [t["speaker"] for t in turns].count("caller") >= 2
    assert kinds(sink, "summary")
    audit = {a["kind"] for a in calllog.list_audit(50)}
    assert {"decision", "escalation", "command", "ended", "hud_command"} <= audit


async def test_known_contact_persona_and_vault_summary(sink, tmp_path):
    c = contacts.add_contact("Mum", "+6598765432", "mother", trust="family")
    contacts.update_contact(c.id, policy={"answer": "auto", "persona": "owner"})
    drv, bridge, route = FakeDriver(), FakeBridge(), FakeRoute()
    rt = make_runtime(
        drv, ["Just checking in, call me back"], ["Will do. END"], bridge, route
    )
    await rt.start()
    try:
        drv.ring("Mum")  # display name resolves through contacts
        await until(lambda: rt.session is not None and rt.said)
        assert rt.said[0][1] == "owner"  # persona honoured for TTS
        assert kinds(sink, "state")[0]["mode"] == "owner_voice"
        await until(lambda: bridge.on_audio is not None)
        bridge.say_to_us()
        await until(lambda: not rt.busy)
    finally:
        await rt.shutdown()
    assert calllog.recent(1)[0]["outcome"] == "completed"
    store_sum = calllog.recent(1)[0]["summary"]
    assert store_sum
    cid = calllog.recent(1)[0]["id"]
    from calls import memory_bridge

    assert memory_bridge.write_call_summary(cid) is True
    import memory

    notes = "".join(f.read_text() for f in memory.threads_dir().glob("*.md"))
    assert "Mum" in notes or store_sum[:20] in notes
    assert any("Call (completed)" in f for f in memory_bridge.facts_for(c.id))


async def test_record_notice_and_disclosure_setting(sink):
    store.set_setting("record_calls", "1")
    store.set_setting("disclose_ai", "0")
    rules.add_rule("answer_all", duration=600)
    drv, bridge, route = FakeDriver(), FakeBridge(), FakeRoute()
    rt = make_runtime(drv, [], [], bridge, route)
    await rt.start()
    try:
        drv.ring("+6590001111")
        await until(lambda: rt.said)
        assert "recorded" in rt.said[0][0]
        assert "AI assistant" not in rt.said[0][0]
        assert kinds(sink, "state")[-1]["recording"] is True
        await rt.session.handle_command("hangup")
        await until(lambda: not rt.busy)
    finally:
        await rt.shutdown()


async def test_reject_rule_declines_without_audio_greeting(sink):
    rules.add_rule("reject", duration=600)
    drv, bridge, route = FakeDriver(), FakeBridge(), FakeRoute()
    rt = make_runtime(drv, [], [], bridge, route)
    await rt.start()
    try:
        drv.ring("+6590002222")
        await until(lambda: ("decline", "") in drv.calls)
        await until(lambda: not rt.busy)
    finally:
        await rt.shutdown()
    assert rt.said == []
    assert calllog.recent(1)[0]["outcome"] == "rejected"


async def test_no_rule_leaves_ringing_then_missed(sink):
    drv, bridge, route = FakeDriver(), FakeBridge(), FakeRoute()
    rt = make_runtime(drv, [], [], bridge, route)
    await rt.start()
    try:
        drv.ring("+6590003333")
        await until(lambda: kinds(sink, "escalation"))
        assert ("accept", "") not in drv.calls  # owner decides
        drv.incoming = None  # caller gave up
        await until(lambda: not rt.busy)
    finally:
        await rt.shutdown()
    assert calllog.recent(1)[0]["outcome"] == "missed"


async def test_barge_in_flushes_playback(sink):
    rules.add_rule("answer_all", duration=600)
    drv, bridge, route = FakeDriver(), FakeBridge(), FakeRoute()
    rt = make_runtime(drv, ["stop"], [], bridge, route)
    await rt.start()
    try:
        drv.ring("+6590004444")
        await until(lambda: rt.session is not None and rt.said)
        bridge.pending_ms = 500  # jarvis still speaking
        for _ in range(3):
            bridge.on_audio(LOUD)
        await until(lambda: bridge.flushed >= 1)
        bridge.pending_ms = 0
        await rt.session.handle_command("hangup")
        await until(lambda: not rt.busy)
    finally:
        await rt.shutdown()


async def test_audio_route_failure_skips_call(sink):
    drv, bridge, route = FakeDriver(), FakeBridge(), FakeRoute()
    route.setup = lambda: False
    rt = make_runtime(drv, [], [], bridge, route)
    await rt.start()
    try:
        drv.ring("+6590005555")
        await until(
            lambda: any(a["kind"] == "call_skipped" for a in calllog.list_audit(10))
        )
    finally:
        await rt.shutdown()
    assert rt.session is None and drv.calls == []


# ------------------------------------------------------------- outbound
def test_outbound_scheduled_call_e2e(sink):
    """Scheduler tick -> dial_sync -> runtime dial -> FakeDriver -> goal spoken."""
    from calls import scheduler, voice_commands

    c = contacts.add_contact("Dr Lee", "+6591112222", "doctor", trust="work")
    drv, bridge, route = FakeDriver(), FakeBridge(), FakeRoute()
    rt = make_runtime(
        drv, ["Yes, Thursday works"], ["Great, thank you. END"], bridge, route
    )
    rt.start_thread()  # runtime loop thread; wires dialer + scheduler thread
    try:
        job = scheduler.Store().create(
            c.name,
            c.number,
            "confirm Thursday's appointment",
            time.time() + 0.3,
            contact_id=c.id,
        )
        deadline = time.time() + 8
        while time.time() < deadline and not drv.calls:
            time.sleep(0.05)
        assert drv.calls, "scheduled call never dialled"
        assert ("dial", "+6591112222") in drv.calls
        while time.time() < deadline and not rt.said:
            time.sleep(0.05)
        assert "AI assistant" in rt.said[0][0]
        assert "confirm Thursday's appointment" in rt.said[0][0]
        # caller answers (feed from this thread through the loop)
        rt._loop.call_soon_threadsafe(bridge.say_to_us)
        while time.time() < deadline and rt.busy:
            time.sleep(0.05)
        assert not rt.busy
        assert scheduler.Store().get(job["id"])["status"] in ("started", "queued")
    finally:
        rt.stop_thread()
    assert voice_commands._dialer is None
    done = calllog.recent(1)[0]
    assert done["direction"] == "out" and done["outcome"] == "completed"
    assert route.down == 1


async def test_dial_blocked_when_busy_and_without_runtime():
    drv, bridge, route = FakeDriver(), FakeBridge(), FakeRoute()
    rt = make_runtime(drv, [], [], bridge, route)
    assert rt.dial({"number": "+6591"})["status"] == "failed"  # loop not started


# ---------------------------------------------------------- HUD delivery
def test_hud_feed_cursor_snapshot_and_commands(sink, tmp_path):
    events.set_sink(None)  # use the real JSONL sink for this test
    first = hud_feed.read_events(None)
    assert first == {"events": [], "next": 0}
    events.emit(
        events.state_event(5, "handling", "in", None, "", 1.0, "jarvis", True, False)
    )
    events.emit(events.transcript_event(5, "caller", "hello"))
    events.emit(events.transcript_event(5, "caller", "hel", partial=True))
    got = hud_feed.read_events(0)
    assert [e["event"] for e in got["events"]] == ["state", "transcript", "transcript"]
    assert all(e["type"] == "call" for e in got["events"])
    assert hud_feed.read_events(got["next"])["events"] == []
    snap = hud_feed.snapshot()
    assert snap["active"]["call_id"] == 5 and len(snap["transcript"]) == 1
    events.emit(events.summary_event(5, "done", "completed"))
    events.emit(
        events.state_event(5, "ended", "in", None, "", 1.0, "jarvis", True, False)
    )
    assert hud_feed.snapshot()["active"] is None

    assert hud_feed.queue_command("take over")["ok"]
    assert not hud_feed.queue_command("rm -rf")["ok"]
    line = (hud_feed.calls_dir() / "commands.jsonl").read_text().splitlines()
    assert len(line) == 1 and json.loads(line[0])["name"] == "takeover"


async def test_command_queue_ignores_stale_and_replays(sink):
    drv, bridge, route = FakeDriver(), FakeBridge(), FakeRoute()
    rt = make_runtime(drv, [], [], bridge, route)
    d = hud_feed.calls_dir()
    d.mkdir(parents=True, exist_ok=True)
    stale = {"id": "old", "name": "hangup", "ts": time.time() - 999}
    (d / "commands.jsonl").write_text(json.dumps(stale) + "\n")
    assert await rt.poll_commands() == 0
    hud_feed.queue_command("hold")
    assert await rt.poll_commands() == 1  # reaches handle_command (no active call)
    assert await rt.poll_commands() == 0  # not re-read
