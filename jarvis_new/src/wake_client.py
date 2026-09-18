"""Always-on hands-free client: say "hey Jarvis" -> talk -> auto-leave.

Idle state costs nothing cloud-side: the laptop mic is monitored
locally by openWakeWord (fully offline, no account, no key) and no
room exists until the keyword fires. On wake the client says
"Yes, Sir." with the local voice ($0), mints a token that dispatches
the worker into a fresh room, streams the mic, and plays the agent
back. When the agent hangs up (60s of silence) the room closes and
we return to listening.

Needs:
- LiveKit URL/key/secret, read from frontend/.env.local (already there).
- AGENT_NAME (default "my-agent") running via the worker.

Wake-word deps (openwakeword needs Python <= 3.11 for tflite wheels,
but we use the ONNX backend) live in a dedicated venv:

    uv venv .venv-wake --python 3.11
    uv pip install --python .venv-wake/bin/python -e . openwakeword sounddevice
    .venv-wake/bin/python src/wake_client.py   # first run downloads models once

(JARVIS_LOCAL=1 not required; this client only joins rooms, it never
touches the laptop itself.)

JARVIS_WAKE_THRESHOLD (default 0.5) tunes sensitivity; lower hears
more, higher false-alarms less.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("jarvis-wake")

MIC_RATE = 48000
BLOCKSIZE = 1536  # 48k samples decimate exactly to a 512-sample 16k frame
OWW_FRAME = 1280  # openWakeWord native frame: 80ms at 16kHz
WAKE_MODEL = "hey_jarvis"
AGENT_JOIN_TIMEOUT = 25.0
IDENTITY = "jarvis-master"


def wake_threshold() -> float:
    """Sensitivity 0..1: explicit > JARVIS_WAKE_THRESHOLD > 0.5 default."""
    try:
        return float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.5"))
    except ValueError:
        return 0.5


def frame_16k_chunks(
    pending: list[int], new_samples: list[int], size: int = OWW_FRAME
) -> tuple[list[list[int]], list[int]]:
    """Buffer 16k samples into full native frames. Pure.

    Returns (complete_frames, remainder). Remainder carries over so no
    audio is dropped between mic blocks.
    """
    buf = [*pending, *new_samples]
    frames = [buf[i : i + size] for i in range(0, len(buf) - len(buf) % size, size)]
    return frames, buf[len(frames) * size :]


def wake_score(prediction: dict, name: str = WAKE_MODEL) -> float:
    """Confidence 0..1 for our model from an openWakeWord prediction. Pure."""
    try:
        return float(prediction.get(name, 0.0))
    except (TypeError, ValueError):
        return 0.0


def room_has_active_call(identities: list[str]) -> bool:
    """True when a room already hosts a call (user + agent). Pure.

    Rooms are unique per session, so 2+ participants means someone is
    already talking to an agent — summoning another would layer a
    second Jarvis voice over the first.
    """
    return len(identities) >= 2


async def active_call_exists(creds: dict[str, str]) -> bool:
    """True when any LiveKit room already hosts a call.

    Fails open (returns False) on API errors: a missed double-voice
    guard is better than going deaf because Cloud hiccuped.
    """
    try:
        from livekit import api

        async with api.LiveKitAPI(
            creds["LIVEKIT_URL"], creds["LIVEKIT_API_KEY"], creds["LIVEKIT_API_SECRET"]
        ) as lk:
            rooms = await lk.room.list_rooms(api.ListRoomsRequest())
            for room in rooms.rooms:
                participants = await lk.room.list_participants(
                    api.ListParticipantsRequest(room=room.name)
                )
                if room_has_active_call(
                    [p.identity for p in participants.participants]
                ):
                    return True
    except Exception as exc:
        logger.warning("call check failed, summoning anyway: %s", exc)
    return False


def load_livekit_env(path: Path | None = None) -> dict[str, str]:
    """Read LiveKit creds from frontend/.env.local. Pure-ish (reads file)."""
    env_path = (
        path or Path(__file__).resolve().parent.parent / "frontend" / ".env.local"
    )
    found: dict[str, str] = {}
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("\"'")
            if key in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
                found[key] = value
    except Exception as exc:
        logger.debug("livekit env read failed: %s", exc)
    return found


def downsample_48k_to_16k(frame_48k) -> list[int]:
    """Decimate one 1536-sample mic block to Porcupine's 512-frame. Pure."""
    return [int(x) for x in frame_48k[::3][:512]]


def summon_room_name(now: float | None = None) -> str:
    """Unique room per summon so stale sessions never collide. Pure."""
    return f"jarvis-{int(now if now is not None else time.time())}"


def mint_summon_token(
    *, url: str, api_key: str, api_secret: str, room: str, agent_name: str
) -> str:
    """JWT that joins `room` and dispatches the worker. Pure (no I/O)."""
    from livekit.api import (
        AccessToken,
        RoomAgentDispatch,
        RoomConfiguration,
        VideoGrants,
    )

    token = AccessToken(api_key, api_secret)
    token.with_identity(IDENTITY).with_name("Sir")
    token.with_grants(VideoGrants(room_join=True, room=room))
    token.with_room_config(
        RoomConfiguration(agents=[RoomAgentDispatch(agent_name=agent_name)])
    )
    return token.to_jwt()


class WakeClient:
    def __init__(self) -> None:
        try:
            from dotenv import load_dotenv

            load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
        except Exception as exc:
            logger.debug("dotenv load failed: %s", exc)
        self._creds = load_livekit_env()
        self._threshold = wake_threshold()
        self._agent_name = (
            os.environ.get("AGENT_NAME", "my-agent").strip() or "my-agent"
        )
        self._ack_pcm: bytes = b""
        self._ack_rate = 22050

    def _check_config(self) -> None:
        missing = [
            k
            for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
            if not self._creds.get(k)
        ]
        if missing:
            raise RuntimeError(f"Missing in frontend/.env.local: {', '.join(missing)}")

    def _render_ack(self) -> None:
        """Pre-render 'Yes, Sir.' with the local voice for instant feedback."""
        try:
            try:
                from src.local_voice import PiperTTS, sentence_split
            except ImportError:
                from local_voice import PiperTTS, sentence_split

            plugin = PiperTTS()
            if not plugin._model_path.exists():
                return
            out = bytearray()
            for sentence in sentence_split("Yes, Sir."):
                out += plugin._render_sentence(sentence)
            self._ack_pcm = bytes(out)
            self._ack_rate = plugin.sample_rate
        except Exception as exc:
            logger.warning("ack render failed: %s", exc)

    async def run_forever(self) -> None:
        self._check_config()
        self._render_ack()
        from openwakeword.model import Model

        # ONNX backend: tflite-runtime wheels don't cover this system's
        # Python, and ONNX scores identically. Models download once on
        # first run (cached under site-packages/openwakeword/resources).
        model = Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")
        logger.warning(
            "listening for 'hey Jarvis' (offline, nothing leaves the laptop)"
        )
        await self._listen_loop(model)

    async def _listen_loop(self, model) -> None:
        import numpy as np
        import sounddevice as sd

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _audio_callback(indata, frames, time_info, status) -> None:
            if status:
                logger.warning("mic status: %s", status)
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

        with sd.RawInputStream(
            samplerate=MIC_RATE,
            channels=1,
            dtype="int16",
            blocksize=BLOCKSIZE,
            callback=_audio_callback,
        ):
            pending: list[int] = []
            while True:
                raw = await queue.get()
                frame_48k = [
                    int.from_bytes(raw[i : i + 2], "little", signed=True)
                    for i in range(0, len(raw), 2)
                ]
                if len(frame_48k) < BLOCKSIZE:
                    continue
                frames, pending = frame_16k_chunks(
                    pending, downsample_48k_to_16k(frame_48k)
                )
                for frame in frames:
                    try:
                        score = wake_score(
                            model.predict(np.array(frame, dtype=np.int16))
                        )
                    except Exception as exc:
                        logger.debug("wake predict failed: %s", exc)
                        continue
                    if score >= self._threshold:
                        logger.warning("wake word detected (%.2f)", score)
                        self._play_ack()
                        await self._summon_session()
                        pending = []

    def _play_ack(self) -> None:
        if not self._ack_pcm:
            return
        try:
            import sounddevice as sd

            with contextlib.suppress(Exception):
                sd.stop()
                sd.wait()
            sd.play(
                self._bytes_to_int16(self._ack_pcm),
                samplerate=self._ack_rate,
                blocking=False,
            )
        except Exception as exc:
            logger.warning("ack playback failed: %s", exc)

    @staticmethod
    def _bytes_to_int16(raw: bytes):
        import numpy as np

        return np.frombuffer(raw, dtype=np.int16)

    @staticmethod
    async def _cancel_task(task: asyncio.Task | None) -> None:
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _summon_session(self) -> None:
        from livekit import rtc

        # Never layer a second Jarvis over an ongoing call (browser
        # session or an earlier summon that hasn't hung up yet).
        if await active_call_exists(self._creds):
            logger.warning("already in a call; staying out")
            return

        room_name = summon_room_name()
        jwt = mint_summon_token(
            url=self._creds["LIVEKIT_URL"],
            api_key=self._creds["LIVEKIT_API_KEY"],
            api_secret=self._creds["LIVEKIT_API_SECRET"],
            room=room_name,
            agent_name=self._agent_name,
        )
        room = rtc.Room()
        disconnected = asyncio.Event()
        agent_audio: asyncio.Queue = asyncio.Queue()

        @room.on("disconnected")
        def _on_disconnected() -> None:
            disconnected.set()

        @room.on("track_subscribed")
        def _on_track(track, _publication, _participant) -> None:
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                agent_audio.put_nowait(track)

        await room.connect(self._creds["LIVEKIT_URL"], jwt)
        logger.warning("joined %s, waiting for %s", room_name, self._agent_name)
        try:
            # Publish the mic.
            source = rtc.AudioSource(MIC_RATE, 1)
            mic_track = rtc.LocalAudioTrack.create_audio_track("jarvis-mic", source)
            await room.local_participant.publish_track(mic_track)
            mic_task = asyncio.create_task(self._pump_mic(source))

            # Wait for the agent, then relay its voice to the speakers.
            try:
                track = await asyncio.wait_for(agent_audio.get(), AGENT_JOIN_TIMEOUT)
            except TimeoutError:
                logger.warning("no agent joined; leaving %s", room_name)
                await self._cancel_task(mic_task)
                return
            play_task = asyncio.create_task(self._play_agent(track))
            await disconnected.wait()
            await self._cancel_task(play_task)
            await self._cancel_task(mic_task)
        finally:
            await room.disconnect()
            logger.warning("session over, back to listening")

    async def _pump_mic(self, source) -> None:
        """Forward live mic blocks to the room (porcupine keeps listening)."""
        import sounddevice as sd
        from livekit import rtc

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _callback(indata, frames, time_info, status) -> None:
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

        with sd.RawInputStream(
            samplerate=MIC_RATE,
            channels=1,
            dtype="int16",
            blocksize=960,
            callback=_callback,
        ):
            while True:
                raw = await queue.get()
                samples = len(raw) // 2
                frame = rtc.AudioFrame(raw, MIC_RATE, 1, samples)
                source.capture_frame(frame)

    async def _play_agent(self, track) -> None:
        """Play the agent's voice on the laptop speakers."""
        import sounddevice as sd
        from livekit.rtc import AudioStream

        stream = AudioStream(track)
        output = None
        try:
            async for event in stream:
                frame = event.frame
                pcm = (
                    frame.data.tobytes()
                    if hasattr(frame.data, "tobytes")
                    else bytes(frame.data)
                )
                if output is None or output.samplerate != frame.sample_rate:
                    if output is not None:
                        output.close()
                    output = sd.OutputStream(
                        samplerate=frame.sample_rate, channels=1, dtype="int16"
                    )
                    output.start()
                output.write(self._bytes_to_int16(pcm))
        finally:
            if output is not None:
                with contextlib.suppress(Exception):
                    output.close()
            with contextlib.suppress(Exception):
                await stream.aclose()


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s")
    asyncio.run(WakeClient().run_forever())


if __name__ == "__main__":
    main()
