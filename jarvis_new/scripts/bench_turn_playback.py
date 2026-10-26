"""Matched, interleaved old/new speaker benchmark; no device or model calls.

Export the original module with git show <base>:jarvis_new/src/audio_playback.py
and pass that file as --baseline. Both implementations see the same paced
fake sink. This measures local playback overhead, not end-to-end voice latency.
"""

import argparse
import asyncio
import importlib.util
import json
import math
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from audio_playback import AudioPlayback


async def trial(cls):
    first = []
    written = bytearray()
    frames = [np.full(240, i + 600, np.int16) for i in range(20)]

    class Output:
        def __init__(self, **kwargs):
            self.samplerate = kwargs["samplerate"]

        def start(self):
            time.sleep(0.03)

        def write(self, samples):
            if not first:
                first.append(time.perf_counter())
            time.sleep(0.01)
            written.extend(samples.tobytes())

        def close(self):
            pass

    with patch.dict(sys.modules, sounddevice=SimpleNamespace(OutputStream=Output)):
        speaker = cls()
        start = time.perf_counter()
        try:
            for frame in frames:
                await speaker.write(frame, 24000)
        finally:
            await speaker.aclose()
        end = time.perf_counter()
    assert written == b"".join(frame.tobytes() for frame in frames)
    return {
        "first_write_ms": (first[0] - start) * 1000,
        "total_ms": (end - start) * 1000,
    }


def summarize(rows):
    return {
        key: {
            "p50_ms": statistics.median(row[key] for row in rows),
            "p95_ms": sorted(row[key] for row in rows)[math.ceil(len(rows) * 0.95) - 1],
        }
        for key in rows[0]
    }


async def main(args):
    spec = importlib.util.spec_from_file_location("baseline_playback", args.baseline)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    rows = {"before": [], "after": []}
    # Warm imports/executor startup identically; exclude these two trials.
    await trial(baseline.AudioPlayback)
    await trial(AudioPlayback)
    for i in range(args.rounds):
        pair = [("before", baseline.AudioPlayback), ("after", AudioPlayback)]
        for name, cls in pair[:: 1 if i % 2 == 0 else -1]:
            rows[name].append(await trial(cls))
    report = {
        "scope": "offline interleaved fake sink, 30ms open + 20x10ms writes; no model/network/audio hardware",
        "rounds_per_version": args.rounds,
        "before": summarize(rows["before"]),
        "after": summarize(rows["after"]),
        "rows": rows,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=20)
    with tempfile.TemporaryDirectory(prefix="jarvis-playback-bench-") as scratch:
        os.environ["JARVIS_SPEAKER_LOCK"] = str(Path(scratch) / "speaker.lock")
        asyncio.run(main(parser.parse_args()))
