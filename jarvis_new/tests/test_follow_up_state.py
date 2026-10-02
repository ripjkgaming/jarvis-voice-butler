"""Follow-up timing follows both live states without committing noisy audio."""

import asyncio
import contextlib
from types import SimpleNamespace

import pytest

import agent
import hud_events
import latency
import school
import system


class Session:
    def __init__(self, *, user="listening", assistant="listening"):
        self.user_state = user
        self.agent_state = assistant
        self.handlers = {}
        self.closed = False
        self.generated = []

    def on(self, name, callback):
        self.handlers.setdefault(name, []).append(callback)

    def off(self, name, callback):
        self.handlers[name].remove(callback)

    def emit(self, name, state):
        setattr(self, name.removesuffix("_changed"), state)
        for callback in tuple(self.handlers.get(name, [])):
            callback(SimpleNamespace(new_state=state))

    def commit_user_turn(self, **kwargs):
        self.generated.append("commit")

    def generate_reply(self, **kwargs):
        self.generated.append("reply")

    async def aclose(self):
        self.closed = True


@pytest.fixture
def observations(monkeypatch):
    captions, logs = [], []
    monkeypatch.setattr(hud_events, "caption", lambda *args: captions.append(args))
    monkeypatch.setattr(system, "log_action", lambda *args: logs.append(args))
    monkeypatch.setattr(latency, "TRACKER", SimpleNamespace(pending=0))
    monkeypatch.setattr(school, "awaiting_answer", lambda: False)
    return captions, logs


async def _settle():
    await asyncio.sleep(0.006)


async def _stop(task):
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_preexisting_speech_holds_initial_deadline(observations):
    now = [0.0]
    session = Session(user="speaking")
    task = asyncio.create_task(
        agent._school_follow_up(
            session, clock=lambda: now[0], tick=0.001, first=5, window=5
        )
    )
    try:
        await _settle()
        now[0] = 6
        await _settle()
        assert not session.closed
        session.emit("user_state_changed", "listening")
        now[0] = 10.9
        await _settle()
        assert not session.closed
        now[0] = 11
        await asyncio.wait_for(task, 1)
        assert session.closed
        assert all(not callbacks for callbacks in session.handlers.values())
    finally:
        await _stop(task)


@pytest.mark.asyncio
async def test_agent_listening_does_not_arm_deadline_during_user_speech(observations):
    now = [0.0]
    session = Session()
    task = asyncio.create_task(
        agent._school_follow_up(
            session, clock=lambda: now[0], tick=0.001, first=5, window=5
        )
    )
    try:
        await _settle()
        session.emit("user_state_changed", "speaking")
        session.emit("agent_state_changed", "thinking")
        now[0] = 2
        session.emit("agent_state_changed", "listening")
        now[0] = 8
        await _settle()
        assert not session.closed
    finally:
        await _stop(task)


@pytest.mark.asyncio
async def test_preexisting_agent_work_holds_window(observations):
    now = [0.0]
    session = Session(assistant="thinking")
    task = asyncio.create_task(
        agent._school_follow_up(
            session, clock=lambda: now[0], tick=0.001, first=5, window=5
        )
    )
    try:
        await _settle()
        now[0] = 70
        await _settle()
        assert not session.closed
        session.emit("agent_state_changed", "listening")
        now[0] = 74.9
        await _settle()
        assert not session.closed
        now[0] = 75
        await asyncio.wait_for(task, 1)
        assert session.closed
    finally:
        await _stop(task)


@pytest.mark.asyncio
async def test_natural_end_resets_continuous_speech_budget(observations):
    now = [0.0]
    session = Session()
    task = asyncio.create_task(
        agent._school_follow_up(session, clock=lambda: now[0], tick=0.001)
    )
    try:
        await _settle()
        session.emit("user_state_changed", "speaking")
        now[0] = 10
        session.emit("user_state_changed", "listening")
        now[0] = 11
        session.emit("user_state_changed", "speaking")
        now[0] = agent.FOLLOW_UP_HOLD_CAP_S + 1
        await _settle()
        assert not session.closed
        now[0] = 11 + agent.FOLLOW_UP_HOLD_CAP_S
        await asyncio.wait_for(task, 1)
        assert session.closed
    finally:
        await _stop(task)


@pytest.mark.asyncio
async def test_continuous_audio_warns_then_closes_at_sixty_seconds(observations):
    captions, logs = observations
    now = [0.0]
    session = Session(user="speaking")
    task = asyncio.create_task(
        agent._school_follow_up(session, clock=lambda: now[0], tick=0.001, first=100)
    )
    try:
        await _settle()
        now[0] = 19.9
        await _settle()
        assert not captions
        now[0] = 20
        await _settle()
        assert len(captions) == 1
        assert "pause" in captions[0][1].lower()
        for at in (30, 59.9):
            now[0] = at
            await _settle()
            assert not session.closed
            assert len(captions) == 1
        now[0] = 60
        await asyncio.wait_for(task, 1)
        assert session.closed
        assert len(captions) == 2
        assert "standby" in captions[-1][1].lower()
        assert any("continuous-audio" in detail for _, detail in logs)
        assert session.generated == []
        assert all(not callbacks for callbacks in session.handlers.values())
    finally:
        await _stop(task)


@pytest.mark.asyncio
async def test_pending_tool_holds_continuous_audio_cap(observations):
    now = [0.0]
    session = Session(user="speaking")
    latency.TRACKER.pending = 1
    task = asyncio.create_task(
        agent._school_follow_up(session, clock=lambda: now[0], tick=0.001)
    )
    try:
        await _settle()
        now[0] = 61
        await _settle()
        assert not session.closed
        latency.TRACKER.pending = 0
        await asyncio.wait_for(task, 1)
        assert session.closed
    finally:
        await _stop(task)


@pytest.mark.asyncio
async def test_cancellation_removes_only_watchdog_listeners(observations):
    session = Session()

    def unrelated(event):
        pass

    session.on("user_state_changed", unrelated)
    task = asyncio.create_task(agent._school_follow_up(session, tick=0.001))
    await _settle()
    await _stop(task)
    assert not session.closed
    assert session.handlers["user_state_changed"] == [unrelated]
    assert session.handlers["agent_state_changed"] == []
