"""Greeting retries use jarvis_rt's completed-handle error semantics."""

import asyncio
import sys
from array import array
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _fake_live import FakeClient

import agent
from jarvis_rt import SpeechHandle
from jarvis_rt.live import LiveBackend


def _completed_speech(error=None):
    speech = SpeechHandle.create(allow_interruptions=True)
    speech._mark_done(error=error)
    return speech


@pytest.mark.asyncio
async def test_failed_speech_handle_does_not_publish_success_caption():
    failed = _completed_speech(RuntimeError("generation timed out"))
    # Reproduce the SDK contract: awaiting a failed handle does not raise.
    assert await failed is failed
    captions = []
    session = SimpleNamespace(say=lambda line: failed)

    assert not await agent._say_with_caption(
        session, "Synthetic greeting.", lambda *args: captions.append(args)
    )
    assert captions == []


@pytest.mark.asyncio
async def test_greeting_retries_stored_error_and_captions_only_success(monkeypatch):
    handles = iter(
        [
            _completed_speech(RuntimeError("generation timed out")),
            _completed_speech(),
        ]
    )
    attempts, captions, delays = [], [], []

    def say(line):
        attempts.append(line)
        return next(handles)

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(agent.asyncio, "sleep", sleep)
    await agent._greet_with_retry(
        SimpleNamespace(say=say),
        lambda *args: captions.append(args),
        budget_aside=" Synthetic budget aside.",
    )
    assert attempts == [
        "Good day, Sir. What do you require? Synthetic budget aside.",
        "Good day, Sir. What do you require?",
    ]
    assert delays == [2.0]
    assert captions == [("jarvis", attempts[1])]


@pytest.mark.asyncio
async def test_successful_greeting_does_not_retry(monkeypatch):
    attempts, captions = [], []

    def say(line):
        attempts.append(line)
        return _completed_speech()

    async def no_sleep(delay):
        pytest.fail("a successful greeting must not retry")

    monkeypatch.setattr(agent.asyncio, "sleep", no_sleep)
    await agent._greet_with_retry(
        SimpleNamespace(say=say), lambda *args: captions.append(args)
    )
    assert len(attempts) == 1
    assert captions == [("jarvis", attempts[0])]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [None, asyncio.CancelledError()])
async def test_interrupted_greeting_neither_overwrites_user_caption_nor_retries(
    monkeypatch,
    error,
):
    interrupted = SpeechHandle.create(allow_interruptions=True)
    interrupted.interrupt()
    interrupted._mark_done(error=error)
    assert interrupted.interrupted
    assert interrupted.exception() is error
    attempts, captions = [], []

    def say(line):
        attempts.append(line)
        return interrupted

    async def no_sleep(delay):
        pytest.fail("an interrupted greeting must not retry over the user")

    monkeypatch.setattr(agent.asyncio, "sleep", no_sleep)
    await agent._greet_with_retry(
        SimpleNamespace(say=say), lambda *args: captions.append(args)
    )
    assert len(attempts) == 1
    assert captions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_state", "agent_state"),
    [("speaking", "listening"), ("listening", "thinking"), ("listening", "speaking")],
)
async def test_greeting_retry_yields_to_active_conversation(
    monkeypatch, user_state, agent_state
):
    attempts, captions = [], []

    def say(line):
        attempts.append(line)
        return _completed_speech(RuntimeError("generation timed out"))

    session = SimpleNamespace(say=say, user_state="listening", agent_state="listening")

    async def resume_conversation(delay):
        assert delay == 2.0
        session.user_state = user_state
        session.agent_state = agent_state

    monkeypatch.setattr(agent.asyncio, "sleep", resume_conversation)
    await agent._greet_with_retry(session, lambda *args: captions.append(args))
    assert len(attempts) == 1
    assert captions == []


@pytest.mark.asyncio
async def test_greeting_stops_after_one_retry_without_false_captions(monkeypatch):
    attempts, captions = [], []

    def say(line):
        attempts.append(line)
        return _completed_speech(RuntimeError("generation timed out"))

    async def no_delay(delay):
        pass

    monkeypatch.setattr(agent.asyncio, "sleep", no_delay)
    await agent._greet_with_retry(
        SimpleNamespace(say=say), lambda *args: captions.append(args)
    )
    assert len(attempts) == 2
    assert captions == []


@pytest.mark.asyncio
async def test_cancelled_greeting_is_not_retried(monkeypatch):
    attempts, captions = [], []

    def say(line):
        attempts.append(line)
        raise asyncio.CancelledError

    async def no_sleep(delay):
        pytest.fail("a cancelled greeting must not retry")

    monkeypatch.setattr(agent.asyncio, "sleep", no_sleep)
    with pytest.raises(asyncio.CancelledError):
        await agent._greet_with_retry(
            SimpleNamespace(say=say), lambda *args: captions.append(args)
        )
    assert len(attempts) == 1
    assert captions == []


@pytest.mark.asyncio
async def test_native_greeting_retry_waits_for_pcm_before_provider_speech_event(
    monkeypatch,
):

    attempts = []
    now = [0.0]
    meter = agent._InputActivity(clock=lambda: now[0])

    def say(line):
        attempts.append(line)
        return _completed_speech(RuntimeError("generation timed out"))

    session = SimpleNamespace(
        say=say,
        user_state="listening",
        agent_state="listening",
        _jarvis_input_activity=meter,
    )

    async def speaking_before_native_event(delay):
        now[0] = 2.0
        meter.process(array("h", [1200, -1200] * 400).tobytes())

    monkeypatch.setattr(agent.asyncio, "sleep", speaking_before_native_event)
    await agent._greet_with_retry(session, lambda *args: None)
    assert attempts == ["Good day, Sir. What do you require?"]
    assert session.user_state == "listening"


@pytest.mark.asyncio
async def test_wake_greeting_stays_quiet_when_sir_is_already_talking(monkeypatch):
    attempts = []
    session = SimpleNamespace(
        say=lambda line: attempts.append(line) or _completed_speech(),
        user_state="listening",
    )
    monkeypatch.setattr(agent, "_input_activity_holds", lambda *a, **k: True)
    await agent._greet_with_retry(session, lambda *a: None, listen_first_s=1.0)
    assert attempts == []


@pytest.mark.asyncio
async def test_wake_greeting_speaks_after_a_quiet_listen(monkeypatch):
    attempts = []
    session = SimpleNamespace(
        say=lambda line: attempts.append(line) or _completed_speech(),
        user_state="listening",
    )
    monkeypatch.setattr(agent, "_input_activity_holds", lambda *a, **k: False)
    await agent._greet_with_retry(session, lambda *a: None, listen_first_s=0.1)
    assert attempts == ["Good day, Sir. What do you require?"]


@pytest.mark.asyncio
async def test_wake_noise_defers_but_does_not_abandon_greeting(monkeypatch):
    now = [0.0]
    attempts = []
    session = SimpleNamespace(
        say=lambda line: attempts.append(line) or _completed_speech(),
        user_state="listening",
        agent_state="listening",
    )
    monkeypatch.setattr(agent.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(agent, "_input_activity_holds", lambda *a, **k: now[0] < 0.3)

    async def advance(delay):
        now[0] += delay

    monkeypatch.setattr(agent.asyncio, "sleep", advance)
    await agent._greet_with_retry(session, lambda *a: None, listen_first_s=1)
    assert attempts == ["Good day, Sir. What do you require?"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance,replying", [("Jarvis open Firefox", False), ("hey Jarvis", True)]
)
async def test_wake_deferral_does_not_greet_over_request_or_reply(
    monkeypatch, utterance, replying
):
    now = [0.0]
    attempts = []
    session = SimpleNamespace(
        say=lambda line: attempts.append(line) or _completed_speech(),
        user_state="listening",
        agent_state="listening",
    )
    monkeypatch.setattr(agent.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(agent, "_input_activity_holds", lambda *a, **k: now[0] < 0.3)

    async def advance(delay):
        now[0] += delay
        agent._note_wake_greeting_input(session, SimpleNamespace(transcript=utterance))
        if replying:
            session.agent_state = "speaking"

    monkeypatch.setattr(agent.asyncio, "sleep", advance)
    await agent._greet_with_retry(session, lambda *a: None, listen_first_s=1)
    assert attempts == []


def test_only_actual_bare_wake_ignores_request_tracking():
    session = SimpleNamespace()
    agent._note_wake_greeting_input(session, SimpleNamespace(transcript="Hey, Jarvis!"))
    assert not getattr(session, "_jarvis_wake_request_seen", False)
    agent._note_wake_greeting_input(
        session, SimpleNamespace(transcript="Jarvis open Firefox")
    )
    assert session._jarvis_wake_request_seen


@pytest.mark.asyncio
async def test_stalled_speech_is_interrupted_and_releases_startup(monkeypatch):
    speech = SpeechHandle.create(allow_interruptions=True)
    captions = []
    monkeypatch.setattr(agent, "ANNOUNCEMENT_TIMEOUT_S", 0.02)
    try:
        result = await asyncio.wait_for(
            agent._say_with_caption(
                SimpleNamespace(say=lambda line: speech),
                "Hello.",
                lambda *args: captions.append(args),
            ),
            timeout=0.5,
        )
        assert result is False
        assert speech.interrupted
        assert captions == []
    finally:
        speech._mark_done()


async def test_realtime_announcement_does_not_duplicate_conversation_caption():
    # The live backend already captions its own generated speech.
    backend = LiveBackend(["gemini-3.8-live"], client=FakeClient(), watchdog=False)
    session = SimpleNamespace(say=lambda line: _completed_speech(), backend=backend)
    captions = []
    assert await agent._say_with_caption(
        session, "Hello", lambda *args: captions.append(args)
    )
    assert captions == []
