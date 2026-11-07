"""Native Gemini turn handling on LiveBackend, against the fake Live server.

Server voice activity detection owns end of turn. The client must never send
an empty turn_complete for an audio turn, and it nudges only when a tool
result is left without any generation.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _fake_live import FakeClient
from google.genai import types

from jarvis_rt import Agent, AgentSession
from jarvis_rt.chat_context import FunctionCallOutput
from jarvis_rt.live import LiveBackend, needs_reply_placeholder

NATIVE_3_8 = "gemini-3.8-live"
PLACEHOLDER_2_5 = "gemini-2.5-flash-native-audio-latest"


async def boot(models=(NATIVE_3_8,)):
    client = FakeClient()
    backend = LiveBackend(list(models), client=client, watchdog=False)
    session = AgentSession(backend=backend)
    session.output.audio = lambda data: None
    await session.start(Agent(instructions="be brief"))
    await asyncio.sleep(0.02)
    return client, backend, session


async def settle(seconds: float = 0.05) -> None:
    await asyncio.sleep(seconds)


def client_turns(conn) -> list:
    return [kw for kind, kw in conn.sent if kind == "content"]


def tool_result(call_id="f1", output="42"):
    return [
        FunctionCallOutput(call_id=call_id, name="echo", output=output, is_error=False)
    ]


def tool_responses(conn) -> list:
    return [kw for kind, kw in conn.sent if kind == "tool_response"]


async def test_audio_turn_sends_no_empty_client_turn():
    client, backend, _ = await boot()
    try:
        conn = client.conns[0]
        reply = asyncio.create_task(backend.generate_reply(None, None))
        await settle()
        assert client_turns(conn) == []
        reply.cancel()
        await asyncio.gather(reply, return_exceptions=True)
    finally:
        await backend.aclose()


async def test_audio_turn_on_placeholder_model_sends_dot_not_empty_turn():
    assert needs_reply_placeholder(PLACEHOLDER_2_5)
    client, backend, _ = await boot(models=(PLACEHOLDER_2_5,))
    try:
        conn = client.conns[0]
        reply = asyncio.create_task(backend.generate_reply(None, None))
        await settle()
        turns = client_turns(conn)
        assert len(turns) == 1
        assert turns[0]["turns"][0].parts[0].text == "."
        assert turns[0]["turn_complete"] is True
        reply.cancel()
        await asyncio.gather(reply, return_exceptions=True)
    finally:
        await backend.aclose()


async def test_instructed_reply_is_sent_as_a_turn():
    client, backend, _ = await boot()
    try:
        conn = client.conns[0]
        reply = asyncio.create_task(backend.generate_reply("Say hi.", None))
        await settle()
        turns = client_turns(conn)
        assert len(turns) == 1
        # Instructions are sent wrapped as private direction (commit 0e3c666).
        sent = turns[0]["turns"][0].parts[0].text
        assert "Say hi." in sent and "aloud" in sent.lower()
        assert turns[0]["turn_complete"] is True
        reply.cancel()
        await asyncio.gather(reply, return_exceptions=True)
    finally:
        await backend.aclose()


def test_nudge_delay_is_one_and_a_half_seconds():
    backend = LiveBackend([NATIVE_3_8], client=FakeClient(), watchdog=False)
    assert backend._nudge_s == 1.5


async def test_tool_response_nudges_when_no_generation_begins():
    client, backend, _ = await boot()
    try:
        backend._nudge_s = 0.05
        conn = client.conns[0]
        await backend.send_tool_results(tool_result(), reply=True)
        await settle(0.2)
        nudges = [kw for kw in client_turns(conn) if kw["turns"] is None]
        assert nudges == [{"turns": None, "turn_complete": True}]
    finally:
        await backend.aclose()


async def test_tool_response_is_not_nudged_once_generation_begins():
    client, backend, _ = await boot()
    try:
        backend._nudge_s = 0.1
        conn = client.conns[0]
        await backend.send_tool_results(tool_result(), reply=True)
        conn.audio(b"\x01\x00")  # the model speaks the tool result
        await settle(0.25)
        assert [kw for kw in client_turns(conn) if kw["turns"] is None] == []
    finally:
        await backend.aclose()


async def test_nudge_can_be_disabled_by_environment(monkeypatch):
    monkeypatch.setenv("JARVIS_GEMINI_TURN_PATCH", "0")
    client, backend, _ = await boot()
    try:
        backend._nudge_s = 0.05
        conn = client.conns[0]
        await backend.send_tool_results(tool_result(), reply=True)
        await settle(0.2)
        assert client_turns(conn) == []
        assert len(tool_responses(conn)) == 1
    finally:
        await backend.aclose()


async def test_tool_response_without_reply_is_never_nudged():
    client, backend, _ = await boot()
    try:
        backend._nudge_s = 0.05
        conn = client.conns[0]
        await backend.send_tool_results(tool_result(), reply=False)
        await settle(0.2)
        assert client_turns(conn) == []
        scheduling = tool_responses(conn)[0]["function_responses"][0].scheduling
        assert scheduling == types.FunctionResponseScheduling.SILENT
    finally:
        await backend.aclose()
