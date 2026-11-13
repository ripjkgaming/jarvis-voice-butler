"""Glue between a hub room and an AgentSession (mic in, audio/captions out)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

from .audio import AutoGain
from .io import TextOutput, TranscriptSynchronizer
from .markup import strip_all_markup

log = logging.getLogger("jarvis_rt.room_io")

CAPTION_TOPIC = "caption"
STATE_TOPIC = "state"


@dataclass
class TextOutputOptions:
    next_in_chain: TextOutput | None = None
    sync_transcription: bool = True


@dataclass
class AudioInputOptions:
    #: Observer with ``process(pcm) -> pcm`` (pass-through meters welcome).
    noise_cancellation: Any = None
    auto_gain_control: bool = False


@dataclass
class RoomOptions:
    text_output: TextOutputOptions = field(default_factory=TextOutputOptions)
    audio_input: AudioInputOptions = field(default_factory=AudioInputOptions)
    video_input: bool = False
    #: Typed ``lk.chat`` text becomes user turns (screen sessions turn it off).
    text_input: bool = True
    #: Only audio from this identity reaches the session (private phone calls).
    participant_identity: str | None = None
    close_on_disconnect: bool = True
    delete_room_on_close: bool = True


class _RoomCaptions(TextOutput):
    """First link: send paced, markup-free text to the room, then the chain."""

    def __init__(self, room: Any, next_in_chain: TextOutput | None) -> None:
        super().__init__(label="RoomCaptions", next_in_chain=next_in_chain)
        self._room = room
        self._text = ""

    async def capture_text(self, text: str) -> None:
        self._text += text
        clean = strip_all_markup(self._text)
        with contextlib.suppress(Exception):
            self._room.send_text(CAPTION_TOPIC, {"text": clean, "final": False})
        if self.next_in_chain is not None:
            await self.next_in_chain.capture_text(text)

    def flush(self) -> None:
        if self._text:
            with contextlib.suppress(Exception):
                self._room.send_text(
                    CAPTION_TOPIC, {"text": strip_all_markup(self._text), "final": True}
                )
        self._text = ""
        if self.next_in_chain is not None:
            self.next_in_chain.flush()


class _RoomVideo:
    """Phone camera/screen frames received on the call (``(jpeg, ts_ms)``)."""

    def __init__(self, room: Any) -> None:
        self._room = room

    async def frames(self):
        video = self._room.video
        while True:
            while video:
                ts_ms, jpeg = video.popleft()
                yield jpeg, ts_ms
            await asyncio.sleep(0.05)


class RoomIO:
    def __init__(
        self, session: Any, room: Any, options: RoomOptions | None = None
    ) -> None:
        self.session = session
        self.room = room
        self.options = options or RoomOptions()
        self._agc = AutoGain() if self.options.audio_input.auto_gain_control else None
        self._observer = self.options.audio_input.noise_cancellation
        self._captions = _RoomCaptions(room, self.options.text_output.next_in_chain)
        self._sync = TranscriptSynchronizer(self._captions)
        self._tasks: list[asyncio.Task] = []
        self._closed = False

    async def start(self) -> None:
        s = self.session
        s.output.audio = self._on_audio
        s.output.transcription = self._on_text
        s.output.flush = self._on_flush
        if self.options.text_input:
            self.room.on_chat(self._on_chat)
        s.on("agent_state_changed", self._publish_state)
        s.on("user_state_changed", self._publish_state)
        if (
            self.options.video_input
            and getattr(self.room, "video", None) is not None
            and s.input.video is None
        ):
            s.input.video = _RoomVideo(self.room)
        self._tasks.append(asyncio.get_running_loop().create_task(self._pump_mic()))
        if self.options.close_on_disconnect:
            self.room.on("disconnected", self._on_disconnected)

    # ---- inbound -------------------------------------------------------
    async def _pump_mic(self) -> None:
        async for pcm in self.room.mic_frames():
            pinned = self.options.participant_identity
            speaker = getattr(self.room, "speaker", None)
            if pinned and (speaker is None or speaker.identity != pinned):
                continue
            if self._observer is not None:
                with contextlib.suppress(Exception):
                    pcm = self._observer.process(pcm) or pcm
            if self._agc is not None:
                pcm = self._agc.process(pcm)
            self.session.push_audio(pcm)

    def _on_chat(self, text: str) -> None:
        # typed text interrupts whatever is playing and becomes the next user turn
        self.session.interrupt()
        self.session.generate_reply(user_input=text)

    def _on_disconnected(self) -> None:
        from .events import CloseReason

        asyncio.get_running_loop().create_task(
            self.session.aclose(CloseReason.PARTICIPANT_DISCONNECTED)
        )

    # ---- outbound ------------------------------------------------------
    def _on_audio(self, pcm24k: bytes) -> None:
        self._sync.push_audio(pcm24k)
        self.room.send_audio(pcm24k)

    def _on_text(self, text: str, final: bool) -> None:
        if final:
            asyncio.get_running_loop().create_task(self._sync.flush())
        elif text:
            self._sync.push_text(text)

    def request_fade(self, ms: int) -> None:
        """Make the next flush ramp the speaker down over ``ms`` (one-shot)."""
        self._fade_ms = max(0, min(int(ms), 1000))

    def _on_flush(self) -> None:
        fade_ms, self._fade_ms = getattr(self, "_fade_ms", 0), 0
        self.room.flush_playback(fade_ms)
        self._sync.reset()

    def _publish_state(self, _event: Any) -> None:
        with contextlib.suppress(Exception):
            self.room.send_text(
                STATE_TOPIC,
                {"agent": self.session.agent_state, "user": self.session.user_state},
            )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        for t in self._tasks:
            t.cancel()
        with contextlib.suppress(Exception):
            await self._sync.flush()
        if self.options.delete_room_on_close:
            with contextlib.suppress(Exception):
                await self.room.close()
