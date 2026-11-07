"""Validated (realtime) replies stream: speech starts before the turn ends."""

import asyncio

import pytest

import claim_guard as cg


def _session(monkeypatch):
    from test_rt_session import FakeBackend

    from jarvis_rt import AgentSession

    monkeypatch.setattr(cg, "GUARD", cg.ClaimGuard())
    backend = FakeBackend()
    backend.requires_response_validation = True
    session = AgentSession(backend=backend)
    audio, captions, flushes = [], [], []
    session.output.audio = audio.append
    session.output.transcription = lambda text, final: captions.append((text, final))
    session.output.flush = lambda: flushes.append(True)
    return session, audio, captions, flushes


@pytest.mark.asyncio
async def test_clean_reply_streams_before_turn_complete(monkeypatch):
    session, audio, captions, _ = _session(monkeypatch)
    session._on_agent_audio(b"\0\0" * 100)
    assert not audio  # no transcript checked yet
    session._on_agent_text("The weather is sunny today, Sir.")
    assert audio == [b"\0\0" * 100]
    assert captions == [("The weather is sunny today, Sir.", False)]
    session._on_agent_audio(b"\1\1" * 100)
    assert audio[-1] == b"\1\1" * 100
    session._finish_turn(interrupted=False)
    assert audio == [b"\0\0" * 100, b"\1\1" * 100]
    assert captions[-1] == ("", True)
    await session.aclose()


@pytest.mark.asyncio
async def test_audio_bounded_by_checked_transcript(monkeypatch):
    session, audio, _, _ = _session(monkeypatch)
    one_s = b"\0\0" * 24_000
    for _ in range(10):
        session._on_agent_audio(one_s)
    session._on_agent_text("Hi.")  # covers well under one second
    released = sum(len(a) for a in audio)
    assert 0 < released < 3 * len(one_s)
    session._finish_turn(interrupted=False)
    assert sum(len(a) for a in audio) == 10 * len(one_s)
    await session.aclose()


@pytest.mark.asyncio
async def test_calendar_word_holds_rest_of_reply(monkeypatch):
    session, audio, captions, _ = _session(monkeypatch)
    session._on_agent_text("Right away. ")
    session._on_agent_audio(b"\0\0" * 10)
    assert audio == [b"\0\0" * 10]
    session._on_agent_text("I have added the exam to your calendar.")
    session._on_agent_audio(b"\1\1" * 10)
    assert audio == [b"\0\0" * 10]
    session._finish_turn(interrupted=False)
    await asyncio.sleep(0)
    assert audio == [b"\0\0" * 10]
    assert all("added" not in text for text, _ in captions)
    await session.aclose()


@pytest.mark.asyncio
async def test_state_stays_speaking_until_playout_ends(monkeypatch):
    session, _, _, flushes = _session(monkeypatch)
    session._on_agent_text("Long answer. " * 20)
    session._on_agent_audio(b"\0\0" * 24_000 * 3)  # 3 s of speech
    session._finish_turn(interrupted=False)
    assert session.agent_state == "speaking"
    session.interrupt()  # barge-in after generation ended
    assert flushes == [True]
    assert session.agent_state == "listening"
    await session.aclose()
