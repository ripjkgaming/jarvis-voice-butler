"""Host-only SDK generation rotation for permissioned private screen speech.

Not a tool, transcript command or default entrypoint. Host factories must build
fresh providers/Agent/chat context, bind FeatureSession before ordinary callbacks,
and authenticate the physical control source. No capture consent is transferred.
The holder owns engine close; the host end_call callback owns final room teardown.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import inspect
import time
import weakref
from dataclasses import dataclass, field
from importlib.metadata import version as _sdk_version

from livekit.agents import Agent, AgentSession
from livekit.agents.utils import is_given
from livekit.agents.voice.events import CloseReason
from livekit.agents.voice.room_io import RoomOptions

from feature_sessions import FeatureSession
from screen_understanding import _deadline


class SessionLifecycleError(Exception):
    """Content-free lifecycle refusal; no provider or screen evidence in errors."""


@dataclass(frozen=True)
class LocalControlEvent:
    event_id: str
    source: str
    action: str
    quiet_confirmed: bool = False
    received_at: float = field(default_factory=time.monotonic)


@dataclass
class _Slot:
    generation: int
    session: AgentSession
    features: FeatureSession | None = None
    handlers: list = field(default_factory=list)
    providers: list = field(default_factory=list)


class GenerationEvents:
    """Host observers must register through this generation/privacy gate."""

    def __init__(self, holder, slot):
        self.holder = holder
        self.slot = slot

    def on(self, name, callback):
        if inspect.iscoroutinefunction(callback):
            raise SessionLifecycleError("Host event observers must be synchronous")

        def guarded(event):
            if self.holder.accepts_event(self.slot.generation):
                callback(event)

        self.slot.session.on(name, guarded)
        self.slot.handlers.append((name, guarded))
        return guarded


class ScreenSessionHolder:
    """Rotate actual AgentSession engines while preserving the connected room.

    All factories/bind are synchronous constructors. bind installs FeatureSession
    and a fresh caption sink and registers normal app observers via events.on.
    It must not load screen consent, start detached jobs or reuse Agent/providers.
    room_options_factory rebuilds generation-owned I/O (including caption tails).
    end_call is async and may delete/disconnect the room only on final hangup.
    Owner/guest transitions must invoke await revoke_authority() immediately.
    """

    def __init__(
        self,
        *,
        room,
        session_factory,
        agent_factory,
        bind,
        room_options_factory,
        owner_allowed,
        end_call,
        close_providers=None,
        clock=time.monotonic,
    ):
        self.room = room
        self.session_factory = session_factory
        self.agent_factory = agent_factory
        self.bind = bind
        self.room_options_factory = room_options_factory
        self.owner_allowed = owner_allowed
        self.end_call = end_call
        self.close_providers = close_providers
        self.clock = clock
        self.generation = 0
        self.state = "stopped"
        self._slot = None
        self._lock = asyncio.Lock()
        self._resume_task = None
        self._background = set()
        self._hangup_task = None
        self._end_call_started = False
        self._final = False
        self._sessions = weakref.WeakSet()
        self._agents = weakref.WeakSet()
        self._providers = weakref.WeakSet()

    @property
    def session(self):
        """Host-only current SDK object; callers must not cache across rollover."""
        return self._slot.session if self._slot is not None else None

    def accepts_event(self, generation):
        slot = self._slot
        return bool(
            not self._final
            and self.state == "listening"
            and slot is not None
            and generation == self.generation == slot.generation
            and self.owner_allowed()
            and slot.features is not None
            and not slot.features.input_quarantined
        )

    def _invalidate(self):
        self.generation += 1  # retire callbacks before the first cleanup await
        self.state = "stopped"
        if self._slot is not None:
            session = self._slot.session
            session.input.set_audio_enabled(False)
            session.output.set_transcription_enabled(False)
            session.output.set_audio_enabled(False)
            if self._slot.features is not None:
                self._slot.features.revoke_authority()
        task = self._resume_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    async def stop(self):
        """Physical stop/revocation: mute synchronously, then drain owned SDK."""
        self._invalidate()
        await _owned(self._stop_locked())

    async def revoke_authority(self):
        await self.stop()

    async def _stop_locked(self):
        async with self._lock:
            await self._retire_locked()
            self.state = "closed" if self._final else "stopped"

    async def _retire_locked(self):
        slot = self._slot
        if slot is None:
            return
        failures = []
        for name, handler in slot.handlers:
            slot.session.off(name, handler)
        slot.handlers.clear()
        if slot.features is not None:
            try:
                await slot.features.close()
            except Exception as exc:
                failures.append(exc)
        try:
            await _close_sdk(slot.session)
        except Exception as exc:
            failures.append(exc)
        for provider in slot.providers:
            try:
                await provider.aclose()
            except Exception as exc:
                failures.append(exc)
        if self.close_providers is not None:
            try:
                await self.close_providers(slot.session)
            except Exception as exc:
                failures.append(exc)
        if failures:
            # Keep ownership for retry; never enable replacement after failed drain.
            self.state = "failed"
            raise SessionLifecycleError("SDK retirement did not complete") from None
        self._slot = None

    def _acquire_providers(self, slot, container):
        for name in ("llm", "stt", "tts"):
            provider = getattr(container, name)
            if not is_given(provider) or provider is None:
                continue
            if any(provider is owned for owned in slot.providers):
                continue
            if provider in self._providers:
                raise SessionLifecycleError("Fresh SDK providers are required")
            self._providers.add(provider)
            slot.providers.append(provider)

    async def resume(self, *, quiet_confirmed=False, expires_at=None):
        """Host-only physical resume. Controls authenticate/deduplicate the event."""
        if (
            quiet_confirmed is not True
            or not self.owner_allowed()
            or self._final
            or self.state in {"starting", "failed"}
            or (self._resume_task is not None and not self._resume_task.done())
        ):
            raise SessionLifecycleError("Fresh physical owner resume is required")
        if self._slot and self._slot.features and self._slot.features.sensitive_active:
            raise SessionLifecycleError("Stop private speech and wait before resuming")
        self._invalidate()
        generation = self.generation
        self._resume_task = asyncio.current_task()
        expires_at = self.clock() + 30 if expires_at is None else expires_at

        def check():
            if (
                self._final
                or generation != self.generation
                or not self.owner_allowed()
                or self.clock() > expires_at
            ):
                raise SessionLifecycleError("Resume was revoked")

        try:
            async with self._lock:
                await _owned(self._retire_locked())
                check()
                self.state = "starting"
                if _sdk_version("livekit-agents") != "1.8.2":
                    raise SessionLifecycleError("SDK lifecycle version requires review")
                session = self.session_factory()
                if not isinstance(session, AgentSession):
                    raise SessionLifecycleError("Installed SDK session is required")
                if session in self._sessions:
                    raise SessionLifecycleError("Reused SDK session is forbidden")
                self._sessions.add(session)
                slot = _Slot(generation, session)
                self._slot = slot  # acquire ownership before agent/bind/start can fail
                session.input.set_audio_enabled(False)
                session.input.set_video_enabled(False)
                session.output.set_audio_enabled(False)
                session.output.set_transcription_enabled(False)
                self._acquire_providers(slot, session)
                _require_partial_close_contract(session)
                agent = self.agent_factory()
                if isinstance(agent, Agent):
                    self._acquire_providers(slot, agent)
                if (
                    not isinstance(agent, Agent)
                    or agent._activity is not None
                    or agent in self._agents
                    or any(
                        item.type != "message" or item.role != "system"
                        for item in agent.chat_ctx.items
                    )
                ):
                    raise SessionLifecycleError("Fresh SDK agent is required")
                self._agents.add(agent)
                slot.features = self.bind(session, GenerationEvents(self, slot))
                if (
                    not isinstance(slot.features, FeatureSession)
                    or slot.features.session is not session
                    or slot.features.input_quarantined
                    or slot.features.screen.disclosure_accepted
                    or slot.features.screen_authorized()
                ):
                    raise SessionLifecycleError(
                        "Fresh unconsented feature binding is required"
                    )
                options = copy.copy(self.room_options_factory(session))
                if not isinstance(options, RoomOptions):
                    raise SessionLifecycleError("SDK room options are required")
                options.delete_room_on_close = False
                # The ordinary lk.chat stream is remote/synthetic input, not a
                # physical resume source. Only actual microphone input is restored.
                options.text_input = False
                options.video_input = False

                def closed(event):
                    if self._slot is slot and self.generation == generation:
                        self._invalidate()
                        task = asyncio.create_task(self._stop_locked())
                        self._background.add(task)
                        task.add_done_callback(self._background.discard)
                        task.add_done_callback(_consume)

                session.on("close", closed)
                slot.handlers.append(("close", closed))

                def startup_speech(event):
                    if self._slot is slot and self.state == "starting":
                        self._invalidate()

                session.on("speech_created", startup_speech)
                slot.handlers.append(("speech_created", startup_speech))
                async with _deadline(20):
                    await session.start(
                        agent,
                        room=self.room,
                        room_options=options,
                        record=False,
                        session_host=False,
                    )
                    # SDK start schedules on_enter without awaiting it. It must
                    # settle silently before enabling the fresh microphone.
                    if session._activity is not None:
                        entered = session._activity._on_enter_task
                        if entered is not None:
                            await entered
                check()
                # New caption sink/context/providers, old SDK fully drained. No
                # await between enabling and publishing the listening generation.
                session.output.set_audio_enabled(True)
                session.output.set_transcription_enabled(True)
                session.input.set_audio_enabled(True)
                check()  # reentrant authority changes fail closed
                self.state = "listening"
                return {"status": "listening", "generation": generation}
        except BaseException as exc:
            if self._slot is not None and self._slot.features is None:
                self._slot.features = getattr(
                    self._slot.session, "jarvis_features", None
                )
            self._invalidate()
            await _finish(self._stop_locked())
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise SessionLifecycleError("Screen session resume failed safely") from None
        finally:
            if self._resume_task is asyncio.current_task():
                self._resume_task = None

    async def hangup(self):
        """Final job shutdown only: retire every engine, then end the call once."""
        if (
            self._hangup_task is not None
            and self._hangup_task.done()
            and not self._end_call_started
            and (
                self._hangup_task.cancelled()
                or self._hangup_task.exception() is not None
            )
        ):
            self._hangup_task = (
                None  # retry a repaired drain, never uncertain room deletion
            )
        if self._hangup_task is None:
            self._final = True
            self._invalidate()
            self._hangup_task = asyncio.create_task(self._hangup())
        await _owned_task(self._hangup_task)

    async def _hangup(self):
        await self._stop_locked()
        self._end_call_started = True
        try:
            await self.end_call()
        except Exception:
            self.state = "failed"
            raise SessionLifecycleError(
                "Final room teardown could not be confirmed"
            ) from None
        self.state = "closed"

    async def drain(self):
        if self._background:
            await asyncio.gather(*tuple(self._background))


class PhysicalScreenControls:
    """Bind only to authenticated host button/shortcut events, never SDK tools.

    authorize_event must validate host provenance itself; a client supplied source
    string/quiet boolean is insufficient. The fail-closed default rejects all.
    """

    def __init__(self, holder, *, authorize_event=lambda event: False):
        self.holder = holder
        self.authorize_event = authorize_event
        self._seen = set()

    async def dispatch(self, event):
        if (
            not isinstance(event, LocalControlEvent)
            or not isinstance(event.event_id, str)
            or not 0 < len(event.event_id) <= 128
            or event.event_id in self._seen
            or len(self._seen) >= 256
            or event.action not in {"stop", "resume", "hangup"}
            or not self.authorize_event(event)
            or not 0 <= self.holder.clock() - event.received_at <= 30
        ):
            raise SessionLifecycleError(
                "Authenticated fresh physical control is required"
            )
        self._seen.add(event.event_id)  # consume before any await, including failure
        if event.action == "resume":
            return await self.holder.resume(
                quiet_confirmed=event.quiet_confirmed,
                expires_at=event.received_at + 30,
            )
        if event.action == "stop":
            await self.holder.stop()
            return {"status": "stopped"}
        await self.holder.hangup()
        return {"status": "closed"}


def _require_partial_close_contract(session):
    # Installed SDK's public aclose is a no-op before _started. This narrow
    # compatibility boundary is checked before start and must be revalidated
    # on SDK upgrades; no general SDK private-state replacement is attempted.
    if (
        _sdk_version("livekit-agents") != "1.8.2"
        or not all(
            hasattr(session, name)
            for name in ("_started", "_lock", "_activity", "_room_io", "_session_span")
        )
        or not callable(getattr(session, "_teardown_activity", None))
    ):
        raise SessionLifecycleError("SDK partial-start cleanup contract is unsupported")


async def _close_sdk(session):
    await session.aclose()  # normal initialized SDK path, actual provider drain
    # Partial start does not enter public aclose's teardown. Do not leave RoomIO
    # or an AgentActivity alive on a cancelled/failed initialization.
    if session._activity is not None or session._room_io is not None:
        async with session._lock:
            await session._teardown_activity(
                reason=CloseReason.USER_INITIATED, drain=False
            )
            session.input.audio = None
            session.input.video = None
            session.output.audio = None
            session.output.transcription = None
            if session._room_io is not None:
                await session._room_io.aclose()
                session._room_io = None
            if session._session_span is not None:
                session._session_span.end()
                session._session_span = None
    if (
        session._started
        or session._activity is not None
        or session._room_io is not None
    ):
        raise SessionLifecycleError("SDK did not finish retiring")


def _consume(task):
    if not task.cancelled():
        task.exception()


async def _finish(coro):
    task = asyncio.create_task(coro)
    while not task.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(task)
    return task.result()


async def _owned(coro):
    return await _owned_task(asyncio.create_task(coro))


async def _owned_task(task):
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result
