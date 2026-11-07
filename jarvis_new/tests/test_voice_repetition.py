"""Duplicate callbacks and background-announcement races must not repeat actions."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from jarvis_rt import AgentSession


def test_partial_final_replays_emit_once_but_new_utterance_can_repeat():
    session = AgentSession()
    finals = []
    partials = []
    session.on(
        "user_input_transcribed",
        lambda e: (finals if e.is_final else partials).append(e.transcript),
    )
    session.backend_event("user_speech_started", utterance_id="request1")
    for _ in range(3):
        session.backend_event(
            "user_transcript",
            text="  Add   training  ",
            final=False,
            utterance_id="request1",
        )
    for _ in range(3):
        session.backend_event(
            "user_transcript",
            text=" Add training ",
            final=True,
            utterance_id="request1",
        )
    assert partials == ["Add training"]
    assert finals == ["Add training"]
    assert len(session.history.items) == 1
    session.backend_event("user_speech_started", utterance_id="request2")
    session.backend_event(
        "user_transcript", text="Add training", final=True, utterance_id="request2"
    )
    assert finals == ["Add training", "Add training"]


def test_cancelled_and_old_utterances_cannot_retrigger_actions():
    session = AgentSession()
    mutations = []
    session.on(
        "user_input_transcribed",
        lambda e: mutations.append(e.transcript) if e.is_final else None,
    )
    session.backend_event("user_speech_started", utterance_id="cancelled")
    session.backend_event(
        "user_transcript", text="add an event", final=False, utterance_id="cancelled"
    )
    session.clear_user_turn()
    session.backend_event("user_speech_started", utterance_id="new")
    session.backend_event(
        "user_transcript", text="repeat that please", final=True, utterance_id="new"
    )
    session.backend_event(
        "user_transcript", text="add an event", final=True, utterance_id="cancelled"
    )
    session.backend_event(
        "user_transcript", text="repeat that please", final=True, utterance_id="new"
    )
    assert mutations == ["repeat that please"]


def test_announcement_claim_is_atomic_and_normalized(monkeypatch, tmp_path):
    import announce_once

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(
            pool.map(
                lambda _: announce_once.unconsumed("  Training   reminder "), range(16)
            )
        )
    assert sum(claims) == 1
    assert not announce_once.unconsumed("Training reminder")


@pytest.mark.asyncio
async def test_duplicate_final_listener_starts_one_reply_and_allows_requested_repeat():
    class Backend:
        async def generate_reply(self, instructions, tool_choice):
            replies.append(instructions)

    replies = []
    session = AgentSession(backend=Backend())

    def answer(event):
        if event.is_final:
            session.generate_reply(instructions="Answer: " + event.transcript)

    session.on("user_input_transcribed", answer)
    session.backend_event("user_speech_started", utterance_id="a")
    for _ in range(2):
        session.backend_event(
            "user_transcript",
            text="what time is training",
            final=True,
            utterance_id="a",
        )
    await asyncio.sleep(0)
    assert len(replies) == 1
    session.backend_event("user_speech_started", utterance_id="b")
    session.backend_event(
        "user_transcript", text="repeat that please", final=True, utterance_id="b"
    )
    await asyncio.sleep(0)
    assert len(replies) == 2


@pytest.mark.asyncio
async def test_overlapping_replayed_tool_call_runs_mutation_once():
    from jarvis_rt import Agent, function_tool
    from jarvis_rt.chat_context import FunctionCall

    mutations = []

    class Main(Agent):
        def __init__(self):
            super().__init__(instructions="test")

        @function_tool()
        async def add_event(self, title: str):
            """Add the requested event."""
            mutations.append(title)
            await asyncio.sleep(0.01)
            return {"ok": True}

    session = AgentSession()
    await session.start(Main())
    first = FunctionCall(
        call_id="same-provider-id", name="add_event", arguments='{"title":"Training"}'
    )
    session.backend_event("tool_calls", calls=[first, first])
    session.backend_event("tool_calls", calls=[first])
    await asyncio.sleep(0.05)
    assert mutations == ["Training"]
    session.backend_event(
        "tool_calls",
        calls=[
            FunctionCall(
                call_id="fresh-user-request",
                name="add_event",
                arguments='{"title":"Training"}',
            )
        ],
    )
    await asyncio.sleep(0.05)
    assert mutations == ["Training", "Training"]
    await session.aclose()


def test_overlapping_notification_producers_deliver_once(monkeypatch, tmp_path):
    import time

    import notify

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(notify, "_input_state", lambda: "active")
    monkeypatch.setattr(notify, "_in_quiet", lambda _: False)
    delivered = []

    def speaker(text, **kwargs):
        delivered.append(text)
        time.sleep(0.01)
        return True

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: notify.send(
                    "Training is due", fingerprint="training:one", speaker=speaker
                ),
                range(12),
            )
        )
    assert delivered == ["Training is due"]
    assert sum(r["route"] == "deduped" for r in results) == 11


def test_explicit_repeat_request_is_not_mistaken_for_self_echo():
    from echo_guard import EchoGuard

    guard = EchoGuard()
    guard.note_agent("Computer Science Event January sixteen", now=100)
    assert not guard.is_echo("repeat Computer Science Event January sixteen", now=101)


@pytest.mark.asyncio
async def test_echo_backstop_receives_words_during_playout(monkeypatch):
    import agent
    from echo_guard import EchoGuard

    monkeypatch.setattr(agent, "_write_live_caption", lambda *args: None)
    guard = EchoGuard()
    caption = agent._HudLiveCaption()
    caption.on_text = guard.note_agent
    await caption.capture_text("Training starts at one o'clock today")
    assert guard.is_echo("Training starts at one o'clock today")
    assert not guard.is_echo("repeat Training starts at one o'clock today")
