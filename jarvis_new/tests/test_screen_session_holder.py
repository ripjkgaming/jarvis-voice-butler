"""Actual jarvis_rt AgentSession lifecycle; fake RoomIO, no media/providers."""

import asyncio
from types import SimpleNamespace
from typing import ClassVar

import pytest

import jarvis_rt.room_io as room_io_module
from feature_sessions import FeatureSession
from jarvis_rt import Agent, AgentSession, ChatContext, stt
from jarvis_rt.events import CloseEvent, CloseReason
from jarvis_rt.room_io import RoomOptions
from screen_session_holder import (
    LocalControlEvent,
    PhysicalScreenControls,
    ScreenSessionHolder,
    SessionLifecycleError,
)


class Backend:
    async def close(self):
        pass


class FakeMicrophone:
    def __init__(self):
        self.entered = asyncio.Event()
        self.drained = False
        self.attached = False

    def on_attached(self):
        self.attached = True

    def on_detached(self):
        self.attached = False

    async def __anext__(self):
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.drained = True
        raise StopAsyncIteration


class FakeRoomIO:
    instances: ClassVar[list] = []
    entered = None
    release = None
    fail = False

    def __init__(self, session, room, options=None):
        self.room = room
        self.session = session
        self.options = options
        self.closed = False
        self.subscribed_fut = None
        self.linked_participant = None
        self.microphone = FakeMicrophone()
        self.__class__.instances.append(self)

    async def start(self, **kwargs):
        assert not self.session.input.audio_enabled
        assert not self.session.output.audio_enabled
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.fail:
            raise RuntimeError("synthetic RoomIO start failure")
        self.session.input.audio = self.microphone

    @property
    def _closed(self):
        # Mirrors RoomIO._closed, which the holder reads after retirement.
        return self.closed

    async def aclose(self):
        self.closed = True


@pytest.fixture
def rig(monkeypatch):
    FakeRoomIO.instances = []
    FakeRoomIO.entered = None
    FakeRoomIO.release = None
    FakeRoomIO.fail = False
    monkeypatch.setattr(room_io_module, "RoomIO", FakeRoomIO)
    sessions = []
    bindings = []
    received = []
    ending = []
    authority = {"owner": True}
    room = object()

    def session_factory():
        session = AgentSession(llm=None)
        sessions.append(session)
        return session

    def bind(session, events):
        feature = FeatureSession(
            session, backend=Backend(), authorized=lambda: authority["owner"]
        )
        feature.bind_caption_tail(SimpleNamespace(clear_sensitive=lambda: None))
        feature.install(realtime=True)
        events.on("synthetic_host_event", received.append)
        bindings.append(feature)
        return feature

    async def end_call():
        ending.append(room)

    holder = ScreenSessionHolder(
        room=room,
        session_factory=session_factory,
        agent_factory=lambda: Agent(instructions="Synthetic test; no providers."),
        bind=bind,
        room_options_factory=lambda session: RoomOptions(
            delete_room_on_close=True,  # adapter must override, never inherit
        ),
        owner_allowed=lambda: authority["owner"],
        end_call=end_call,
    )
    controls = PhysicalScreenControls(
        holder,
        authorize_event=lambda event: event.source == "test-owner-button",
    )
    return SimpleNamespace(
        holder=holder,
        controls=controls,
        sessions=sessions,
        bindings=bindings,
        received=received,
        ending=ending,
        authority=authority,
        room=room,
    )


def event(action, identity="one", *, quiet=True, source="test-owner-button"):
    return LocalControlEvent(identity, source, action, quiet_confirmed=quiet)


@pytest.mark.asyncio
async def test_actual_sdk_rollover_room_preserved_and_generation_events_retired(rig):
    await rig.controls.dispatch(event("resume", "initial"))
    old = rig.sessions[0]
    assert old._started and old._room_io is not None
    assert old.input.audio_enabled
    old_generation = rig.holder.generation
    old.emit("synthetic_host_event", "ordinary")
    assert rig.received == ["ordinary"]
    async with rig.bindings[0].sensitive():
        old.emit("synthetic_host_event", "sensitive")
    old.emit("synthetic_host_event", "late")
    assert rig.received == ["ordinary"]
    await rig.controls.dispatch(event("resume", "after-readback"))
    new = rig.sessions[1]
    assert new is not old and new._started
    assert old._closed and old._room_io.closed
    assert rig.bindings[0]._closed
    assert not rig.bindings[1].screen.disclosure_accepted
    assert not rig.bindings[1].screen_authorized()
    assert not rig.holder.accepts_event(old_generation)
    old.emit("synthetic_host_event", "stale")
    new.emit("synthetic_host_event", "fresh")
    assert rig.received == ["ordinary", "fresh"]
    assert all(io.room is rig.room for io in FakeRoomIO.instances)
    assert all(not io.options.delete_room_on_close for io in FakeRoomIO.instances)
    assert not rig.ending
    await rig.controls.dispatch(event("hangup", "final"))
    assert rig.ending == [rig.room]
    assert all(io.closed for io in FakeRoomIO.instances)


@pytest.mark.asyncio
async def test_physical_stop_drains_sdk_and_explicit_resume_is_required(rig):
    await rig.controls.dispatch(event("resume"))
    await rig.controls.dispatch(event("stop", "stop"))
    assert rig.holder.state == "stopped"
    assert rig.sessions[0]._closed
    assert not rig.sessions[0].input.audio_enabled
    assert not rig.ending
    with pytest.raises(SessionLifecycleError):
        await rig.controls.dispatch(event("resume", "echo", source="transcript"))
    with pytest.raises(SessionLifecycleError):
        await rig.controls.dispatch(event("resume", "unconfirmed", quiet=False))
    await rig.controls.dispatch(event("resume", "physical"))
    assert len(rig.sessions) == 2
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_guest_revokes_before_enabling_replacement(rig):
    await rig.controls.dispatch(event("resume"))
    rig.authority["owner"] = False
    assert not rig.holder.accepts_event(rig.holder.generation)
    await rig.holder.revoke_authority()
    assert rig.sessions[0]._closed
    with pytest.raises(SessionLifecycleError):
        await rig.controls.dispatch(event("resume", "guest"))
    assert len(rig.sessions) == 1 and not rig.ending
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_cancel_during_actual_sdk_start_cleans_partial_roomio(rig):
    FakeRoomIO.entered = asyncio.Event()
    FakeRoomIO.release = asyncio.Event()
    run = asyncio.create_task(rig.controls.dispatch(event("resume")))
    await FakeRoomIO.entered.wait()
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert FakeRoomIO.instances[0].closed
    assert rig.sessions[0]._closed
    assert rig.sessions[0]._room_io.closed
    assert rig.bindings[0]._closed
    assert rig.holder.state == "stopped" and not rig.ending
    FakeRoomIO.release = None
    await rig.controls.dispatch(event("resume", "retry"))
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_sdk_start_error_cleans_partial_slot_and_can_retry(rig):
    FakeRoomIO.fail = True
    with pytest.raises(SessionLifecycleError):
        await rig.controls.dispatch(event("resume"))
    assert FakeRoomIO.instances[0].closed
    assert rig.sessions[0]._closed
    assert rig.sessions[0]._room_io.closed
    assert not rig.sessions[0].input.audio_enabled
    assert rig.bindings[0]._closed and not rig.ending
    FakeRoomIO.fail = False
    await rig.controls.dispatch(event("resume", "retry"))
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_unexpected_sdk_close_does_not_end_room(rig):
    await rig.controls.dispatch(event("resume"))
    old = rig.sessions[0]
    await old.aclose()  # real SDK emits close, wrapper retires its generation
    await rig.holder.drain()
    assert rig.holder.state == "stopped" and not rig.ending
    old.emit("synthetic_host_event", "old")
    assert not rig.received
    await rig.controls.dispatch(event("resume", "new"))
    # A queued old close must not kill the replacement.
    old.emit("close", CloseEvent(reason=CloseReason.USER_INITIATED))
    assert rig.holder.state == "listening"
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_duplicate_physical_resume_is_not_replayed(rig):
    await rig.controls.dispatch(event("resume"))
    with pytest.raises(SessionLifecycleError):
        await rig.controls.dispatch(event("resume"))
    assert len(rig.sessions) == 1
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_final_hangup_idempotent_and_rejects_resume(rig):
    await rig.controls.dispatch(event("resume"))
    await rig.holder.hangup()
    await rig.holder.hangup()
    assert rig.ending == [rig.room]
    with pytest.raises(SessionLifecycleError):
        await rig.controls.dispatch(event("resume", "after-hangup"))


@pytest.mark.asyncio
async def test_authority_lost_while_roomio_starts_never_enables(rig):
    FakeRoomIO.entered = asyncio.Event()
    FakeRoomIO.release = asyncio.Event()
    run = asyncio.create_task(rig.controls.dispatch(event("resume")))
    await FakeRoomIO.entered.wait()
    rig.authority["owner"] = False
    FakeRoomIO.release.set()
    with pytest.raises(SessionLifecycleError):
        await run
    assert not rig.sessions[0].input.audio_enabled
    assert rig.sessions[0]._closed
    assert FakeRoomIO.instances[0].closed and not rig.ending
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_stop_during_start_cancels_and_drains_before_returning(rig):
    FakeRoomIO.entered = asyncio.Event()
    FakeRoomIO.release = asyncio.Event()
    run = asyncio.create_task(rig.controls.dispatch(event("resume")))
    await FakeRoomIO.entered.wait()
    await rig.controls.dispatch(event("stop", "stop-startup"))
    with pytest.raises(asyncio.CancelledError):
        await run
    assert FakeRoomIO.instances[0].closed
    assert rig.sessions[0]._closed and not rig.ending
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_cancelled_stop_waits_for_real_sdk_on_exit_drain(rig):
    entered, release = asyncio.Event(), asyncio.Event()

    class ExitAgent(Agent):
        async def on_exit(self):
            entered.set()
            await release.wait()

    rig.holder.agent_factory = lambda: ExitAgent(instructions="Synthetic")
    await rig.controls.dispatch(event("resume"))
    run = asyncio.create_task(rig.holder.stop())
    await entered.wait()
    run.cancel()
    await asyncio.sleep(0)
    run.cancel()
    assert not run.done()
    assert not rig.holder.accepts_event(rig.holder.generation)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert rig.sessions[0]._closed
    assert FakeRoomIO.instances[0].closed and not rig.ending
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_cancelled_hangup_still_drains_then_ends_room_once(rig):
    entered, release = asyncio.Event(), asyncio.Event()

    class ExitAgent(Agent):
        async def on_exit(self):
            entered.set()
            await release.wait()

    rig.holder.agent_factory = lambda: ExitAgent(instructions="Synthetic")
    await rig.controls.dispatch(event("resume"))
    run = asyncio.create_task(rig.holder.hangup())
    await entered.wait()
    run.cancel()
    await asyncio.sleep(0)
    assert not rig.ending and not run.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await run
    await rig.holder.hangup()
    assert rig.ending == [rig.room]
    assert rig.sessions[0]._closed


@pytest.mark.asyncio
async def test_resume_event_expires_during_startup(rig):
    now = {"value": 100.0}
    rig.holder.clock = lambda: now["value"]
    FakeRoomIO.entered = asyncio.Event()
    FakeRoomIO.release = asyncio.Event()
    run = asyncio.create_task(
        rig.controls.dispatch(
            LocalControlEvent("expiring", "test-owner-button", "resume", True, 100.0)
        )
    )
    await FakeRoomIO.entered.wait()
    now["value"] = 131.0
    FakeRoomIO.release.set()
    with pytest.raises(SessionLifecycleError):
        await run
    assert not rig.sessions[0].input.audio_enabled
    assert FakeRoomIO.instances[0].closed
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_reused_session_agent_or_user_context_is_refused(rig):

    agent = Agent(instructions="Synthetic")
    rig.holder.agent_factory = lambda: agent
    await rig.controls.dispatch(event("resume"))
    with pytest.raises(SessionLifecycleError):
        await rig.controls.dispatch(event("resume", "reused-agent"))
    assert rig.holder.state == "stopped"
    context = ChatContext.empty()
    context.add_message(role="user", content="Synthetic old private context")
    rig.holder.agent_factory = lambda: Agent(instructions="Synthetic", chat_ctx=context)
    with pytest.raises(SessionLifecycleError):
        await rig.controls.dispatch(event("resume", "old-context"))
    rig.holder.session_factory = lambda: rig.sessions[0]
    rig.holder.agent_factory = lambda: Agent(instructions="Synthetic")
    with pytest.raises(SessionLifecycleError):
        await rig.controls.dispatch(event("resume", "reused-session"))
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_late_queued_callback_closure_checks_generation(rig):
    await rig.controls.dispatch(event("resume"))
    old = rig.sessions[0]
    callback = next(iter(old._handlers["synthetic_host_event"]))
    await rig.controls.dispatch(event("resume", "new"))
    callback("late callback held outside event bus")
    assert not rig.received
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_partial_bind_failure_retires_feature_resource(rig):
    old_bind = rig.holder.bind

    def failed_bind(session, events):
        old_bind(session, events)
        raise RuntimeError("Synthetic bind failure")

    rig.holder.bind = failed_bind
    with pytest.raises(SessionLifecycleError):
        await rig.controls.dispatch(event("resume"))
    assert rig.bindings[0]._closed
    assert rig.bindings[0]._handlers == []
    assert rig.holder.state == "stopped"
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_provider_retirement_error_blocks_new_sdk_generation(rig):
    async def failed_close(session):
        raise RuntimeError("Synthetic owned provider cleanup failure")

    rig.holder.close_providers = failed_close
    await rig.controls.dispatch(event("resume"))
    with pytest.raises(SessionLifecycleError):
        await rig.controls.dispatch(event("stop", "stop"))
    assert rig.holder.state == "failed"
    with pytest.raises(SessionLifecycleError):
        await rig.controls.dispatch(event("resume", "unsafe-retry"))
    assert len(rig.sessions) == 1 and not rig.ending
    rig.holder.close_providers = None
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_no_physical_control_is_implicitly_authorized(rig):
    controls = PhysicalScreenControls(rig.holder)
    with pytest.raises(SessionLifecycleError):
        await controls.dispatch(event("resume"))
    assert not rig.sessions
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_session_stt_providers_are_fresh_and_closed_on_rollover_and_hangup(rig):
    class Provider(stt.STT):
        def __init__(self):
            super().__init__(
                capabilities=stt.STTCapabilities(streaming=True, interim_results=False)
            )
            self.closed = False

        async def _recognize_impl(self, *args, **kwargs):
            pytest.fail("No batch recognition allowed")

        async def aclose(self):
            self.closed = True

    providers = []

    def factory():
        provider = Provider()
        providers.append(provider)
        session = AgentSession(llm=None, stt=provider, turn_detection="manual")
        rig.sessions.append(session)
        return session

    rig.holder.session_factory = factory
    await rig.controls.dispatch(event("resume"))
    assert FakeRoomIO.instances[0].room is rig.room
    await rig.controls.dispatch(event("resume", "rollover"))
    assert providers[0].closed and FakeRoomIO.instances[0].closed
    assert providers[0] is not providers[1] and not providers[1].closed
    await rig.holder.hangup()
    assert providers[1].closed


@pytest.mark.asyncio
async def test_agent_start_error_closes_partial_providers_and_roomio(rig):
    class BrokenAgent(Agent):
        async def on_enter(self):
            raise RuntimeError("Synthetic on_enter failure")

    class Provider(stt.STT):
        def __init__(self):
            super().__init__(
                capabilities=stt.STTCapabilities(streaming=True, interim_results=False)
            )
            self.closed = False

        async def _recognize_impl(self, *args, **kwargs):
            pytest.fail("No recognition allowed")

        async def aclose(self):
            self.closed = True

    provider = Provider()
    rig.holder.agent_factory = lambda: BrokenAgent(
        instructions="Synthetic", stt=provider
    )
    with pytest.raises(SessionLifecycleError):
        await rig.controls.dispatch(event("resume"))
    assert provider.closed
    assert rig.sessions[0]._closed
    assert rig.sessions[0]._room_io.closed
    assert FakeRoomIO.instances[0].closed
    assert rig.holder.state == "stopped"
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_startup_budget_expires_with_clean_cancellation_accounting(
    rig, monkeypatch
):
    import screen_session_holder

    deadline = screen_session_holder._deadline
    monkeypatch.setattr(screen_session_holder, "_deadline", lambda _: deadline(0.01))
    FakeRoomIO.release = asyncio.Event()
    with pytest.raises(SessionLifecycleError):
        await rig.controls.dispatch(event("resume"))
    assert not asyncio.current_task().cancelling()
    assert FakeRoomIO.instances[0].closed
    assert rig.holder.state == "stopped"
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_cancellation_during_retirement_does_not_start_replacement(rig):
    entered, release = asyncio.Event(), asyncio.Event()

    class ExitAgent(Agent):
        async def on_exit(self):
            entered.set()
            await release.wait()

    rig.holder.agent_factory = lambda: ExitAgent(instructions="Synthetic")
    await rig.controls.dispatch(event("resume"))
    run = asyncio.create_task(rig.controls.dispatch(event("resume", "rollover")))
    await entered.wait()
    run.cancel()
    await asyncio.sleep(0)
    assert not run.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert len(rig.sessions) == 1
    assert rig.sessions[0]._closed
    assert rig.holder.state == "stopped"
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_sdk_on_enter_must_settle_before_input_is_enabled(rig):
    entered, release = asyncio.Event(), asyncio.Event()

    class EnterAgent(Agent):
        async def on_enter(self):
            entered.set()
            await release.wait()

    rig.holder.agent_factory = lambda: EnterAgent(instructions="Synthetic")
    run = asyncio.create_task(rig.controls.dispatch(event("resume")))
    await entered.wait()
    assert not rig.sessions[0].input.audio_enabled
    assert not rig.holder.accepts_event(rig.holder.generation)
    release.set()
    await run
    assert rig.sessions[0].input.audio_enabled
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_sdk_startup_speech_is_refused_before_microphone_enable(rig):
    class SpeakingAgent(Agent):
        async def on_enter(self):
            async def no_samples():
                if False:
                    yield None

            self.session.say(
                "Synthetic startup speech", audio=no_samples(), add_to_chat_ctx=False
            )

    rig.holder.agent_factory = lambda: SpeakingAgent(instructions="Synthetic")
    with pytest.raises((asyncio.CancelledError, SessionLifecycleError)):
        await rig.controls.dispatch(event("resume"))
    assert not rig.sessions[0].input.audio_enabled
    assert rig.sessions[0]._closed
    assert rig.holder.state == "stopped"
    assert FakeRoomIO.instances[0].closed
    await rig.holder.hangup()


@pytest.mark.asyncio
async def test_hangup_can_retry_repaired_drain_but_not_uncertain_room_effect(rig):
    async def failed_close(session):
        raise RuntimeError("Synthetic provider drain error")

    await rig.controls.dispatch(event("resume"))
    rig.holder.close_providers = failed_close
    with pytest.raises(SessionLifecycleError):
        await rig.holder.hangup()
    assert not rig.ending
    rig.holder.close_providers = None
    calls = []

    async def uncertain_room_end():
        calls.append("attempt")
        raise RuntimeError("Synthetic room deletion acknowledgement lost")

    rig.holder.end_call = uncertain_room_end
    with pytest.raises(SessionLifecycleError):
        await rig.holder.hangup()
    with pytest.raises(SessionLifecycleError):
        await rig.holder.hangup()
    assert calls == ["attempt"]
    assert rig.holder.state == "failed"
