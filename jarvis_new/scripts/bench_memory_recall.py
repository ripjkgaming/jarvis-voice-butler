#!/usr/bin/env python3
"""Compare parsed-note cache misses/hits using a temporary synthetic vault.

Run with: uv run python scripts/bench_memory_recall.py
No models, services, or user vault files are accessed.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import memory


def main():
    previous = os.environ.get("JARVIS_VAULT")
    cold, warm = [], []
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-recall-bench-") as directory:
            os.environ["JARVIS_VAULT"] = directory
            notes = Path(directory) / "Memory" / "Threads"
            notes.mkdir(parents=True)
            for index in range(24):
                (notes / f"lesson-{index}.md").write_text(
                    f"# Physics lesson {index}\n\n"
                    + "\n\n".join(
                        f"## Topic {number}\nPhysics wave speed equals frequency times wavelength."
                        for number in range(48)
                    ),
                    encoding="utf-8",
                )
            for _ in range(50):
                with memory._note_cache_lock:
                    memory._note_cache.clear()
                started = time.perf_counter()
                expected = memory.recall("physics wave", brain=lambda *_: [])
                cold.append((time.perf_counter() - started) * 1000)
                started = time.perf_counter()
                actual = memory.recall("physics wave", brain=lambda *_: [])
                warm.append((time.perf_counter() - started) * 1000)
                assert actual == expected
    finally:
        if previous is None:
            os.environ.pop("JARVIS_VAULT", None)
        else:
            os.environ["JARVIS_VAULT"] = previous
        with memory._note_cache_lock:
            memory._note_cache.clear()
    cold_median = statistics.median(cold)
    warm_median = statistics.median(warm)
    print(
        json.dumps(
            {
                "iterations": 50,
                "notes": 24,
                "paragraphs_per_note": 48,
                "cold_recall_ms_median": round(cold_median, 4),
                "warm_recall_ms_median": round(warm_median, 4),
                "speedup": round(cold_median / warm_median, 3),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
