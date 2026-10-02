"""Native Gemini turn detection must not wait for a noisy local VAD."""

from types import SimpleNamespace

import pytest
from livekit.agents import TurnHandlingOptions

import agent


def test_voice_chain_keeps_native_activity_detection(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    for model in agent._realtime_llm()._models:
        assert (
            not model._opts.realtime_input_config.automatic_activity_detection.disabled
        )


@pytest.mark.asyncio
async def test_realtime_session_uses_provider_turns_without_local_vad(monkeypatch):
    monkeypatch.setenv("JARVIS_PIPELINE", "realtime")
    session = agent._session_for_pipeline(
        TurnHandlingOptions(turn_detection="vad", interruption=agent.barge_in("vad")),
        vad=object(),
    )
    assert session.turn_detection == "realtime_llm"
    assert session.vad is None
    assert session._opts.interruption["enabled"]
    assert session._opts.user_away_timeout == agent.IDLE_HANGUP_SECONDS


@pytest.mark.asyncio
async def test_default_realtime_does_not_load_silero(monkeypatch):
    monkeypatch.setenv("JARVIS_PIPELINE", "realtime")

    def no_local_model(**kwargs):
        pytest.fail("Native realtime must not load a local speech detector")

    monkeypatch.setattr(
        agent, "silero", SimpleNamespace(VAD=SimpleNamespace(load=no_local_model))
    )
    assert agent._session_for_pipeline(TurnHandlingOptions()).vad is None


@pytest.mark.asyncio
async def test_native_activity_uses_provider_speech_events_without_local_interruptions(
    monkeypatch,
):
    import socket

    from livekit.agents import Agent, llm
    from livekit.agents.voice.agent_activity import AgentActivity

    def no_network(*args, **kwargs):
        pytest.fail("Offline activity validation must not connect to a provider")

    def no_provider_session(*args, **kwargs):
        pytest.fail("Offline activity validation must not start a provider session")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(
        agent.google.realtime.RealtimeModel, "session", no_provider_session
    )
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("JARVIS_PIPELINE", "realtime")
    session = agent._session_for_pipeline(TurnHandlingOptions())
    model = agent._realtime_llm()
    try:
        activity = AgentActivity(
            Agent(instructions="Synthetic offline activity check.", llm=model), session
        )
        assert activity._rt_turn_detection_enabled
        assert activity._turn_detection == "realtime_llm"
        assert activity.vad is None
        assert activity._interruption_detector is None
        assert not activity._interruption_by_audio_activity_enabled
        assert activity.allow_interruptions

        states = []
        session.on("user_state_changed", lambda event: states.append(event.new_state))
        activity._interrupt_by_audio_activity()
        assert states == []

        activity._on_input_speech_started(llm.InputSpeechStartedEvent())
        assert session.user_state == "speaking"
        activity._on_input_speech_stopped(
            llm.InputSpeechStoppedEvent(user_transcription_enabled=False)
        )
        assert session.user_state == "listening"
        assert states == ["speaking", "listening"]
    finally:
        await model.aclose()


@pytest.mark.parametrize("pipeline", ["realtime", "local", "direct"])
def test_room_input_observer_is_wired_only_for_native_and_preserves_agc(
    monkeypatch, pipeline
):
    monkeypatch.setenv("JARVIS_PIPELINE", pipeline)
    session = SimpleNamespace()
    options = agent._audio_input_options(session)
    if pipeline == "realtime":
        assert options.noise_cancellation is session._jarvis_input_activity
        assert isinstance(options.noise_cancellation, agent._InputActivity)
        assert options.auto_gain_control is True
    else:
        assert options.noise_cancellation is None
        assert not hasattr(session, "_jarvis_input_activity")
