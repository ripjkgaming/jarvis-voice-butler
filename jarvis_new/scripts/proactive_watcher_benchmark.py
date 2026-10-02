#!/usr/bin/env python3
"""Injected sensor-stall probe: no real sensor, audio, desktop or model calls.

The production telemetry reader sees a mocked upower subprocess that sleeps
for --stall-ms and returns a healthy battery. A 2ms asyncio heartbeat measures
the maximum scheduling lag during each collection. Sensor completion time is
expected to stay constant; this probes voice-loop responsiveness only.

Run before and after the source change in separate invocations:
  uv run python scripts/proactive_watcher_benchmark.py --label before --output /tmp/before.json
  uv run python scripts/proactive_watcher_benchmark.py --label after --output /tmp/after.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from context.telemetry import read_real_snapshot  # noqa: E402
from proactive.watcher import ProactiveWatcher  # noqa: E402


async def trial(stall_ms: float) -> dict[str, float]:
    gaps = []

    async def beat() -> None:
        previous = time.perf_counter()
        while True:
            await asyncio.sleep(0.002)
            current = time.perf_counter()
            gaps.append(max(0.0, current - previous - 0.002))
            previous = current

    def slow_upower(*args, **kwargs):
        time.sleep(stall_ms / 1000)
        return SimpleNamespace(stdout="percentage: 99%\nstate: discharging\n")

    async def speak(line: str) -> None:
        raise AssertionError("healthy battery must not speak")

    policy = SimpleNamespace(drain_queue=lambda now: SimpleNamespace(texts=[]))
    watcher = ProactiveWatcher(
        read_real_snapshot, speak, policy=policy, log_fn=lambda *args: None
    )
    heartbeat = asyncio.create_task(beat())
    try:
        await asyncio.sleep(0.003)
        with patch("subprocess.run", slow_upower):
            started = time.perf_counter()
            await watcher._tick(1)
            elapsed = time.perf_counter() - started
        await asyncio.sleep(0.004)
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
    assert watcher._history == [(1, 99)]
    return {"wall_ms": elapsed * 1000, "max_loop_lag_ms": max(gaps) * 1000}


async def main(args) -> None:
    rows = [await trial(args.stall_ms) for _ in range(args.rounds)]
    report = {
        "label": args.label,
        "kind": (
            f"injected {args.stall_ms:g}ms upower subprocess; "
            "no real process, sensor, audio or model"
        ),
        "rows": rows,
        "median_max_loop_lag_ms": statistics.median(
            row["max_loop_lag_ms"] for row in rows
        ),
        "max_loop_lag_ms": max(row["max_loop_lag_ms"] for row in rows),
        "median_wall_ms": statistics.median(row["wall_ms"] for row in rows),
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--stall-ms", type=float, default=120)
    args = parser.parse_args()
    if args.rounds < 1 or args.stall_ms <= 0:
        parser.error("rounds and stall-ms must be positive")
    asyncio.run(main(args))
