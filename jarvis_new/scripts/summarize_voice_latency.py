#!/usr/bin/env python3
"""Read content-free timing fields from actions.log without making live calls."""

import argparse
import json
import math
import re
from collections import deque
from pathlib import Path

FIELDS = (
    "vad_end_to_agent_speaking_s",
    "vad_end_to_verified_workflow_s",
    "provider_generation_to_first_audio_s",
)
VALUE = re.compile(r"\b(" + "|".join(FIELDS) + r")=([0-9]+(?:\.[0-9]+)?)\b")


def summarize(path: Path, window: int = 256) -> dict:
    series = {name: deque(maxlen=window) for name in FIELDS}
    overlaps = 0
    with path.open() as log:
        for line in log:
            if " latency " not in line or "summary metric=" in line:
                continue
            overlaps += "agent_speaking_before_vad_end=true" in line
            for name, raw in VALUE.findall(line):
                value = float(raw)
                if math.isfinite(value):
                    series[name].append(value)
    metrics = {}
    for name, samples in series.items():
        if not samples:
            metrics[name] = {"n": 0, "p50_s": None, "p95_s": None}
            continue
        ordered = sorted(samples)
        metrics[name] = {
            "n": len(ordered),
            "p50_s": ordered[math.ceil(len(ordered) * 0.5) - 1],
            "p95_s": ordered[math.ceil(len(ordered) * 0.95) - 1],
        }
    return {
        "metrics": metrics,
        "window": window,
        "overlap_observations_in_log": overlaps,
        "acoustic_end_to_physical_first_audible": "unavailable; requires coordinated live measurement",
        "scope": "VAD/state, validated read-workflow, and provider-generation intervals are distinct; no inference of action correctness or spoken completion",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--window", type=int, default=256)
    args = parser.parse_args()
    if not 1 <= args.window <= 10000:
        parser.error("window must be 1-10000")
    print(json.dumps(summarize(args.log, args.window), indent=2))
