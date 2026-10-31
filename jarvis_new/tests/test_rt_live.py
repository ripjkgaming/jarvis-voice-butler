import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _fake_live import FakeClient
from google.genai import types

from jarvis_rt import Agent, AgentSession, function_tool
from jarvis_rt.live import (
    LiveBackend,
    ModelChain,
    http_options,
    needs_reply_placeholder,
    thinking_config,
)


class Main(Agent):
    def __init__(self):
        super().__init__(instructions="be brief")

    @function_tool()
    async def echo(self, text: str) -> str:
        """Echo."""
        return text


async def boot(
    models=("gemini-3.8-live", "gemini-2.5-flash-native-audio-latest"), client=None
):
    client = client or FakeClient()
    backend = LiveBackend(list(models), client=client, watchdog=False)
    s = AgentSession(backend=backend)
    events = []
    for n in (
        "user_state_changed",
        "agent_state_changed",
        "user_input_transcribed",
        "conversation_item_added",
        "function_tools_executed",
        "error",
    ):
        s.on(n, lambda e, n=n: events.append((n, e)))
    audio = []
    s.output.audio = audio.append
    await s.start(Main())
    await asyncio.sleep(0.02)
    return client, backend, s, events, audio


async def settle():
    await asyncio.sleep(0.05)


def test_setup_config_and_mic_order():
    async def run():
        client, be, s, *_ = await boot()
        model, cfg = client.connects[0]
        for i in range(5):
            s.push_audio(bytes([i]) * 1024)
        await settle()
        await be.aclose()
        return client, model, cfg

    client, model, cfg = asyncio.run(run())
    assert model == "gemini-3.8-live"
    aad = cfg.realtime_input_config.automatic_activity_detection
    assert aad.disabled is False and aad.silence_duration_ms == 500
    assert aad.end_of_speech_sensitivity.name == "END_SENSITIVITY_HIGH"
    assert (
        cfg.generation_config.thinking_config is None
    )  # 3.8 accepts no thinking knobs
    assert (
        cfg.speech_config.voice_config.prebuilt_voice_config.voice_name == "Enceladus"
    )
    assert cfg.tools and cfg.tools[0].function_declarations[0].name == "echo"
    mic = [kw["audio"].data[0] for k, kw in client.conns[0].sent if k == "realtime"]
    assert mic == [0, 1, 2, 3, 4]


def test_user_turn_events_and_reply_audio():
    async def run():
        client, be, s, ev, audio = await boot()
        c = client.conns[0]
        c.content(input_transcription=types_tx("what time"))
        c.content(input_transcription=types_tx(" is it"))
        c.audio(b"\x01\x02")
        c.content(output_transcription=types_tx("It is noon."))
        c.content(generation_complete=True)
        c.content(turn_complete=True)
        await settle()
        await be.aclose()
        return s, ev, audio

    s, ev, audio = asyncio.run(run())
    assert audio == [b"\x01\x02"]
    finals = [
        e.transcript for n, e in ev if n == "user_input_transcribed" and e.is_final
    ]
    assert finals == ["what time is it"]
    assert [(i.role, i.text_content) for i in s.history.items] == [
        ("user", "what time is it"),
        ("assistant", "It is noon."),
    ]
    states = [e.new_state for n, e in ev if n == "user_state_changed"]
    assert states == ["speaking", "listening"]


def types_tx(text):
    from google.genai import types

    return types.Transcription(text=text)


def test_server_interrupt_while_agent_speaks():
    async def run():
        client, be, s, ev, audio = await boot()
        c = client.conns[0]
        h = s.generate_reply()
        await settle()
        c.audio(b"a")
        c.audio(b"b")
        await settle()
        c.content(interrupted=True)
        await settle()
        c.audio(b"late")  # belongs to no reply the user wants
        await settle()
        await be.aclose()
        return s, h, audio, ev

    s, h, audio, ev = asyncio.run(run())
    assert audio == [b"a", b"b"]  # nothing of the cut reply plays after the interrupt
    assert h.interrupted and s.agent_state == "listening"
    assert "user_state_changed" in [n for n, _ in ev]


def test_tool_roundtrip_with_when_idle_scheduling():
    from google.genai import types

    async def run():
        client, be, _s, ev, _ = await boot()
        c = client.conns[0]
        c.push(
            tool_call=types.LiveServerToolCall(
                function_calls=[
                    types.FunctionCall(id="f1", name="echo", args={"text": "hi"})
                ]
            )
        )
        await asyncio.sleep(0.1)
        await be.aclose()
        return c, ev

    c, ev = asyncio.run(run())
    sent = [kw for k, kw in c.sent if k == "tool_response"]
    fr = sent[0]["function_responses"][0]
    assert fr.id == "f1" and fr.response == {"output": "hi"}
    assert fr.scheduling.name == "WHEN_IDLE"
    assert [n for n, _ in ev].count("function_tools_executed") == 1


def test_tool_nudge_when_no_generation_follows():
    from google.genai import types

    async def run():
        client, be, _s, _ev, _ = await boot()
        be._nudge_s = 0.05
        c = client.conns[0]
        c.push(
            tool_call=types.LiveServerToolCall(
                function_calls=[
                    types.FunctionCall(id="f1", name="echo", args={"text": "hi"})
                ]
            )
        )
        await asyncio.sleep(0.25)
        await be.aclose()
        return c

    c = asyncio.run(run())
    assert ("content", {"turns": None, "turn_complete": True}) in c.sent


def test_failover_cooldown_and_error_event():
    async def run():
        client = FakeClient()
        client.fail_models["gemini-3.8-live"] = RuntimeError("1011 quota")
        _client, be, _s, ev, _ = await boot(client=client)
        await asyncio.sleep(0.5)
        await be.aclose()
        return client, ev

    client, ev = asyncio.run(run())
    models = [m for m, _ in client.connects]
    assert (
        models[0] == "gemini-3.8-live"
        and "gemini-2.5-flash-native-audio-latest" in models
    )
    assert any(n == "error" for n, _ in ev)
    # failed model is cooling down: not retried within 60 s
    assert models.count("gemini-3.8-live") == 1


def test_model_chain_cooldown_clock():
    t = [0.0]
    ch = ModelChain(["a", "b"], cooldown=60, clock=lambda: t[0])
    assert ch.pick() == "a"
    ch.fail("a")
    assert ch.pick() == "b"
    t[0] = 61
    assert ch.pick() == "a"


def test_agent_handoff_reconnects_with_new_instructions_and_history():
    async def run():
        client, be, s, _ev, _ = await boot()
        other = Agent(instructions="specialist")
        s.update_agent(other)
        await asyncio.sleep(0.3)
        await be.aclose()
        return client

    client = asyncio.run(run())
    assert len(client.connects) == 2
    assert client.connects[1][1].system_instruction.parts[0].text == "specialist"


def test_quirk_helpers():
    assert thinking_config("gemini-3.8-live") is None
    assert (
        thinking_config("gemini-3.1-flash-live-preview").thinking_level.name
        == "MINIMAL"
    )
    assert thinking_config("gemini-2.5-flash-native-audio-latest").thinking_budget == 32
    assert not needs_reply_placeholder("gemini-3.8-live") and needs_reply_placeholder(
        "gemini-2.5-flash"
    )


class Specialist(Agent):
    def __init__(self):
        super().__init__(instructions="specialist")

    @function_tool()
    async def ping(self) -> str:
        """Ping."""
        return "pong"


def test_tool_set_change_reconnects_with_new_declarations_and_keeps_history():
    async def run():
        client, be, s, _ev, _ = await boot()
        c = client.conns[0]
        c.push(
            session_resumption_update=types.LiveServerSessionResumptionUpdate(
                new_handle="h1", resumable=True
            )
        )
        await settle()
        s.update_agent(Specialist())
        await asyncio.sleep(0.3)
        await be.aclose()
        return client

    client = asyncio.run(run())
    assert len(client.connects) == 2
    cfg = client.connects[1][1]
    assert [f.name for f in cfg.tools[0].function_declarations] == ["ping"]
    assert cfg.system_instruction.parts[0].text == "specialist"
    # The resumption handle carries the conversation across the tool-set change.
    assert cfg.session_resumption.handle == "h1"


def test_tls_http_options_keep_certificate_environment(monkeypatch):
    calls = []
    sentinel = object()
    monkeypatch.setenv("SSL_CERT_FILE", "/custom/ca.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "/custom/certs")
    monkeypatch.setattr(
        "ssl.create_default_context", lambda **kw: calls.append(kw) or sentinel
    )
    options = http_options()
    assert options.async_client_args["ssl"] is sentinel
    assert options.timeout == 10_000
    assert calls == [{"cafile": "/custom/ca.pem", "capath": "/custom/certs"}]


def test_tool_choice_none_applies_to_one_reply_only():
    """A tool-less greeting must not disable tools for the rest of the call."""
    from google.genai import types

    async def run():
        client, be, _s, ev, _ = await boot()
        c = client.conns[0]
        await be.generate_reply("greet", "none")
        c.content(turn_complete=True)
        await asyncio.sleep(0.1)
        c.push(
            tool_call=types.LiveServerToolCall(
                function_calls=[
                    types.FunctionCall(id="f9", name="echo", args={"text": "yo"})
                ]
            )
        )
        await asyncio.sleep(0.1)
        await be.aclose()
        return ev

    ev = asyncio.run(run())
    assert [n for n, _ in ev].count("function_tools_executed") == 1
