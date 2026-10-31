import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_rt_session import FakeBackend

from jarvis_rt import Agent, AgentSession, EndCallTool
from jarvis_rt.chat_context import FunctionCall


def test_end_call_waits_for_farewell_then_closes():
    async def run():
        tool = EndCallTool(extra_description="x", end_instructions="bye")
        be = FakeBackend()
        s = AgentSession(backend=be)
        closed = []
        s.on("close", closed.append)
        await s.start(Agent(instructions="i", tools=tool.tools))
        s.backend_event(
            "tool_calls",
            calls=[FunctionCall(call_id="1", name="end_call", arguments="{}")],
        )
        await asyncio.sleep(0.1)
        assert not closed  # still waiting for the goodbye
        s.backend_event("agent_audio", pcm24k=b"\0\0")
        s.backend_event("agent_text", text="Goodbye, Sir.")
        s.backend_event("turn_complete")
        await asyncio.sleep(0.2)
        return be, closed

    be, closed = asyncio.run(run())
    assert ("results", ["bye"], True) in be.log
    assert len(closed) == 1
