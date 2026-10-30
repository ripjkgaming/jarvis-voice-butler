#!/usr/bin/env python3
"""Compare the previous full-history HUD read with the bounded tail reader.

Uses synthetic captions in a temporary directory; no services or models needed.
Run with: uv run python scripts/bench_caption_storage.py
"""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from log_tail import tail_lines


def measure(read, iterations=100):
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        read()
        samples.append((time.perf_counter() - started) * 1000)
    return round(statistics.median(samples), 4)


def main():
    rows = []
    with tempfile.TemporaryDirectory(prefix="jarvis-caption-bench-") as directory:
        path = Path(directory) / "captions.log"
        for count in (250, 5000, 20000):
            path.write_text(("1\tsir\t" + "x" * 500 + "\n") * count, encoding="utf-8")

            def legacy():
                return path.read_text(encoding="utf-8").splitlines()[-20:]

            def optimized():
                return tail_lines(path, 20)

            assert legacy() == optimized()
            rows.append(
                {
                    "history_lines": count,
                    "file_bytes": path.stat().st_size,
                    "legacy_read_ms_median": measure(legacy),
                    "tail_read_ms_median": measure(optimized),
                }
            )
    print(
        json.dumps(
            {"iterations": 100, "caption_limit": 20, "measurements": rows}, indent=2
        )
    )


if __name__ == "__main__":
    main()
