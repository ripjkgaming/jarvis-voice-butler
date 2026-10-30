"""Gemini Live stall watchdog: pure helpers, then LiveBackend against the fake server.

The old wrapper tests drove a patched plugin session. They now drive the
LiveBackend with a manual clock, 10 ms mic frames and the fake Live server, so
every assertion is about the production connection path.
"""

import asyncio
import contextlib
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _fake_live import FakeClient
from google.genai import types

import gemini_watchdog as g
from jarvis_rt import Agent, AgentSession
from jarvis_rt.live import RECOVERY_NOTICE, LiveBackend

TALK = 2000
QUIET = 10
FRAME_S = 0.01
MODELS = ("gemini-3.8-live", "gemini-2.5-flash-native-audio-latest")


# ---- pure helpers ----------------------------------------------------------


class _Run:
    def __init__(self) -> None:
        self.clock = g.StallClock()
        self.t = 0.0
        self.fired: list[float] = []

    def frames(self, seconds: float, rms: float) -> None:
        for _ in range(round(seconds / FRAME_S)):
            self.t += FRAME_S
            if self.clock.mic(FRAME_S, rms, self.t):
                self.fired.append(self.t)

    def talk(self, seconds: float) -> None:
        self.frames(seconds, TALK)

    def quiet(self, seconds: float) -> None:
        self.frames(seconds, QUIET)


def test_stall_clock_answered_turns_never_restart():
    r = _Run()
    for _ in range(10):
        r.talk(3)
        r.quiet(1)
        r.clock.server(r.t)
        r.quiet(30)
    assert r.fired == []


def test_stall_clock_short_noise_bursts_never_restart():
    r = _Run()
    for _ in range(600):
        r.talk(0.2)
        r.quiet(0.8)
    assert r.fired == []


def test_stall_clock_single_finished_request_fires_after_silence():
    r = _Run()
    r.talk(3)
    r.quiet(g.SILENT_S + 2)
    assert len(r.fired) == 1
    assert r.fired[0] >= 3 + g.SILENT_S


def test_stall_clock_long_speech_is_not_cut_before_it_finishes():
    r = _Run()
    r.talk(40)
    assert r.fired == []
    r.quiet(2)
    assert r.fired == []


def test_stall_clock_cooldown_bounds_repeated_restarts():
    r = _Run()
    for _ in range(4):
        r.talk(3)
        r.quiet(5)
    assert len(r.fired) == 1
    assert r.fired[0] >= g.SILENT_S
    first = r.fired[0]
    for _ in range(4):
        r.talk(3)
        r.quiet(5)
    assert all(t - first >= g.COOLDOWN_S for t in r.fired[1:])


def test_frame_rms():
    assert g.frame_rms(np.zeros(160, dtype=np.int16).tobytes()) == 0.0
    loud = np.full(160, 1000, dtype=np.int16).tobytes()
    assert abs(g.frame_rms(loud) - 1000.0) < 1e-3
    assert g.frame_rms(b"") == 0.0


def test_enabled_flag(monkeypatch):
    monkeypatch.delenv("JARVIS_GEMINI_WATCHDOG", raising=False)
    assert g.enabled()
    for value in ("0", "false", "No", "OFF"):
        monkeypatch.setenv("JARVIS_GEMINI_WATCHDOG", value)
        assert not g.enabled()


def test_install_and_guard_are_gone():
    assert not hasattr(g, "install")
    assert not hasattr(g, "_guard")


# ---- LiveBackend against the fake Live server -----------------------------


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def frame(level: int) -> bytes:
    return np.full(160, level, dtype=np.int16).tobytes()


async def settle() -> None:
    await asyncio.sleep(0.02)


class Live:
    """A started session on the fake server plus helpers that advance the clock."""

    def __init__(self, backend, client, session, clock, recoveries):
        self.be = backend
        self.client = client
        self.s = session
        self.clock = clock
        self.recoveries = recoveries

    @property
    def conn(self):
        return self.client.conns[-1]

    async def mic(self, seconds: float, level: int) -> None:
        """Feed 10 ms frames; yield to the loop after each so sends interleave."""
        data = frame(level)
        for _ in range(round(seconds / FRAME_S)):
            self.clock.t += FRAME_S
            self.be.push_audio(data)
            await asyncio.sleep(0)

    async def talk(self, seconds: float) -> None:
        await self.mic(seconds, TALK)

    async def quiet(self, seconds: float) -> None:
        await self.mic(seconds, QUIET)

    async def quiet_until_recovery(self, limit_s: float = 60.0) -> None:
        """Quiet frames until the watchdog fires (or the limit passes)."""
        data = frame(QUIET)
        for _ in range(round(limit_s / FRAME_S)):
            if self.recoveries:
                return
            self.clock.t += FRAME_S
            self.be.push_audio(data)
            await asyncio.sleep(0)


@contextlib.asynccontextmanager
async def live(*, watchdog=True, timeout=10.0, models=MODELS):
    clock = Clock()
    client = FakeClient()
    backend = LiveBackend(
        list(models),
        client=client,
        watchdog=watchdog,
        clock=clock,
        generate_reply_timeout=timeout,
    )
    recoveries: list[float] = []
    original = backend._recover

    def recover():
        recoveries.append(clock.t)
        original()

    backend._recover = recover
    session = AgentSession(backend=backend)
    session.output.audio = lambda data: None
    await session.start(Agent(instructions="be brief"))
    await settle()
    try:
        yield Live(backend, client, session, clock, recoveries)
    finally:
        await backend.aclose()


def contents(conn) -> list[dict]:
    return [kw for kind, kw in conn.sent if kind == "content"]


def notices(conn) -> list[dict]:
    return [
        kw
        for kw in contents(conn)
        if kw.get("turns")
        and any(
            "I lost that voice request" in (part.text or "")
            for turn in kw["turns"]
            for part in turn.parts or []
        )
    ]


@pytest.mark.parametrize("answer", ["audio", "output_transcription", "tool_call"])
async def test_answered_turns_never_restart(answer):
    async with live() as rt:
        for _ in range(4):
            await rt.talk(3)
            await rt.quiet(1)
            if answer == "audio":
                rt.conn.audio(b"\x01\x00")
            elif answer == "output_transcription":
                rt.conn.content(
                    output_transcription=types.Transcription(text="It is noon.")
                )
            else:
                rt.conn.push(
                    tool_call=types.LiveServerToolCall(
                        function_calls=[
                            types.FunctionCall(id="f1", name="nope", args={})
                        ]
                    )
                )
            await settle()
            await rt.quiet(30)
        assert rt.recoveries == []


async def test_short_noise_bursts_never_restart():
    async with live() as rt:
        for _ in range(120):
            await rt.talk(0.2)
            await rt.quiet(0.8)
        assert rt.recoveries == []


async def test_repeated_unanswered_speech_restarts_once_per_cooldown():
    async with live() as rt:
        for _ in range(4):
            await rt.talk(3)
            await rt.quiet(5)
        assert len(rt.recoveries) == 1
        assert rt.recoveries[0] >= g.SILENT_S
        first = rt.recoveries[0]
        for _ in range(4):
            await rt.talk(3)
            await rt.quiet(5)
        assert all(t - first >= g.COOLDOWN_S for t in rt.recoveries[1:])


async def test_single_long_utterance_does_not_restart():
    async with live() as rt:
        await rt.talk(40)
        await rt.quiet(2)
        assert rt.recoveries == []


async def test_single_finished_request_recovers_once():
    async with live() as rt:
        await rt.talk(3)
        await rt.quiet(g.SILENT_S + 2)
        assert len(rt.recoveries) == 1
        assert rt.recoveries[0] >= 3 + g.SILENT_S


async def test_input_transcripts_usage_and_keepalive_are_not_answers():
    async with live() as rt:
        await rt.talk(3)
        for i in range(2200):
            if i % 100 == 0:
                rt.conn.push(usage_metadata=types.UsageMetadata(total_token_count=5))
                rt.conn.push()  # keepalive
                rt.conn.content(input_transcription=types.Transcription(text="test"))
                await settle()
            await rt.quiet(FRAME_S)
            if rt.recoveries:
                break
        assert len(rt.recoveries) == 1


async def test_stall_opens_fresh_connection_and_ignores_late_resumption_handle():
    async with live() as rt:
        old = rt.conn
        old.push(
            session_resumption_update=types.LiveServerSessionResumptionUpdate(
                new_handle="stale-client", resumable=True
            )
        )
        await settle()
        assert rt.be._handle == "stale-client"
        await rt.talk(3)
        data = frame(QUIET)
        for _ in range(round(60 / FRAME_S)):
            if rt.recoveries:
                break
            rt.clock.t += FRAME_S
            rt.be.push_audio(data)
            await asyncio.sleep(0)
        assert len(rt.recoveries) == 1
        # A final handle from the stalled connection arrives while it closes.
        old.push(
            session_resumption_update=types.LiveServerSessionResumptionUpdate(
                new_handle="late-old-client", resumable=True
            )
        )
        await settle()
        assert rt.be.recovery_pending()
        assert len(rt.client.connects) == 2
        config = rt.client.connects[1][1]
        assert config.session_resumption.handle is None
        assert rt.be._handle is None
        # The window closes after RECOVERY_S without an answer.
        rt.clock.t = rt.recoveries[0] + g.RECOVERY_S + 1
        assert not rt.be.recovery_pending()


async def test_fresh_recovery_repeats_once_and_never_replays_audio():
    async with live() as rt:
        await rt.talk(3)
        await rt.quiet_until_recovery()
        await settle()
        await settle()
        assert len(rt.recoveries) == 1
        all_notices = [n for conn in rt.client.conns for n in notices(conn)]
        assert len(all_notices) == 1
        second = rt.client.conns[1]
        assert notices(second) == all_notices
        assert all_notices[0]["turn_complete"] is True
        prompt = all_notices[0]["turns"][0].parts[0].text
        assert "repeat" in prompt.lower() and "do not execute" in prompt.lower()
        # Only the notice goes to the fresh connection: no earlier command or audio.
        assert contents(second) == all_notices
        talk = frame(TALK)
        replayed = [
            kw
            for kind, kw in second.sent
            if kind == "realtime" and kw["audio"].data == talk
        ]
        assert replayed == []
        assert prompt == RECOVERY_NOTICE
        # Recovered answer ends the window.
        second.content(output_transcription=types.Transcription(text="Recovered"))
        await settle()
        assert not rt.be.recovery_pending()


async def test_generation_timeout_recovers_once_per_cooldown():
    async with live(timeout=0.05) as rt:
        await asyncio.wait_for(rt.be.generate_reply("say hi", None), 1)
        assert rt.recoveries == [rt.clock.t]
        assert rt.be.recovery_pending()
        # Same clock, same cooldown: a second silent reply cannot loop reconnects.
        await settle()  # the fresh connection is up and silent again
        assert len(rt.client.connects) == 2
        await asyncio.wait_for(rt.be.generate_reply("say hi", None), 1)
        assert len(rt.recoveries) == 1
        assert len(rt.client.connects) == 2


async def test_generation_that_begins_in_time_does_not_recover():
    async with live(timeout=0.5) as rt:
        reply = asyncio.create_task(rt.be.generate_reply("say hi", None))
        await settle()
        rt.conn.audio(b"\x01\x00")
        await asyncio.wait_for(reply, 1)
        assert rt.recoveries == []


async def test_cancelled_or_other_generation_errors_do_not_force_recovery():
    async with live(timeout=0.05) as rt:
        reply = asyncio.create_task(rt.be.generate_reply("say hi", None))
        await settle()
        reply.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reply
        await asyncio.sleep(0.2)
        assert rt.recoveries == []

        reply = asyncio.create_task(rt.be.generate_reply("say hi", None))
        await settle()
        rt.conn.inbox.put_nowait(RuntimeError("provider unavailable"))
        with pytest.raises(RuntimeError, match="provider unavailable"):
            await asyncio.wait_for(reply, 1)
        await asyncio.sleep(0.2)
        assert rt.recoveries == []


async def test_disabled_watchdog_never_recovers(monkeypatch):
    monkeypatch.setenv("JARVIS_GEMINI_WATCHDOG", "0")
    clock = Clock()
    backend = LiveBackend(list(MODELS), client=FakeClient(), clock=clock)
    assert backend._stall is None
    await backend.aclose()

    async with live(watchdog=False, timeout=0.01) as rt:
        await rt.talk(3)
        await rt.quiet(g.SILENT_S + 5)
        await asyncio.wait_for(rt.be.generate_reply("say hi", None), 1)
        assert rt.recoveries == []
        assert not rt.be.recovery_pending()


def test_live_watchdog_default_follows_environment(monkeypatch):
    monkeypatch.setenv("JARVIS_GEMINI_WATCHDOG", "off")
    assert LiveBackend(list(MODELS), client=FakeClient())._stall is None
    monkeypatch.delenv("JARVIS_GEMINI_WATCHDOG")
    assert LiveBackend(list(MODELS), client=FakeClient())._stall is not None
