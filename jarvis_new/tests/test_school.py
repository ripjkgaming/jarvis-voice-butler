"""School mode: shared state, commands, loud-action confirmation, follow-up."""

import asyncio
import types

import pytest
from livekit.agents.llm import ToolError

import school


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    school._cache.update(path=None, mtime=None, mode=school.NORMAL)
    school._confirmed_at.update(at=0.0, used=None)


def test_mode_roundtrip_and_default(tmp_path):
    assert school.current() == school.NORMAL
    school.set_mode("school")
    assert school.is_school()
    school.set_mode("normal")
    assert school.current() == school.NORMAL
    (tmp_path / "mode.json").write_text("{broken")
    assert school.current() == school.NORMAL


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("enter school mode", "school"), ("school mode", "school"),
        ("Can you turn on school mode please", "school"), ("school mode on", "school"),
        ("exit school mode", "normal"), ("turn off school mode", "normal"),
        ("school mode off", "normal"), ("back to normal mode", "normal"),
        ("what is school mode", None), ("my school mode of transport", None), ("", None),
    ],
)
def test_parse_command(text, want):
    assert school.parse_command(text) == want


@pytest.mark.parametrize(
    ("tool", "args", "loud"),
    [
        ("play_media", {"query": "x"}, True),
        ("set_volume", {"action": "up"}, True),
        ("set_volume", {"action": "set", "level": 70}, True),
        ("set_volume", {"action": "set", "level": 20}, False),
        ("set_volume", {"action": "down"}, False),
        ("open_app", {"app": "sober"}, True),
        ("open_app", {"app": "brave", "url": "https://www.youtube.com"}, True),
        ("open_app", {"app": "files"}, False),
        ("quote_action", {"action": "party"}, True),
        ("quote_action", {"action": "vitals"}, False),
        ("disk_space", {}, False),
    ],
)
def test_is_loud(tool, args, loud):
    assert school.is_loud(tool, args) is loud


def test_confirmation_covers_one_chain_then_expires():
    assert not school.loud_allowed(now=100.0)
    school.confirm_loud(now=100.0)
    assert school.loud_allowed(now=101.0)  # quote guard
    assert school.loud_allowed(now=102.0)  # play_media guard, same action
    assert not school.loud_allowed(now=101.0 + school.LOUD_CHAIN_S + 1)
    school.confirm_loud(now=200.0)
    assert not school.loud_allowed(now=200.0 + school.LOUD_CONFIRM_S + 1)


def test_loud_guard_only_in_school_mode():
    from system.school_tools import loud_guard

    loud_guard("play_media", {"query": "x"})  # normal: fine
    school.set_mode("school")
    with pytest.raises(ToolError):
        loud_guard("play_media", {"query": "x"})
    loud_guard("disk_space", {})
    school.confirm_loud()
    loud_guard("play_media", {"query": "x"})


def test_school_vad_is_stricter():
    import agent

    normal = agent.vad_kwargs()
    school.set_mode("school")
    strict = agent.vad_kwargs()
    assert strict["activation_threshold"] >= 0.7 > normal["activation_threshold"]
    assert strict["min_speech_duration"] >= 0.25
    assert strict["min_silence_duration"] == normal["min_silence_duration"]


class _Session:
    def __init__(self):
        self.handlers = {}
        self.closed = False

    def on(self, name, fn):
        self.handlers[name] = fn

    async def aclose(self):
        self.closed = True

    def emit(self, name, state):
        self.handlers[name](types.SimpleNamespace(new_state=state))


async def test_follow_up_ends_call_after_window(monkeypatch):
    import agent

    now = {"t": 0.0}
    monkeypatch.setattr(school, "FOLLOW_UP_S", 8.0)
    s = _Session()
    task = asyncio.create_task(agent._school_follow_up(s, clock=lambda: now["t"], tick=0.001))
    await asyncio.sleep(0.01)
    s.emit("user_state_changed", "speaking")
    s.emit("agent_state_changed", "thinking")
    now["t"] = 30.0  # thinking holds the call open
    await asyncio.sleep(0.01)
    assert not s.closed
    s.emit("agent_state_changed", "speaking")
    s.emit("agent_state_changed", "listening")  # reply done at t=30
    now["t"] = 35.0
    s.emit("user_state_changed", "speaking")  # follow-up inside 8 s
    s.emit("user_state_changed", "listening")  # deadline 43
    now["t"] = 42.0
    await asyncio.sleep(0.01)
    assert not s.closed
    now["t"] = 44.0
    await asyncio.wait_for(task, 1.0)
    assert s.closed


@pytest.mark.parametrize(
    ("text", "addressed", "question"),
    [
        ("Hey Jarvis, what is the capital of France?", True, "what is the capital of France?"),
        ("Jarvis, play music.", True, "play music."),
        ("hey jarvis what time is it", True, "what time is it"),
        ("Hey Jarvis.", True, ""),
        ("Okay class. Jarvis Cocker was a singer in the nineties.", False, None),
        ("what is the capital of France", False, None),
        ("", False, None),
    ],
)
def test_address_gate_and_question(text, addressed, question):
    assert school.addressed(text) is addressed
    if question is not None:
        assert school.question_after_address(text) == question
