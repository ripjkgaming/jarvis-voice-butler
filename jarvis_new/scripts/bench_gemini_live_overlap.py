"""Isolated Gemini Live conversational interruption probe. Creates one temporary room.

This probe joins the installed (running) production voice hub over the
jarvis_rt WebSocket transport without changing its code or process. It
measures received audio, NOT physical speaker latency. Uses only synthesized
test prompts (Piper); no live microphone or private recordings.

Writes benchmarks/gemini-live-overlap-<today>-jarvis_rt.json, created
exclusively (existing benchmarks are never overwritten).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from voice_latency import Mic, render, resample_16k  # noqa: E402

from jarvis_rt.transport.client import VoiceClient  # noqa: E402
from mint_token import voice_url  # noqa: E402
from wake_client import mint_summon_token  # noqa: E402

FIRST = "Jarvis, please explain for thirty seconds how medieval castles were defended, giving several distinct examples."
INTERRUPT = "Wait Jarvis, stop the castle explanation. Please say violet sapphire seven, then finish."
OUT_RATE = 24000
VOICED_RMS = 300


class Recorder:
    """Agent audio as received by the caller (24 kHz PCM16 mono)."""

    def __init__(self, client: VoiceClient) -> None:
        self.client = client
        self.frames: list[tuple[float, float, np.ndarray, int]] = []
        self.first_voiced: float | None = None
        self.armed = False
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._listen())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def arm(self) -> None:
        self.armed = True

    async def _listen(self) -> None:
        async for pcm in self.client.audio_frames():
            now = time.perf_counter()
            x = np.frombuffer(pcm, dtype=np.int16).copy()
            rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) if len(x) else 0.0
            self.frames.append((now, rms, x, OUT_RATE))
            if rms >= VOICED_RMS and self.armed and self.first_voiced is None:
                self.first_voiced = now

    async def wait_first_voiced(self, timeout: float) -> float | None:
        end = time.perf_counter() + timeout
        while self.first_voiced is None and time.perf_counter() < end:
            await asyncio.sleep(0.05)
        return self.first_voiced


def gap_after(frames, start, end):
    values = [(t, level) for t, level, _, _ in frames if start <= t <= end]
    first = None
    for index, (t, _) in enumerate(values):
        if t <= start:
            continue
        quiet = [v for at, v in values[index:] if at < t + 0.25]
        if len(quiet) >= 10 and max(quiet) < VOICED_RMS:
            first = (t - start) * 1000
            break
    return None if first is None else round(first, 1)


def transcript(frames, start, end):
    blocks = [(x, sr) for t, _, x, sr in frames if start <= t <= end]
    if not blocks:
        return ""
    sample_rate = blocks[0][1]
    x = np.concatenate([data for data, sr in blocks if sr == sample_rate])
    if len(x) < sample_rate:
        return ""
    from faster_whisper import WhisperModel

    model = WhisperModel(
        "base.en", device="cpu", compute_type="int8", local_files_only=True
    )
    input_t = np.arange(len(x)) / sample_rate
    out_t = np.arange(round(len(x) * 16000 / sample_rate)) / 16000
    normalized = np.interp(out_t, input_t, x).astype(np.float32) / 32768.0
    parts, _ = model.transcribe(
        normalized, beam_size=1, language="en", vad_filter=False
    )
    return " ".join(part.text.strip() for part in parts)


def output_path() -> Path:
    return (
        ROOT
        / "benchmarks"
        / f"gemini-live-overlap-{date.today().isoformat()}-jarvis_rt.json"
    )


async def main() -> None:
    room_name = f"barge-probe-{int(time.time())}"
    token = mint_summon_token(room=room_name, reason="bench")
    first16 = resample_16k(*render(FIRST))
    second16 = resample_16k(*render(INTERRUPT))
    client = VoiceClient(voice_url(), token)
    recorder = Recorder(client)
    mic = Mic(client)
    client.start()
    try:
        await client.wait_connected(35)
        recorder.start()
        mic.start()
        # Give the greeting time to finish, if the agent has one.
        await asyncio.sleep(8)
        recorder.arm()
        await mic.speak(first16)
        if await recorder.wait_first_voiced(30) is None:
            raise TimeoutError("Gemini did not start its first explanation")
        await asyncio.sleep(0.7)
        onset, speech_end = await mic.speak(second16)
        await asyncio.sleep(10)
        end = time.perf_counter()
        frames = recorder.frames
        voiced = [
            (t, level)
            for t, level, _, _ in frames
            if onset <= t <= end and level >= VOICED_RMS
        ]
        metrics = {
            "kind": "real_Gemini_Live_overlapping_speech_with_running_jarvis_rt_hub",
            "source": "synthesized_Piper_audio_over_jarvis_rt_websocket",
            "real_microphone_test": False,
            "real_speaker_test": False,
            "room_name_redacted": True,
            "first_reply_start_before_interruption_s": round(
                onset - recorder.first_voiced, 2
            ),
            "interruption_audio_duration_s": round(speech_end - onset, 2),
            "last_received_voiced_frame_after_onset_ms": round(
                (voiced[-1][0] - onset) * 1000, 1
            )
            if voiced
            else None,
            "first_250ms_received_silence_gap_after_onset_ms": gap_after(
                frames, onset, end
            ),
            "voiced_frames_after_interruption_start": len(voiced),
            "total_received_frames": len(frames),
            "reply_transcript_after_overlap": transcript(frames, speech_end, end),
            "limitations": "No clean generation ID in the received audio stream. The first silence gap is not proof of Gemini cancellation, and received audio latency differs from hardware playout.",
        }
        path = output_path()
        with path.open("x") as fh:
            fh.write(json.dumps(metrics, indent=2) + "\n")
        print(json.dumps(metrics, indent=2), flush=True)
    finally:
        await mic.stop()
        await recorder.stop()
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
