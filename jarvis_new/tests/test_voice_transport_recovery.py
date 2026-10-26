"""No real models/provider sessions; assertions on installed SDK configuration."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def module(monkeypatch):
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    # Agent module import defines factories only. No provider session is started.
    spec = importlib.util.spec_from_file_location(
        "staged_voice_agent", Path(__file__).parents[1] / "src/agent.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_failure_moves_to_existing_fallback_without_reissuing_reply(
    module, monkeypatch
):
    children = []
    options = {}

    def make(mid):
        item = SimpleNamespace(model=mid)
        children.append(item)
        return item

    def adapter(models, **kwargs):
        options.update(kwargs)
        return SimpleNamespace(models=models)

    monkeypatch.setattr(module, "_voice_model", make)
    monkeypatch.setattr(module, "RealtimeModelFallbackAdapter", adapter)
    model = module._realtime_llm()
    assert [c.model for c in model.models] == module.VOICE_MODEL_CHAIN
    assert options["cooldown"] == 60.0
    assert options["regenerate_on_swap"] is False


def test_child_transport_retries_disabled_for_adapter_owned_recovery(
    module, monkeypatch
):
    captured = []
    import gemini_watchdog
    import realtime_startup

    monkeypatch.setattr(gemini_watchdog, "install", lambda m: m)
    monkeypatch.setattr(realtime_startup, "install", lambda m: m)
    monkeypatch.setattr(realtime_startup, "http_options", lambda: None)
    monkeypatch.setattr(
        module.google.realtime,
        "RealtimeModel",
        lambda **k: captured.append(k) or SimpleNamespace(),
    )
    for name in module.VOICE_MODEL_CHAIN:
        module._voice_model(name)
    for opts in captured:
        assert opts["conn_options"].max_retry == 0
        assert "language" not in opts
        assert not opts["realtime_input_config"].automatic_activity_detection.disabled
        assert opts["voice"] == "Enceladus"
    assert "thinking_config" not in captured[0]


def test_user_voice_chain_override_stays_authoritative(module, monkeypatch):
    monkeypatch.setenv("JARVIS_VOICE_MODELS", "custom-live, other-live")
    called = []
    monkeypatch.setattr(
        module, "_voice_model", lambda name: called.append(name) or object()
    )
    monkeypatch.setattr(
        module, "RealtimeModelFallbackAdapter", lambda models, **k: models
    )
    module._realtime_llm()
    assert called == ["custom-live", "other-live"]


@pytest.mark.asyncio
async def test_installed_sdk_failover_preserves_context_and_does_not_replay(
    module, monkeypatch
):
    import time

    from livekit import rtc
    from livekit.agents import llm
    from livekit.agents.llm.realtime import RealtimeCapabilities, RealtimeModelError

    class Child(rtc.EventEmitter):
        def __init__(self, model):
            super().__init__()
            self.realtime_model = model
            self.chat_ctx = llm.ChatContext()
            self.closed = False
            self.updates = []

        async def _update_session(self, **kwargs):
            self.updates.append(kwargs)
            self.chat_ctx = kwargs["chat_ctx"]

        async def aclose(self):
            self.closed = True

    class Model(llm.RealtimeModel):
        def __init__(self):
            super().__init__(
                capabilities=RealtimeCapabilities(
                    message_truncation=True,
                    turn_detection=True,
                    user_transcription=True,
                    auto_tool_reply_generation=True,
                    audio_output=True,
                    manual_function_calls=True,
                )
            )
            self.children = []

        def session(self, **kwargs):
            child = Child(self)
            self.children.append(child)
            return child

        async def aclose(self):
            pass

    models = [Model() for _ in module.VOICE_MODEL_CHAIN]
    source = iter(models)
    monkeypatch.delenv("JARVIS_VOICE_MODELS", raising=False)
    monkeypatch.setattr(module, "_voice_model", lambda name: next(source))
    adapter = module._realtime_llm()  # actual installed SDK adapter
    session = adapter.session()
    interrupted = []

    async def interrupt(**kwargs):
        interrupted.append(kwargs)

    def replay():
        raise AssertionError("Automatic reply replay can repeat a tool request")

    context = llm.ChatContext()
    context.add_message(role="user", content="synthetic request")
    session._agent_session = SimpleNamespace(
        agent_state="speaking",
        interrupt=interrupt,
        generate_reply=replay,
        current_agent=SimpleNamespace(chat_ctx=context),
    )
    tool_marker = object()
    session._instructions = "synthetic instructions"
    session._tools = [tool_marker]
    errors = []
    session.on("error", errors.append)
    try:
        for index in (0, 1):
            models[index].children[0].emit(
                "error",
                RealtimeModelError(
                    timestamp=time.time(),
                    label="synthetic provider",
                    error=RuntimeError("synthetic 1011"),
                    recoverable=False,
                ),
            )
            await session._swap_task
            assert session._active_index == index + 1
            assert models[index].children[0].closed
            latest = models[index + 1].children[0].updates[-1]
            assert latest["chat_ctx"] is context
            assert latest["tools"] == [tool_marker]
            assert latest["instructions"] == "synthetic instructions"
            assert session._cooldown_deadline[index] > time.time() + 58
        models[2].children[0].emit(
            "error",
            RealtimeModelError(
                timestamp=time.time(),
                label="synthetic provider",
                error=RuntimeError("synthetic 1011"),
                recoverable=False,
            ),
        )
        assert [e.recoverable for e in errors] == [True, True, False]
        assert len(interrupted) == 2
        assert [len(m.children) for m in models] == [1, 1, 1]
    finally:
        await session.aclose()
        await adapter.aclose()
