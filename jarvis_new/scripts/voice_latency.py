#!/usr/bin/env python3
"""Voice round-trip latency probe against the LIVE agent.

Joins a fresh LiveKit room as a fake caller (dispatching the running
worker), speaks Piper-rendered questions into it, and times

    end of the caller's speech  ->  first audible agent audio frame

which is what the owner feels: it includes local VAD endpointing, the
model's time-to-first-audio and transport. Uses the free Gemini Live
quota only. Run: `uv run python scripts/voice_latency.py [-n ROUNDS]`.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

import numpy as np
from livekit import rtc

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from local_voice import resolve_voice_path  # noqa: E402
from wake_client import load_livekit_env, mint_summon_token  # noqa: E402

QUESTIONS = [
    "Jarvis, what is the capital of Australia?",
    "How many legs does a spider have?",
    "Give me one fun fact about the moon.",
    "What's the boiling point of water in Fahrenheit?",
    "Who wrote Romeo and Juliet?",
]
FRAME_MS = 10
VOICED_RMS = 300.0  # int16 RMS; agent speech sits well above this
QUIET_AFTER_REPLY_S = 1.5
REPLY_TIMEOUT_S = 25.0


def render(text: str) -> tuple[np.ndarray, int]:
    """Piper-render `text` to mono int16 + sample rate."""
    from piper import PiperVoice

    voice = PiperVoice.load(str(resolve_voice_path()))
    chunks = list(voice.synthesize(text))
    audio = np.concatenate([np.frombuffer(c.audio_int16_bytes, np.int16) for c in chunks])
    return audio, chunks[0].sample_rate


class AgentEar:
    """Tracks when the agent's audio was last / first voiced."""

    def __init__(self) -> None:
        self.last_voiced = 0.0
        self.first_voiced_after: dict[float, float] = {}
        self.armed_at: float | None = None
        self.subscribed = asyncio.Event()

    async def listen(self, track: rtc.Track) -> None:
        self.subscribed.set()
        async for ev in rtc.AudioStream(track):
            pcm = np.frombuffer(ev.frame.data, np.int16).astype(np.float32)
            if pcm.size and float(np.sqrt(np.mean(pcm**2))) > VOICED_RMS:
                now = time.perf_counter()
                self.last_voiced = now
                if self.armed_at is not None and self.armed_at not in self.first_voiced_after:
                    self.first_voiced_after[self.armed_at] = now

    async def wait_quiet(self, quiet_s: float, timeout: float) -> None:
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            if time.perf_counter() - self.last_voiced >= quiet_s:
                return
            await asyncio.sleep(0.1)


async def speak(source: rtc.AudioSource, audio: np.ndarray, sr: int) -> None:
    n = sr * FRAME_MS // 1000
    for i in range(0, len(audio), n):
        chunk = audio[i : i + n]
        if len(chunk) < n:
            chunk = np.pad(chunk, (0, n - len(chunk)))
        await source.capture_frame(rtc.AudioFrame(chunk.tobytes(), sr, 1, n))


async def silence_pump(source: rtc.AudioSource, sr: int, stop: asyncio.Event) -> None:
    n = sr * FRAME_MS // 1000
    zeros = np.zeros(n, np.int16).tobytes()
    while not stop.is_set():
        await source.capture_frame(rtc.AudioFrame(zeros, sr, 1, n))


async def main(rounds: int) -> int:
    creds = load_livekit_env()
    room_name = f"latency-probe-{int(time.time())}"
    token = mint_summon_token(
        url=creds["LIVEKIT_URL"],
        api_key=creds["LIVEKIT_API_KEY"],
        api_secret=creds["LIVEKIT_API_SECRET"],
        room=room_name,
        agent_name=os.environ.get("AGENT_NAME", "my-agent"),
    )
    rendered = [render(q) for q in QUESTIONS[:rounds]]
    sr = rendered[0][1]

    ear = AgentEar()
    room = rtc.Room()
    tasks: list[asyncio.Task] = []

    @room.on("track_subscribed")
    def _on_track(track, _pub, _participant) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            tasks.append(asyncio.ensure_future(ear.listen(track)))

    t_join = time.perf_counter()
    await room.connect(creds["LIVEKIT_URL"], token)
    source = rtc.AudioSource(sr, 1)
    track = rtc.LocalAudioTrack.create_audio_track("mic", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    stop_silence = asyncio.Event()
    pump = asyncio.ensure_future(silence_pump(source, sr, stop_silence))
    await asyncio.wait_for(ear.subscribed.wait(), 30)
    print(f"agent audio subscribed after {time.perf_counter() - t_join:.2f}s")
    # Let any greeting finish before the first question.
    await asyncio.sleep(2)
    await ear.wait_quiet(QUIET_AFTER_REPLY_S, 20)

    results: list[float] = []
    for (audio, _), q in zip(rendered, QUESTIONS):
        stop_silence.set()
        await pump
        await speak(source, audio, sr)
        t0 = time.perf_counter()
        ear.armed_at = t0
        stop_silence = asyncio.Event()
        pump = asyncio.ensure_future(silence_pump(source, sr, stop_silence))
        end = t0 + REPLY_TIMEOUT_S
        while t0 not in ear.first_voiced_after and time.perf_counter() < end:
            await asyncio.sleep(0.02)
        got = ear.first_voiced_after.get(t0)
        if got is None:
            print(f"  NO REPLY in {REPLY_TIMEOUT_S:.0f}s  | {q}")
        else:
            results.append(got - t0)
            print(f"  {got - t0:5.2f}s  | {q}")
        ear.armed_at = None
        await asyncio.sleep(1)
        await ear.wait_quiet(QUIET_AFTER_REPLY_S, 30)

    stop_silence.set()
    await room.disconnect()
    for t in tasks:
        t.cancel()
    if results:
        print(
            f"round trip: median {statistics.median(results):.2f}s  "
            f"min {min(results):.2f}s  max {max(results):.2f}s  (n={len(results)})"
        )
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--rounds", type=int, default=len(QUESTIONS))
    ap.add_argument("questions", nargs="*", help="override the question list")
    a = ap.parse_args()
    if a.questions:
        QUESTIONS[:] = a.questions
    sys.exit(asyncio.run(main(min(a.rounds, len(QUESTIONS)))))
