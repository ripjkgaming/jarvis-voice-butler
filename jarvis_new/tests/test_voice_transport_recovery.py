"""Realtime voice failover on jarvis_rt: the LiveBackend's ModelChain owns recovery.

No real models or provider sessions. LiveBackend runs over tests/_fake_live.FakeClient,
and the failures are injected as errors on the scripted connections.
"""

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _fake_live import FakeClient

from jarvis_rt import Agent, AgentSession, function_tool
from jarvis_rt.live import LiveBackend, ModelChain

SYNTHETIC_INSTRUCTIONS = "synthetic instructions"


class Main(Agent):
    def __init__(self):
        super().__init__(instructions=SYNTHETIC_INSTRUCTIONS)

    @function_tool()
    async def echo(self, text: str) -> str:
        """Echo."""
        return text


@pytest.fixture
def module(monkeypatch, tmp_path):
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    # No user chain override and no saved voice selection: the shipped chain.
    monkeypatch.delenv("JARVIS_VOICE_MODELS", raising=False)
    monkeypatch.setenv(
        "JARVIS_VOICE_SELECTION_FILE", str(tmp_path / "no-selection.json")
    )
    # Agent module import defines factories only. No provider session is started.
    spec = importlib.util.spec_from_file_location(
        "staged_voice_agent", Path(__file__).parents[1] / "src/agent.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _until(predicate, timeout=5.0):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout)


def _content_sent(conn):
    return [kw for kind, kw in conn.sent if kind == "content"]


def test_realtime_chain_is_the_voice_chain_with_a_60s_cooldown(module):
    chain = module._realtime_llm()
    assert isinstance(chain, ModelChain)
    assert chain.models == module.VOICE_MODEL_CHAIN
    assert chain.cooldown == 60.0


def test_user_voice_chain_override_stays_authoritative(module, monkeypatch):
    monkeypatch.setenv("JARVIS_VOICE_MODELS", "custom-live, other-live")
    assert module._realtime_llm().models == ["custom-live", "other-live"]


async def test_each_chain_model_gets_the_production_live_config(module):
    backend = LiveBackend(module._realtime_llm(), client=FakeClient(), watchdog=False)
    backend.agent = Main()
    for name in module.VOICE_MODEL_CHAIN:
        cfg = backend._config(name)
        aad = cfg.realtime_input_config.automatic_activity_detection
        assert aad.disabled is False
        assert cfg.speech_config.voice_config.prebuilt_voice_config.voice_name == (
            "Enceladus"
        )
        # The chain never forces a language; the provider picks it.
        assert cfg.speech_config.language_code is None
        assert [f.name for f in cfg.tools[0].function_declarations] == ["echo"]
    assert (
        backend._config(module.VOICE_MODEL_CHAIN[0]).generation_config.thinking_config
        is None
    )


async def test_failed_connect_moves_to_the_next_model_without_same_model_retry(module):
    client = FakeClient()
    models = module.VOICE_MODEL_CHAIN
    client.fail_models[models[0]] = RuntimeError("synthetic 1011")
    session = AgentSession(
        backend=LiveBackend(module._realtime_llm(), client=client, watchdog=False)
    )
    errors = []
    session.on("error", errors.append)
    await session.start(Main())
    try:
        await _until(lambda: len(client.connects) >= 2)
        assert [model for model, _ in client.connects[:2]] == models[:2]
        assert [str(e.error) for e in errors] == ["synthetic 1011"]
    finally:
        await session.aclose()


async def test_reply_in_flight_at_failure_is_not_reissued_on_the_fallback(module):
    client = FakeClient()
    models = module.VOICE_MODEL_CHAIN
    session = AgentSession(
        backend=LiveBackend(module._realtime_llm(), client=client, watchdog=False)
    )
    await session.start(Main())
    try:
        session.generate_reply(instructions="synthetic reply")
        await _until(
            lambda: any(
                kw.get("turn_complete") for kw in _content_sent(client.conns[0])
            )
        )
        client.conns[0].inbox.put_nowait(RuntimeError("synthetic 1011"))
        await _until(lambda: len(client.connects) >= 2)
        await asyncio.sleep(0.05)
        assert client.connects[1][0] == models[1]
        # The fallback opens with no client turn: the failed reply is not replayed.
        assert client.conns[1].sent == []
    finally:
        await session.aclose()


async def test_mid_session_failover_keeps_context_tools_and_instructions_without_replay(
    module,
):
    client = FakeClient()
    models = module.VOICE_MODEL_CHAIN
    chain = module._realtime_llm()
    backend = LiveBackend(chain, client=client, watchdog=False)
    session = AgentSession(backend=backend)
    errors = []
    session.on("error", errors.append)
    await session.start(Main())
    try:
        session.generate_reply(user_input="synthetic request")
        await _until(lambda: _content_sent(client.conns[0]))

        def replay():
            raise AssertionError("failover must not regenerate a reply")

        session.generate_reply = replay
        for index in range(len(models)):
            client.conns[index].inbox.put_nowait(
                RuntimeError(f"synthetic 1011 {index}")
            )
            if index + 1 < len(models):
                await _until(lambda n=index + 2: len(client.connects) >= n)
                model, cfg = client.connects[index + 1]
                assert model == models[index + 1]
                assert cfg.system_instruction.parts[0].text == SYNTHETIC_INSTRUCTIONS
                assert [f.name for f in cfg.tools[0].function_declarations] == ["echo"]
                # Chat history is re-sent to the new connection as context only.
                history = _content_sent(client.conns[index + 1])
                assert history, "chat context was not carried to the new model"
                assert history[0]["turn_complete"] is False
                assert history[0]["turns"][0].parts[0].text == "synthetic request"
                # Cooldown: the model that just failed is not picked again.
                assert chain.pick() != models[index]
        # Every failure is surfaced to the session as an error event.
        await _until(lambda: len(errors) == len(models))
        assert [str(e.error) for e in errors] == [
            f"synthetic 1011 {index}" for index in range(len(models))
        ]
        # Nothing was replayed: no turn was closed and no tool response was sent.
        for conn in client.conns[1:]:
            assert all(kw["turn_complete"] is False for kw in _content_sent(conn))
            assert all(kind != "tool_response" for kind, _ in conn.sent)
        # All models are cooling: the backend retries the one that failed longest ago.
        await _until(lambda: len(client.connects) >= len(models) + 1)
        assert client.connects[len(models)][0] == models[0]
    finally:
        await session.aclose()
