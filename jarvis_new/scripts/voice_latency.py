#!/usr/bin/env python3
"""Voice round-trip latency probe against the LIVE agent.

Joins a fresh LiveKit room as a fake caller (dispatching the running
worker), speaks Piper-rendered questions into it, and times

    sender utterance playout end -> first received voiced agent frame

The sender boundary includes rendered trailing silence and frame padding;
it is not an annotated acoustic speech-end boundary. This does not measure
the user's wake detector, physical microphone, or speaker buffering, and
does not establish answer correctness. The sender boundary uses the RTC
queue's playout estimate, not remote receipt. Negative values denote overlap.
This makes LIVE model calls and may incur usage/cost:
`uv run python scripts/voice_latency.py --live [-n ROUNDS]`.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import numpy as np
from livekit import rtc

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from local_voice import resolve_voice_path
from wake_client import load_livekit_env, mint_summon_token

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
STARTUP_TIMEOUT_S = 30.0
QUIET_TIMEOUT_S = 30.0


def render(text: str) -> tuple[np.ndarray, int]:
    """Piper-render `text` to mono int16 + sample rate."""
    from piper import PiperVoice

    voice = PiperVoice.load(str(resolve_voice_path()))
    chunks = list(voice.synthesize(text))
    audio = np.concatenate(
        [np.frombuffer(c.audio_int16_bytes, np.int16) for c in chunks]
    )
    return audio, chunks[0].sample_rate


class AgentEar:
    """Observe audio before sending so replies during sender drain are retained."""

    def __init__(self) -> None:
        self.last_voiced: float | None = None
        self.first_voiced: float | None = None
        self.armed = False
        self.agent_state: str | None = None
        self.error: Exception | None = None
        self.voice_received = asyncio.Event()
        self.subscribed = asyncio.Event()

    def arm(self) -> None:
        self.first_voiced = None
        self.voice_received.clear()
        self.armed = True

    def disarm(self) -> None:
        self.armed = False

    def record_voice(self, now: float) -> None:
        self.last_voiced = now
        if self.armed and self.first_voiced is None:
            self.first_voiced = now
            self.voice_received.set()

    async def listen(self, track: rtc.Track) -> None:
        stream = rtc.AudioStream(track)
        try:
            self.subscribed.set()
            async for ev in stream:
                pcm = np.frombuffer(ev.frame.data, np.int16).astype(np.float32)
                if pcm.size and float(np.sqrt(np.mean(pcm**2))) > VOICED_RMS:
                    self.record_voice(time.perf_counter())
            raise RuntimeError("agent audio stream ended during probe")
        except Exception as exc:
            self.error = exc
            self.voice_received.set()  # wake a pending first-frame wait
            raise
        finally:
            await stream.aclose()

    async def wait_first(self, timeout: float) -> float | None:
        if self.error is not None:
            raise self.error
        try:
            await asyncio.wait_for(self.voice_received.wait(), timeout)
        except asyncio.TimeoutError:
            return None
        if self.error is not None:
            raise self.error
        return self.first_voiced

    async def wait_quiet(self, quiet_s: float, timeout: float) -> None:
        started = time.perf_counter()
        end = started + timeout
        while time.perf_counter() < end:
            if self.error is not None:
                raise self.error
            # Observe a fresh quiet interval. When available, agent state also
            # prevents an inter-sentence/tool pause from becoming a new question.
            last = max(started, self.last_voiced or started)
            busy = self.agent_state in {"initializing", "thinking", "speaking"}
            if not busy and time.perf_counter() - last >= quiet_s:
                return
            await asyncio.sleep(0.1)
        raise TimeoutError("agent did not become quiet; probe stopped")


async def speak(source: rtc.AudioSource, audio: np.ndarray, sr: int) -> float:
    n = sr * FRAME_MS // 1000
    for i in range(0, len(audio), n):
        chunk = audio[i : i + n]
        if len(chunk) < n:
            chunk = np.pad(chunk, (0, n - len(chunk)))
        await source.capture_frame(rtc.AudioFrame(chunk.tobytes(), sr, 1, n))
    # capture_frame only enqueues; up to 1s can still be pending. Do not
    # start the silence pump until this wait completes (it extends the queue).
    await source.wait_for_playout()
    return time.perf_counter()


async def silence_pump(source: rtc.AudioSource, sr: int, stop: asyncio.Event) -> None:
    n = sr * FRAME_MS // 1000
    zeros = np.zeros(n, np.int16).tobytes()
    while not stop.is_set():
        await source.capture_frame(rtc.AudioFrame(zeros, sr, 1, n))


async def stop_pump(
    pump: asyncio.Task | None, *, propagate_failure: bool = True
) -> None:
    if pump is not None:
        pump.cancel()
        [result] = await asyncio.gather(pump, return_exceptions=True)
        if propagate_failure and isinstance(result, Exception):
            raise result


async def measure_reply(
    source: rtc.AudioSource, audio: np.ndarray, sr: int, ear: AgentEar
) -> float | None:
    """No concurrent sender is allowed; preserve the full utterance and reply."""
    await source.wait_for_playout()  # finish silence from the previous phase
    ear.arm()
    pump = None
    try:
        speech_end = await speak(source, audio, sr)
        pump = asyncio.create_task(
            silence_pump(source, sr, asyncio.Event()), name="probe-silence"
        )
        got = await ear.wait_first(REPLY_TIMEOUT_S)
        if got is None:
            # The caller must stop here: a late reply could contaminate the
            # next question. Never silently count a timeout as a fast response.
            return None
        await ear.wait_quiet(QUIET_AFTER_REPLY_S, QUIET_TIMEOUT_S)
        return got - speech_end
    finally:
        ear.disarm()
        await stop_pump(pump)


def print_summary(results: list[float | None], *, after_greeting: bool = True) -> None:
    """One room's first question is not an independently cold model sample."""
    startup = "after greeting" if after_greeting else "startup speech disabled"
    for label, samples in (
        (f"first exchange (cold-session proxy, {startup})", results[:1]),
        ("warm follow-ups (same room)", results[1:]),
    ):
        values = [value for value in samples if value is not None]
        missing = len(samples) - len(values)
        if values:
            p50, p95 = np.percentile(values, [50, 95])
            print(
                f"{label}: p50={p50:.2f}s p95={p95:.2f}s n={len(values)} missing={missing}"
            )
        else:
            print(f"{label}: no samples; missing={missing}")
    print(
        "Signed seconds; empirical interpolated percentiles, not independent cold runs."
    )


async def main(rounds: int, *, live: bool = False, no_greeting: bool = False) -> int:
    if not live:
        print("Live probe disabled: pass --live to allow model usage.")
        return 2
    if not 1 <= rounds <= len(QUESTIONS):
        raise ValueError("rounds must be between 1 and the number of questions")
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
    if any(rate != sr for _, rate in rendered):
        raise ValueError("rendered sample rates must match")

    ear = AgentEar()
    room = rtc.Room()
    tasks: list[asyncio.Task] = []
    source = None
    pump = None
    agent_identity: str | None = None

    @room.on("track_subscribed")
    def _on_track(track, _pub, participant) -> None:
        nonlocal agent_identity
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            agent_identity = participant.identity
            ear.agent_state = participant.attributes.get("lk.agent.state")
            tasks.append(asyncio.create_task(ear.listen(track)))

    @room.on("participant_attributes_changed")
    def _on_attributes(changed, participant) -> None:
        if participant.identity == agent_identity and "lk.agent.state" in changed:
            ear.agent_state = changed["lk.agent.state"] or None

    t_join = time.perf_counter()
    ear.arm()  # greeting may arrive before track publication completes
    try:
        await room.connect(creds["LIVEKIT_URL"], token)
        source = rtc.AudioSource(sr, 1)
        track = rtc.LocalAudioTrack.create_audio_track("mic", source)
        await room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        pump = asyncio.create_task(
            silence_pump(source, sr, asyncio.Event()), name="probe-silence"
        )
        await asyncio.wait_for(ear.subscribed.wait(), STARTUP_TIMEOUT_S)
        print(f"agent audio subscribed after {time.perf_counter() - t_join:.2f}s")
        if not no_greeting and await ear.wait_first(STARTUP_TIMEOUT_S) is None:
            raise TimeoutError("no startup greeting observed; probe stopped")
        await ear.wait_quiet(QUIET_AFTER_REPLY_S, QUIET_TIMEOUT_S)
        ear.disarm()
        await stop_pump(pump)
        pump = None

        print("Metric: sender utterance playout end -> first received voiced frame.")
        print(
            "Wake/microphone/speaker latency and answer correctness are not measured."
        )
        results: list[float | None] = []
        for index, (audio, _) in enumerate(rendered, 1):
            latency = await measure_reply(source, audio, sr, ear)
            results.append(latency)
            if latency is None:
                print(f"question {index}: NO REPLY in {REPLY_TIMEOUT_S:.0f}s; stopping")
                break
            overlap = " (overlap)" if latency < 0 else ""
            print(f"question {index}: {latency:+.2f}s{overlap}")
        print_summary(results, after_greeting=not no_greeting)
        return 0 if len(results) == rounds and None not in results else 1
    finally:
        await stop_pump(pump, propagate_failure=False)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            if source is not None:
                try:
                    source.clear_queue()
                finally:
                    await source.aclose()
        finally:
            await room.disconnect()


def cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--live", action="store_true", help="allow live model calls and usage/cost"
    )
    ap.add_argument(
        "--no-greeting",
        action="store_true",
        help="only for agents configured without startup speech",
    )
    ap.add_argument("-n", "--rounds", type=int, default=len(QUESTIONS))
    ap.add_argument("questions", nargs="*", help="override the question list")
    a = ap.parse_args(argv)
    if not a.live:
        ap.error("--live is required: this probe makes real model calls")
    if a.rounds < 1:
        ap.error("--rounds must be positive")
    if a.questions:
        QUESTIONS[:] = a.questions
    try:
        return asyncio.run(
            main(min(a.rounds, len(QUESTIONS)), live=True, no_greeting=a.no_greeting)
        )
    except Exception as exc:
        # Do not expose credentials, model responses, or question text from
        # arbitrary SDK/Piper exception messages.
        print(
            f"probe failed ({type(exc).__name__}); no complete measurement",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(cli())
