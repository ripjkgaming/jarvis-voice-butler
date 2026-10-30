import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jarvis_rt import Agent, AgentSession, StopResponse, function_tool
from jarvis_rt.chat_context import FunctionCall


class FakeBackend:
    def __init__(self):
        self.log = []
        self.session = None

    async def start(self, session, agent):
        self.session = session
        self.log.append(("start", agent.id))

    async def aclose(self):
        self.log.append(("close",))

    def push_audio(self, pcm):
        self.log.append(("audio", len(pcm)))

    def push_video(self, frame, ts_ms):
        pass

    def push_text(self, text):
        self.log.append(("text", text))

    async def say(self, text):
        self.log.append(("say", text))

    async def generate_reply(self, instructions, tool_choice):
        self.log.append(("reply", instructions, tool_choice))

    def interrupt(self):
        self.log.append(("interrupt",))

    async def send_tool_results(self, results, *, reply):
        self.log.append(("results", [r.output for r in results], reply))

    async def agent_changed(self, agent):
        self.log.append(("agent_changed", agent.id))

    async def sync_agent(self, agent):
        pass


class Other(Agent):
    def __init__(self):
        super().__init__(instructions="other")
        self.entered = False

    async def on_enter(self):
        self.entered = True


class Main(Agent):
    def __init__(self):
        super().__init__(instructions="main")

    @function_tool()
    async def echo(self, text: str) -> str:
        """Echo."""
        return text

    @function_tool()
    async def hush(self) -> str:
        """Silent."""
        raise StopResponse()

    @function_tool()
    async def go(self):
        """Hand off."""
        return Other()


def trace(session):
    events = []
    for name in (
        "user_state_changed",
        "agent_state_changed",
        "user_input_transcribed",
        "conversation_item_added",
        "function_tools_executed",
        "speech_created",
    ):
        session.on(name, lambda e, n=name: events.append((n, e)))
    return events


async def _boot():
    be = FakeBackend()
    s = AgentSession(backend=be)
    ev = trace(s)
    main = Main()
    await s.start(main)
    return be, s, ev, main


def test_qa_turn_and_history():
    async def run():
        _, s, ev, _ = await _boot()
        s.backend_event("user_speech_started")
        s.backend_event("user_transcript", text="hi", final=True)
        s.backend_event("user_speech_stopped")
        s.backend_event("agent_audio", pcm24k=b"\0" * 10)
        s.backend_event("agent_text", text="hello there")
        s.backend_event("turn_complete")
        return s, ev

    s, ev = asyncio.run(run())
    names = [n for n, _ in ev]
    assert names.index("user_state_changed") < names.index("user_input_transcribed")
    assert [e.new_state for n, e in ev if n == "agent_state_changed"][-2:] == [
        "speaking",
        "listening",
    ]
    roles = [(i.role, i.text_content) for i in s.history.items]
    assert roles == [("user", "hi"), ("assistant", "hello there")]


def test_tool_call_results_and_reply_flag():
    async def run():
        be, s, ev, _ = await _boot()
        s.backend_event(
            "tool_calls",
            calls=[FunctionCall(call_id="1", name="echo", arguments='{"text":"x"}')],
        )
        await asyncio.sleep(0.05)
        s.backend_event(
            "tool_calls", calls=[FunctionCall(call_id="2", name="hush", arguments="{}")]
        )
        await asyncio.sleep(0.05)
        return be, ev

    be, ev = asyncio.run(run())
    results = [x for x in be.log if x[0] == "results"]
    assert results[0] == ("results", ["x"], True)
    assert results[1] == ("results", [""], False)
    assert len([n for n, _ in ev if n == "function_tools_executed"]) == 2


def test_handoff_swaps_agent_and_enters():
    async def run():
        be, s, _, main = await _boot()
        s.backend_event(
            "tool_calls", calls=[FunctionCall(call_id="1", name="go", arguments="{}")]
        )
        await asyncio.sleep(0.05)
        return be, s, main

    be, s, main = asyncio.run(run())
    assert isinstance(s.current_agent, Other) and s.current_agent.entered
    assert ("agent_changed", "Other") in be.log
    assert main._session is None


def test_interrupt_rules():
    async def run():
        be, s, _, _ = await _boot()
        h = s.say("hold on", allow_interruptions=False)
        await s.interrupt()
        assert not h.interrupted
        await s.interrupt(force=True)
        return be, h

    be, h = asyncio.run(run())
    assert h.interrupted and ("interrupt",) in be.log


def test_user_barge_marks_speech_interrupted():
    async def run():
        _, s, _, _ = await _boot()
        h = s.generate_reply()
        s.backend_event("agent_audio", pcm24k=b"\0" * 4)
        s.backend_event("user_speech_started")
        s.backend_event("interrupted")
        return s, h

    s, h = asyncio.run(run())
    assert h.interrupted and s.agent_state == "listening"


def test_user_away_timeout():
    async def run():
        s = AgentSession(backend=FakeBackend(), user_away_timeout=0.05)
        await s.start(Main())
        await asyncio.sleep(0.12)
        return s.user_state

    assert asyncio.run(run()) == "away"
