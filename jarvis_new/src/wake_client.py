"""Always-on hands-free client: say "Jarvis" -> talk -> auto-leave.

Idle state costs nothing cloud-side: the laptop mic is monitored
locally by Porcupine (offline) and no room exists until the keyword
fires. On wake the client says "Yes, Sir." with the local voice ($0),
mints a token that dispatches the worker into a fresh room, streams
the mic, and plays the agent back. When the agent hangs up (60s of
silence) the room closes and we return to listening.

Needs:
- PICOVOICE_ACCESS_KEY (free at console.picovoice.ai, no card).
- LiveKit URL/key/secret, read from frontend/.env.local (already there).
- AGENT_NAME (default "my-agent") running via the worker.

Run: .venv/bin/python src/wake_client.py  (JARVIS_LOCAL=1 not required;
this client only joins rooms, it never touches the laptop itself.)
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
PORCUPINE_RATE = 16000
BLOCKSIZE = 1536  # 48k samples decimate exactly to Porcupine's 512-frame
AGENT_JOIN_TIMEOUT = 25.0
IDENTITY = "jarvis-master"


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
    except Exception:
        pass
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
        self._creds = load_livekit_env()
        self._porcupine_key = os.environ.get("PICOVOICE_ACCESS_KEY", "").strip()
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
        if not self._porcupine_key:
            raise RuntimeError(
                "Set PICOVOICE_ACCESS_KEY (free at console.picovoice.ai, no card) "
                "to enable the 'Jarvis' wake word."
            )

    def _render_ack(self) -> None:
        """Pre-render 'Yes, Sir.' with the local voice for instant feedback."""
        try:
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
        import pvporcupine

        porcupine = pvporcupine.create(
            access_key=self._porcupine_key, keywords=["jarvis"]
        )
        logger.warning("listening for 'Jarvis' (offline, nothing leaves the laptop)")
        try:
            await self._listen_loop(porcupine)
        finally:
            porcupine.delete()

    async def _listen_loop(self, porcupine) -> None:
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
            while True:
                raw = await queue.get()
                frame_48k = [
                    int.from_bytes(raw[i : i + 2], "little", signed=True)
                    for i in range(0, len(raw), 2)
                ]
                if len(frame_48k) < BLOCKSIZE:
                    continue
                try:
                    detected = porcupine.process(downsample_48k_to_16k(frame_48k))
                except Exception:
                    continue
                if detected >= 0:
                    logger.warning("wake word detected")
                    self._play_ack()
                    await self._summon_session()

    def _play_ack(self) -> None:
        if not self._ack_pcm:
            return
        try:
            import sounddevice as sd

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

    async def _summon_session(self) -> None:
        from livekit import rtc

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
                mic_task.cancel()
                return
            play_task = asyncio.create_task(self._play_agent(track))
            await disconnected.wait()
            play_task.cancel()
            mic_task.cancel()
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
                with __import__("contextlib").suppress(Exception):
                    output.close()


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s")
    asyncio.run(WakeClient().run_forever())


if __name__ == "__main__":
    main()
