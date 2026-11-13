"""A wedged model reply, a never-finishing speech or a hung tool must not
leave the voice session (or the HUD) stuck in "thinking" forever."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import hud_voice_state
from jarvis_rt import Agent, AgentSession, RunContext, function_tool
from jarvis_rt import executor as executor_mod
from jarvis_rt import live as live_mod
from jarvis_rt.chat_context import FunctionCall
from jarvis_rt.executor import execute_call
from jarvis_rt.live import LiveBackend


class SilentBackend:
    """A model that accepts everything and never answers."""

    def __init__(self):
        self.recovered = []
        self.results = []

    async def start(self, session, agent):
        pass

    async def aclose(self):
        pass

    def push_audio(self, pcm):
        pass

    def push_video(self, frame, ts_ms):
        pass

    def push_text(self, text):
        pass

    async def say(self, text):
        pass

    async def generate_reply(self, instructions, tool_choice):
        pass

    def interrupt(self):
        pass

    async def send_tool_results(self, results, *, reply):
        self.results.append(reply)

    async def agent_changed(self, agent):
        pass

    async def sync_agent(self, agent):
        pass

    def recover(self, reason):
        self.recovered.append(reason)


class Main(Agent):
    def __init__(self):
        super().__init__(instructions="main")

    @function_tool()
    async def echo(self, text: str) -> str:
        """Echo."""
        return text

    @function_tool()
    async def slow(self) -> str:
        """Slow but legitimate."""
        await asyncio.sleep(0.5)
        return "done"


async def _session(stuck_s=0.2):
    backend = SilentBackend()
    session = AgentSession(backend=backend)
    session.stuck_turn_s = stuck_s
    await session.start(Main())
    return session, backend


def test_unanswered_tool_reply_resets_turn_and_reconnects():
    async def main():
        session, backend = await _session()
        session.backend_event(
            "tool_calls",
            calls=[FunctionCall(call_id="c1", name="echo", arguments='{"text":"x"}')],
        )
        await asyncio.sleep(0.05)
        assert backend.results == [True]  # a reply is owed...
        assert session.agent_state == "thinking"  # ...and never comes
        await asyncio.sleep(0.5)
        assert session.agent_state == "listening"
        assert len(backend.recovered) == 1
        assert "thinking" in backend.recovered[0]
        await session.aclose()

    asyncio.run(main())


def test_speech_handle_that_never_finishes_is_closed():
    async def main():
        session, backend = await _session()
        handle = session.generate_reply(instructions="say hi")
        await asyncio.sleep(0.05)
        assert session.agent_state == "thinking" and not handle.done()
        await asyncio.sleep(0.5)
        assert handle.done()
        assert session.current_speech is None
        assert session.agent_state == "listening"
        assert backend.recovered
        await session.aclose()

    asyncio.run(main())


def test_running_tool_and_model_activity_are_not_stuck():
    async def main():
        session, backend = await _session(stuck_s=0.15)
        session.backend_event(
            "tool_calls",
            calls=[FunctionCall(call_id="c1", name="slow", arguments="{}")],
        )
        await asyncio.sleep(0.4)  # tool still running past stuck_s
        assert backend.recovered == []
        assert session.agent_state == "thinking"
        await asyncio.sleep(0.2)  # tool done; the reply now streams in
        for _ in range(4):
            session.backend_event("agent_audio", pcm24k=b"\0\0" * 240)
            session.backend_event("agent_text", text="still ")
            await asyncio.sleep(0.08)
        assert backend.recovered == []
        session.backend_event("turn_complete")
        assert session.agent_state == "listening"
        await asyncio.sleep(0.3)
        assert backend.recovered == []  # listening is never "stuck"
        await session.aclose()

    asyncio.run(main())


def test_watchdog_disabled_with_zero():
    async def main():
        session, backend = await _session(stuck_s=0)
        session.generate_reply(instructions="say hi")
        await asyncio.sleep(0.3)
        assert backend.recovered == [] and session.agent_state == "thinking"
        await session.aclose()

    asyncio.run(main())


class _HudSession:
    pass


class Stubborn:
    def __init__(self, session):
        self.session = session
        self.release = False

    @function_tool()
    async def hang(self, ctx: RunContext) -> str:
        """Ignores cancellation, like a wedged browser call."""
        with hud_voice_state.execution_scope(self.session):
            while not self.release:
                try:
                    await asyncio.sleep(0.05)
                except asyncio.CancelledError:
                    continue
        return "late"


def test_tool_ignoring_cancellation_is_abandoned_and_hud_released(monkeypatch):
    monkeypatch.setattr(executor_mod, "TOOL_TIMEOUT_S", 0.1)
    monkeypatch.setattr(executor_mod, "ABANDON_GRACE_S", 0.1)
    hud = _HudSession()

    async def main():
        with hud_voice_state.execution_scope(hud):  # unrelated, legit holder
            t = Stubborn(hud)
            call = FunctionCall(call_id="c1", name="hang", arguments="{}")
            r = await asyncio.wait_for(
                execute_call(call, {"hang": t.hang}, RunContext()), timeout=2
            )
            assert r.output.is_error and "took too long" in r.output.output
            # only the abandoned tool's scope was released
            assert hud._jarvis_executing == 1
        assert hud._jarvis_executing == 0
        t.release = True  # the abandoned tool finally unwinds...
        await asyncio.sleep(0.2)
        assert hud._jarvis_executing == 0  # ...without double-releasing

    asyncio.run(main())


def test_release_task_is_idempotent_with_scope_exit():
    hud = _HudSession()

    async def main():
        entered = asyncio.Event()
        leave = asyncio.Event()

        async def worker():
            with hud_voice_state.execution_scope(hud):
                entered.set()
                await leave.wait()

        task = asyncio.create_task(worker())
        await entered.wait()
        assert hud._jarvis_executing == 1
        assert hud_voice_state.release_task(task) == 1
        assert hud._jarvis_executing == 0
        leave.set()
        await task  # the scope's own exit must not decrement again
        assert hud._jarvis_executing == 0
        assert hud_voice_state.release_task(task) == 0

    asyncio.run(main())


def test_live_recover_forces_fresh_connection_and_clears_pending_reply():
    async def main():
        b = LiveBackend(["m"], client=object(), watchdog=True)
        b._pending_reply = True
        b._handle = "old-handle"
        b.recover("turn stuck in 'thinking' for 30s")
        assert b._restart.is_set()
        assert b._pending_reply is False
        assert b._fresh and b._notice_pending and b._handle is None

    asyncio.run(main())


def test_live_close_of_hung_socket_is_bounded(monkeypatch):
    monkeypatch.setattr(live_mod, "TEARDOWN_TIMEOUT_S", 0.1)

    class HungCM:
        async def __aexit__(self, *exc):
            await asyncio.Event().wait()

    async def main():
        b = LiveBackend(["m"], client=object(), watchdog=False)
        b._cm = HungCM()
        await asyncio.wait_for(b._close_conn(), timeout=1)
        assert b._cm is None and b._conn is None

    asyncio.run(main())


def test_connect_pacing_only_for_recovery_and_quota(monkeypatch):
    import time as _time

    from jarvis_rt import live

    monkeypatch.setattr(live, "CONNECT_MIN_GAP_S", 35.0)
    monkeypatch.setattr(live, "QUOTA_BACKOFF_S", 60.0)
    b = live.LiveBackend.__new__(live.LiveBackend)
    b._last_connect = _time.monotonic() - 5
    b._connect_not_before = 0.0
    b._pace_next = False
    assert b._connect_wait() == 0.0  # deliberate handoff reconnects at once
    b._pace_next = True
    assert 25 < b._connect_wait() <= 30  # recovery waits out the 35 s gap
    assert b._pace_next is False
    b._connect_not_before = _time.monotonic() + 40  # quota backoff always applies
    assert b._connect_wait() > 30
    b._last_connect = None
    b._connect_not_before = 0.0
    b._pace_next = True
    assert b._connect_wait() == 0.0  # first connect never waits
