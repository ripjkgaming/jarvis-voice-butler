"""Registered, model-free screen call host; native controls own SDK generations."""

from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass
from pathlib import Path

from feature_sessions import FeatureSession, load_host_consent
from jarvis_rt import Agent, AgentSession, StopResponse
from jarvis_rt.io import TextOutput
from jarvis_rt.room_io import AudioInputOptions, RoomOptions, TextOutputOptions
from screen_control_socket import NativeControlServer, NativePeerVerifier
from screen_session_holder import ScreenSessionHolder, _owned_task
from screen_understanding import (
    ForegroundBackend,
    explicit_screen_request,
    local_owner_authorized,
)


@dataclass(frozen=True)
class ScreenHostConfig:
    socket_path: str
    native_executable: str
    consent_path: str | None = None

    @classmethod
    def from_env(cls):
        socket_path = os.environ.get("JARVIS_SCREEN_CONTROL_SOCKET", "")
        native_executable = os.environ.get("JARVIS_SCREEN_CONTROL_EXE", "")
        if not socket_path or not native_executable:
            raise ValueError(
                "Explicit screen control socket and native executable are required"
            )
        for path in (socket_path, native_executable):
            if not Path(path).is_absolute():
                raise ValueError("Screen host paths must be absolute")
        return cls(
            socket_path,
            native_executable,
            os.environ.get("JARVIS_FEATURE_CONSENT_FILE"),
        )


def local_screen_session():
    # Called only by authenticated physical Resume. All inference is local;
    # no text/realtime model, tools, camera, launch router or proactive watcher.
    from jarvis_rt.vad import SileroVAD
    from local_voice import PiperTTS
    from screen_local_stt import DrainingScreenSTT

    return AgentSession(
        llm=None,
        stt=DrainingScreenSTT(),
        tts=PiperTTS(strict_sentence_audio=True),
        vad=SileroVAD.load(),
        turn_detection="vad",
        user_away_timeout=None,
    )


def public_caption(role, text):
    from hud_events import caption

    caption(role, text)


class ScreenCaptionTail(TextOutput):
    def __init__(self, features, publish):
        super().__init__(label="screen-host-private-caption-tail", next_in_chain=None)
        self.features = features
        self.publish = publish
        self._text = ""

    async def capture_text(self, text):
        if self.features.captions_allowed:
            self._text += text

    def flush(self):
        if self.features.captions_allowed and self._text:
            self.publish("assistant", self._text)
        self.clear_sensitive()

    def clear_sensitive(self):
        self._text = ""


class ScreenAgent(Agent):
    def __init__(self, prepare_request):
        super().__init__(
            instructions="Handle explicit selected-window screen requests only.",
            tools=[],
        )
        self.prepare_request = prepare_request

    async def on_user_turn_completed(self, context, message):
        features = getattr(self.session, "jarvis_features", None)
        if features is not None:
            self.prepare_request(self.session, message)
            await features.handle_message(message)
        raise StopResponse()  # never infer a normal model continuation


class ScreenHost:
    def __init__(
        self,
        ctx,
        config,
        *,
        session_factory=local_screen_session,
        backend_factory=ForegroundBackend,
        owner=local_owner_authorized,
        consent_loader=load_host_consent,
        caption=public_caption,
        server_factory=NativeControlServer,
        verifier_factory=NativePeerVerifier,
    ):
        self.ctx = ctx
        self.config = config
        self.owner = owner
        self.consent_loader = consent_loader
        self._caption = caption
        self._backend_factory = backend_factory
        self._closed = False
        self._close_task = None
        self._watcher = None
        self.holder = ScreenSessionHolder(
            room=ctx.room,
            session_factory=session_factory,
            agent_factory=lambda: ScreenAgent(self._prepare_screen_request),
            bind=self._bind_generation,
            room_options_factory=self._room_options,
            owner_allowed=owner,
            end_call=self._end_call,
        )
        self.controls = server_factory(
            config.socket_path,
            self.holder,
            verifier_factory(config.native_executable),
        )

    def _bind_generation(self, session, events):
        from echo_guard import EchoGuard

        if session.backend is not None or session.options.get("llm") is not None:
            raise ValueError("Screen host permits no live conversational model")
        features = FeatureSession(
            session,
            backend=self._backend_factory(),
            authorized=self.owner,
        )
        features.install(realtime=False)  # authoritative SDK ChatMessage owns turn
        tail = ScreenCaptionTail(features, self._caption)
        features.bind_caption_tail(tail)
        session.jarvis_caption_tail = tail
        echo = EchoGuard()
        features.is_echo = echo.is_echo

        def transcript(event):
            if features.captions_allowed and getattr(event, "is_final", False):
                self._caption("user", getattr(event, "transcript", ""))

        def conversation(event):
            item = getattr(event, "item", None)
            if features.captions_allowed and getattr(item, "role", None) == "assistant":
                text = getattr(item, "text_content", "") or ""
                echo.note_agent(text)

        events.on("user_input_transcribed", transcript)
        events.on("conversation_item_added", conversation)
        return features

    @staticmethod
    def _room_options(session):
        return RoomOptions(
            audio_input=AudioInputOptions(),
            video_input=False,
            text_input=False,
            text_output=TextOutputOptions(next_in_chain=session.jarvis_caption_tail),
            delete_room_on_close=False,
        )

    def _prepare_screen_request(self, session, message):
        # ONLY a fresh trusted SDK user message with positive screen grammar
        # may consult the independently approved host consent file. Native
        # Resume never reads it or grants capture consent.
        features = session.jarvis_features
        turn_id = getattr(message, "id", None)
        text = getattr(message, "text_content", "") or ""
        if (
            session is not self.holder.session
            or not self.owner()
            or features.input_quarantined
            or not features.authorized()
            or getattr(message, "role", None) != "user"
            or not turn_id
            or turn_id in features._seen
            or features.is_echo(text)
            or not explicit_screen_request(text)
        ):
            return
        try:
            consent = self.consent_loader(self.config.consent_path)
            if consent.expires_at:
                features.configure(consent, confirmed=True)
        except (OSError, ValueError, TypeError):
            features.revoke_authority()

    async def _end_call(self):
        # Final hangup closes the hub room (code 4011: room closed).
        await self.ctx.room.close()

    async def start(self):
        if not self.owner():
            raise ValueError(
                "Screen host requires the local owner; private/guest refused"
            )
        try:
            await (
                self.controls.start()
            )  # bind controls before connecting; initially no SDK input
            self.ctx.add_shutdown_callback(self.close)
            self.ctx.jarvis_screen_host = self
            await self.ctx.connect()
            self._watcher = asyncio.create_task(self._watch_authority())
        except BaseException:
            await self.close()
            raise
        return self

    async def _watch_authority(self):
        while not self._closed:
            await asyncio.sleep(0.2)
            if not self.owner() and self.holder.session is not None:
                await self.holder.revoke_authority()

    async def close(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._shutdown())
        await _owned_task(self._close_task)

    async def _shutdown(self):
        self._closed = True
        if self._watcher is not None:
            self._watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher
        await self.controls.aclose()
        await self.holder.hangup()


async def start_screen_host(ctx, **kwargs):
    """Entrypoint for explicit JARVIS_SCREEN_HOST=1 staged worker only."""
    config = kwargs.pop("config", None) or ScreenHostConfig.from_env()
    host = ScreenHost(ctx, config, **kwargs)
    return await host.start()
