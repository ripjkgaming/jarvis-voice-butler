import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jarvis_rt import RunContext, StopResponse, ToolError, function_tool
from jarvis_rt.agent import Agent
from jarvis_rt.chat_context import FunctionCall
from jarvis_rt.executor import execute_call


class T:
    @function_tool()
    async def add(self, ctx: RunContext, a: int, b: int = 2) -> int:
        """Add.

        Args:
            a: first
            b: second
        """
        assert isinstance(ctx, RunContext)
        return a + b

    @function_tool()
    async def boom(self) -> str:
        raise RuntimeError("secret detail")

    @function_tool()
    async def bad(self) -> str:
        raise ToolError("visible")

    @function_tool()
    async def quiet(self) -> str:
        raise StopResponse()

    @function_tool()
    async def handoff(self):
        return Agent(instructions="x")

    @function_tool()
    async def obj(self):
        return object()


def _run(name, args):
    t = T()
    tools = {x.id: x for x in (t.add, t.boom, t.bad, t.quiet, t.handoff, t.obj)}
    call = FunctionCall(call_id="c1", name=name, arguments=args)
    return asyncio.run(execute_call(call, tools, RunContext()))


def test_ok_and_defaults():
    r = _run("add", json.dumps({"a": 3}))
    assert r.output.output == "5" and not r.output.is_error and r.output.reply_required


def test_coercion_and_repair():
    assert _run("add", '{"a": "4", "b": 1}').output.output == "5"
    assert _run("add", '{"a": 4,').output.output == "6"


def test_validation_error_is_model_visible():
    r = _run("add", "{}")
    assert r.output.is_error and r.output.output.startswith(
        "Error parsing arguments for `add`"
    )


def test_errors():
    assert _run("boom", "{}").output.output == "An internal error occurred"
    assert _run("bad", "{}").output.output == "visible"
    q = _run("quiet", "{}").output
    assert q.output == "" and not q.is_error and not q.reply_required
    assert _run("obj", "{}").output.output == "the tool returned an invalid output"
    assert "Unknown function: nope" in _run("nope", "{}").output.output


def test_handoff_returns_agent_without_reply():
    r = _run("handoff", "{}")
    assert isinstance(r.agent, Agent) and not r.output.reply_required
