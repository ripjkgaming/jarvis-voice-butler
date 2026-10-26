import asyncio
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest
from livekit.agents import function_tool, llm
from livekit.agents.types import NOT_GIVEN

import realtime_startup as startup


def test_cold_google_schema_import_registers_on_main_thread():
    # A fresh interpreter catches the order-dependent failure masked by tests
    # which import agent (and thus the Google plugin) before this module.
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio
from types import SimpleNamespace
from livekit.agents import function_tool, llm
from livekit.agents.types import NOT_GIVEN
import realtime_startup as startup

@function_tool
async def echo(value: str) -> str:
    '''Return the value without side effects.'''
    return value

class Session:
    _opts = SimpleNamespace(tool_behavior=NOT_GIVEN)
    _tools = llm.ToolContext.empty()
    async def update_tools(self, tools):
        self._tools = llm.ToolContext(tools)

async def main():
    session = startup.prepare_session(Session())
    await session.update_tools([echo])
    assert session._tools.parse_function_tools('google', tool_behavior=None,
        use_parameters_json_schema=False)[0]['name'] == 'echo'

asyncio.run(main())
""",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


@function_tool
async def harmless_echo(value: str) -> str:
    """Return the input without side effects."""
    return value


@pytest.mark.asyncio
async def test_compilation_off_loop_and_cache_scoped_to_updates(monkeypatch):
    loop_thread = threading.get_ident()
    compiled = []
    parse = llm.ToolContext.parse_function_tools

    def tracked(self, provider, **kwargs):
        compiled.append(threading.get_ident())
        return parse(self, provider, **kwargs)

    monkeypatch.setattr(llm.ToolContext, "parse_function_tools", tracked)

    class Session:
        _opts = SimpleNamespace(tool_behavior=NOT_GIVEN)
        _tools = llm.ToolContext.empty()
        restarts = 0

        async def update_tools(self, tools):
            ctx = llm.ToolContext(tools)
            if ctx != self._tools:
                self._tools = ctx
                self.restarts += 1

    session = startup.prepare_session(Session())
    await session.update_tools([harmless_echo])
    assert compiled and all(t != loop_thread for t in compiled)
    options = {"tool_behavior": None, "use_parameters_json_schema": False}
    schema = session._tools.parse_function_tools("google", **options)
    assert schema[0]["name"] == "harmless_echo"
    schema.clear()
    assert session._tools.parse_function_tools("google", **options)
    assert loop_thread not in compiled
    await session.update_tools([])
    assert session._tools.parse_function_tools("google", **options) == []
    assert session.restarts == 2
    assert session._tools.parse_function_tools("openai") == []
    assert compiled[-1] == loop_thread


@pytest.mark.asyncio
async def test_failed_compile_never_publishes_tools(monkeypatch):
    calls = []

    async def publish(tools):
        calls.append(tools)

    session = SimpleNamespace(
        update_tools=publish, _opts=SimpleNamespace(tool_behavior=NOT_GIVEN)
    )

    def fail(*args):
        raise ValueError("invalid schema")

    monkeypatch.setattr(startup, "_prepare", fail)
    startup.prepare_session(session)
    with pytest.raises(ValueError, match="invalid schema"):
        await session.update_tools([harmless_echo])
    assert calls == []


def test_tls_defaults_preserve_certificate_environment(monkeypatch):
    calls = []
    sentinel = object()
    monkeypatch.setenv("SSL_CERT_FILE", "/custom/ca.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "/custom/certs")
    monkeypatch.setattr(
        startup.ssl, "create_default_context", lambda **kw: calls.append(kw) or sentinel
    )
    assert startup.http_options().async_client_args["ssl"] is sentinel
    assert calls == [{"cafile": "/custom/ca.pem", "capath": "/custom/certs"}]


@pytest.mark.asyncio
async def test_real_assistant_can_be_constructed_on_worker(monkeypatch):
    import agent

    monkeypatch.setenv("GOOGLE_API_KEY", "offline-test-key")
    monkeypatch.setenv("JARVIS_PIPELINE", "realtime")
    assistant = await asyncio.to_thread(startup.build_assistant, agent.Assistant, None)
    assert assistant.tools
    assert any("confirm" in tool.id for tool in assistant.tools)


@pytest.mark.asyncio
async def test_initial_prepared_tools_publish_before_loop_can_connect(monkeypatch):
    initial = startup._prepare([harmless_echo], NOT_GIVEN)
    turns = []

    class Session:
        _opts = SimpleNamespace(tool_behavior=NOT_GIVEN)
        _tools = llm.ToolContext.empty()

        async def update_tools(self, tools):
            self._tools = llm.ToolContext(tools)
            turns.append("tools")

    def unexpected(*args):
        pytest.fail("Initial schemas must already be compiled before session creation")

    monkeypatch.setattr(startup, "_prepare", unexpected)
    session = startup.prepare_session(Session(), initial)
    asyncio.get_running_loop().call_soon(turns.append, "connect")
    await session.update_tools([harmless_echo])
    assert turns == ["tools"]
    await asyncio.sleep(0)
    assert turns == ["tools", "connect"]


def test_orphan_terminal_event_has_no_audio_or_transcript_to_drop(caplog):
    from google.genai import types
    from livekit.plugins.google.realtime import realtime_api

    sc = types.LiveServerContent(generation_complete=True, turn_complete=True)
    session = SimpleNamespace(_current_generation=None, _rejected_tool_calls=0)
    assert not realtime_api.RealtimeSession._is_new_generation(
        session, types.LiveServerMessage(server_content=sc)
    )
    realtime_api.RealtimeSession._handle_server_content(session, sc)
    assert "no active generation" in caplog.text
    # Actual payloads do cause the receiver to open a generation first.
    for field in ("input_transcription", "output_transcription"):
        content = types.LiveServerContent(
            **{field: types.Transcription(text="synthetic")}
        )
        assert realtime_api.RealtimeSession._is_new_generation(
            session, types.LiveServerMessage(server_content=content)
        )
