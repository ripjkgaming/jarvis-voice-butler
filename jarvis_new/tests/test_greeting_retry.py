"""Greeting retries use LiveKit's actual completed-handle error semantics."""

import asyncio
from types import SimpleNamespace

import pytest
from livekit.agents import llm
from livekit.agents.voice import SpeechHandle

import agent


def _completed_speech(error=None):
    speech = SpeechHandle.create(allow_interruptions=True)
    speech._mark_done(error=error)
    return speech


@pytest.mark.asyncio
async def test_failed_speech_handle_does_not_publish_success_caption():
    failed = _completed_speech(llm.RealtimeError("generation timed out"))
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
            _completed_speech(llm.RealtimeError("generation timed out")),
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
        return _completed_speech(llm.RealtimeError("generation timed out"))

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
        return _completed_speech(llm.RealtimeError("generation timed out"))

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
