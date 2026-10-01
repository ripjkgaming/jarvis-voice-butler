#!/usr/bin/env python3
"""Offline runtime probe: no mic, speakers, desktop, server or model APIs.

Playback uses a controlled blocking speaker and buffered 10ms frames to
measure event-loop starvation, NOT real hardware or voice round-trip time.
Optional --stt tests cached Whisper base with unchanged recognition settings.
Cold STT means a fresh plugin/model instance; only the first run has cold imports.
Run before/after separately, serializing with other CPU-heavy work.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def distribution(values):
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "p50_ms": statistics.median(ordered) * 1000,
        "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1] * 1000,
    }


async def measured(operation):
    gaps = []
    stop = False

    async def heartbeat():
        previous = time.perf_counter()
        while not stop:
            await asyncio.sleep(0.002)
            now = time.perf_counter()
            gaps.append(max(0, now - previous - 0.002))
            previous = now

    task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    started = time.perf_counter()
    try:
        result = await operation()
        duration = time.perf_counter() - started
        await asyncio.sleep(0.003)
        return result, duration, max(gaps, default=0)
    finally:
        stop = True
        await task


async def playback_trial(frames, expected):
    from livekit import rtc

    from wake_client import WakeClient

    written = bytearray()
    first_at = []
    write_gaps = []

    class Output:
        def __init__(self, **kwargs):
            self.samplerate = kwargs["samplerate"]

        def start(self):
            time.sleep(0.03)

        def write(self, samples):
            now = time.perf_counter()
            if not first_at:
                first_at.append(now)
            else:
                write_gaps.append(now - last_at[0])
            last_at[:] = [now]
            time.sleep(0.01)
            written.extend(samples.tobytes())

        def close(self):
            pass

    class Stream:
        def __init__(self, track):
            pass

        async def __aiter__(self):
            for frame in frames:
                yield SimpleNamespace(frame=frame)

        async def aclose(self):
            pass

    client = WakeClient.__new__(WakeClient)  # no config or live devices
    last_at = []
    with (
        patch.dict(sys.modules, sounddevice=SimpleNamespace(OutputStream=Output)),
        patch.object(rtc, "AudioStream", Stream),
        patch("wake_client._school_quiet", return_value=False),
    ):
        started = time.perf_counter()
        _, wall, lag = await measured(lambda: client._play_agent(None))
    assert bytes(written) == expected, "PCM bytes/order changed"
    return {
        "cold_first_write_s": first_at[0] - started,
        "warm_write_interval_s": statistics.median(write_gaps),
        "wall_s": wall,
        "max_event_loop_lag_s": lag,
    }


async def playback(rounds):
    from livekit import rtc

    frames = [
        rtc.AudioFrame(np.full(240, n + 600, np.int16).tobytes(), 24000, 1, 240)
        for n in range(20)
    ]
    expected = b"".join(bytes(frame.data) for frame in frames)
    rows = [await playback_trial(frames, expected) for _ in range(rounds)]
    return {
        "kind": "controlled fake speaker; 30ms open + 20 x 10ms writes; buffered frames",
        "pcm_sha256": hashlib.sha256(expected).hexdigest(),
        "rows": rows,
        "summary": {key: distribution([r[key] for r in rows]) for key in rows[0]},
    }


async def stt_probe(cold_rounds):
    os.environ["HF_HUB_OFFLINE"] = "1"
    sys.path.insert(0, str(ROOT / "tests"))
    import wave

    from livekit import rtc
    from test_voice_fixtures import FIXTURES, fixture_slug, wer
    from test_voice_regressions import CACHE_DIR, EARS_IDX, expand_numbers

    from local_stt import FasterWhisperSTT

    fixtures = []
    for idx in EARS_IDX:
        fix = FIXTURES[idx]
        # Only use existing WAVs; never synthesize or contact any service.
        with wave.open(
            str(CACHE_DIR / fixture_slug(fix["text"], fix["kind"])), "rb"
        ) as wav:
            assert wav.getframerate() == 16000 and wav.getnchannels() == 1
            pcm = wav.readframes(wav.getnframes())
        fixtures.append((rtc.AudioFrame(pcm, 16000, 1, len(pcm) // 2), fix["text"]))

    async def run_one(plugin, idx):
        frame, reference = fixtures[idx]
        event, wall, lag = await measured(lambda: plugin.recognize([frame]))
        hyp = event.alternatives[0].text
        return {
            "fixture": idx,
            "wall_s": wall,
            "max_event_loop_lag_s": lag,
            "wer": wer(expand_numbers(reference), expand_numbers(hyp)),
            "transcript_sha256": hashlib.sha256(hyp.encode()).hexdigest(),
        }

    cold = []
    for _ in range(cold_rounds):
        plugin = FasterWhisperSTT(model_size="base", device="cpu", compute_type="int8")
        cold.append(await run_one(plugin, 1))
        await plugin.aclose()
        del plugin
    plugin = FasterWhisperSTT(model_size="base", device="cpu", compute_type="int8")
    await run_one(plugin, 1)
    warm = [
        await run_one(plugin, idx) for _ in range(2) for idx in range(len(fixtures))
    ]
    await plugin.aclose()
    return {
        "configuration": "base/cpu/int8/beam_size=1/vad_filter=False",
        "cold_kind": "new model instance (OS cache warm); first sample also includes cold Python imports",
        "cold": cold,
        "warm": warm,
        "summary": {
            kind: {
                key: distribution([r[key] for r in rows])
                for key in ("wall_s", "max_event_loop_lag_s")
            }
            for kind, rows in [("cold", cold), ("warm", warm)]
        },
        "mean_warm_wer": statistics.mean(r["wer"] for r in warm),
    }


async def main(args):
    report = {"label": args.label, "playback": await playback(args.rounds)}
    if args.stt:
        report["stt"] = await stt_probe(args.cold_rounds)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: value.get("summary", value) if isinstance(value, dict) else value
                for key, value in report.items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--cold-rounds", type=int, default=5)
    parser.add_argument("--stt", action="store_true")
    asyncio.run(main(parser.parse_args()))
