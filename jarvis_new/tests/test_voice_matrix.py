"""Scripted matrix for speech while Jarvis is loading or mid-turn.

Drives the real VoiceTurnBinding, TurnManager and EchoGuard against a fake
session. No audio device, model or network.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from echo_guard import EchoGuard
from turn_manager import State, VoiceTurnBinding


class Session:
    def __init__(self):
        self.events = {}
        self.replies = []
        self.interrupts = 0
        self.said = []
        self.agent_state = "listening"

    def on(self, name, callback):
        self.events[name] = callback

    def interrupt(self, **kwargs):
        self.interrupts += 1
        future = asyncio.get_running_loop().create_future()
        future.set_result(None)
        return future

    async def generate_reply(self, **kwargs):
        self.replies.append(kwargs["user_input"])

    def say(self, text, **kwargs):
        self.said.append(text)
        return SimpleNamespace(interrupt=lambda **kw: None)


def say(binding, text):
    binding.transcript(SimpleNamespace(transcript=text, is_final=True))


def state(binding, name):
    binding.session.agent_state = name
    binding.agent_state(SimpleNamespace(new_state=name))


async def make(echo=None):
    session = Session()
    binding = VoiceTurnBinding(session, echo or EchoGuard())
    return session, binding


async def test_1_speech_during_greeting_cuts_greeting_and_is_answered_once():
    session, binding = await make()
    binding.ready()
    session._jarvis_greeting_active = True
    state(binding, "speaking")
    say(binding, "what time is it")
    assert session.interrupts == 1
    turn = binding.manager.current_turn
    assert turn is not None and turn.text == "what time is it"
    assert session.replies == []  # native model already heard it; no replay
    await binding.close()


async def test_2_speech_before_ready_is_buffered_and_answered_in_order():
    session, binding = await make()
    assert binding.manager.state is State.LOADING
    say(binding, "open the calendar")
    say(binding, "what is the weather")
    assert session.replies == []
    assert binding.manager.slow_boot_line(now=0.0) is None  # too early
    binding.ready()
    await asyncio.sleep(0.05)
    assert session.replies == ["open the calendar", "what is the weather"]
    await binding.close()


async def test_2b_slow_boot_line_once_and_only_after_user_spoke():
    session, binding = await make()
    manager = binding.manager
    assert manager.slow_boot_line(now=100.0) is None  # nobody spoke: silent
    say(binding, "hello there")
    first = (
        manager.slow_boot_line(now=manager._boot_spoke_at + 3.0)
        if hasattr(manager, "_boot_spoke_at")
        else None
    )
    if first is None:
        import time

        first = manager.slow_boot_line(now=time.monotonic() + 3.0)
    assert first == "One moment, sir, still coming online."
    assert manager.slow_boot_line(now=time.monotonic() + 10.0) is None
    await binding.close()


async def test_6_stop_while_thinking_cancels_everything():
    session, binding = await make()
    binding.ready()
    say(binding, "research quantum computing")
    state(binding, "thinking")
    turn = binding.manager.current_turn
    say(binding, "never mind")
    assert turn.cancelled
    assert session.interrupts >= 1
    assert session.replies == []
    await binding.close()


async def test_6b_stop_while_speaking_cancels_and_listens():
    session, binding = await make()
    binding.ready()
    say(binding, "tell me a story")
    state(binding, "speaking")
    turn = binding.manager.current_turn
    say(binding, "stop")
    assert turn.cancelled
    assert session.interrupts >= 1
    await binding.close()


async def test_7_two_quick_requests_only_newest_is_answered():
    session, binding = await make()
    binding.ready()
    say(binding, "what is the weather")
    state(binding, "thinking")
    say(binding, "what time is it")
    await asyncio.sleep(0.05)
    assert session.replies == ["what time is it"]
    await binding.close()


async def test_10_own_voice_does_not_self_trigger_barge_in():
    echo = EchoGuard()
    session, binding = await make(echo)
    binding.ready()
    reply = "The weather in Singapore is thirty one degrees and humid today"
    echo.note_agent(reply)
    state(binding, "speaking")
    say(binding, "weather in Singapore is thirty one degrees and humid")
    assert session.interrupts == 0
    assert binding.manager.current_turn is None
    # a real short user command is still honoured during playout
    say(binding, "stop")
    assert session.interrupts == 1
    await binding.close()
