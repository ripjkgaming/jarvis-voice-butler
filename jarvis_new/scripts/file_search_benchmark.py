#!/usr/bin/env python3
"""Private synthetic file-index probe; no desktop, network, model or user files.

Runs the production find_files route against a generated 20,000-node graph,
with locate disabled and filesystem fallback forbidden. Measures wall time and
2ms event-loop heartbeat delay. Results describe this fixture, not live voice
latency. Run before/after separately with the same arguments and compare hashes.
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
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def graph_fixture(count):
    return {
        "nodes": [
            {
                "id": f"file-{i}",
                "type": "file",
                "label": f"engineering-notes-{i:05d}.md",
                "path": f"/mnt/data/fixture/dept-{i % 40}/engineering-notes-{i:05d}.md",
                "tags": ["md"],
            }
            for i in range(count)
        ],
        "edges": [],
    }


async def measured(operation):
    gaps = []
    finished = False

    async def heartbeat():
        previous = time.perf_counter()
        while not finished:
            await asyncio.sleep(0.002)
            now = time.perf_counter()
            gaps.append(max(0, now - previous - 0.002))
            previous = now

    ticker = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    started = time.perf_counter()
    try:
        result = await operation()
        wall = time.perf_counter() - started
        await asyncio.sleep(0.003)
        return result, {"wall_ms": wall * 1000, "max_loop_lag_ms": max(gaps) * 1000}
    finally:
        finished = True
        await ticker


def distribution(values):
    values = sorted(values)
    return {
        "n": len(values),
        "p50": statistics.median(values),
        "p95": values[math.ceil(0.95 * len(values)) - 1],
    }


async def main(args):
    from system.files_tools import FilesTools

    async def no_locate(*_args):
        return []

    async def no_fallback(*_args):
        raise AssertionError("fixture unexpectedly invoked filesystem fallback")

    graph = graph_fixture(args.nodes)
    queries = ("engineering notes", "engineering-notes-00001.md", "md")
    rows = []
    outputs = []
    with tempfile.TemporaryDirectory(prefix="jarvis-search-benchmark-") as tmp:
        home = Path(tmp)
        (home / "brain").mkdir()
        (home / "brain" / "graph.json").write_text(json.dumps(graph))
        with (
            patch.dict(os.environ, JARVIS_HOME=tmp, JARVIS_LOCAL="1"),
            patch("system.files_tools.log_action"),
            patch.object(FilesTools, "_via_locate", no_locate),
            patch.object(FilesTools, "_via_find", no_fallback),
        ):
            for _ in range(args.rounds):
                for query in queries:
                    tool = FilesTools()
                    result, timing = await measured(
                        lambda query=query, tool=tool: tool.find_files(
                            SimpleNamespace(session=None), query=query
                        )
                    )
                    rows.append({"query": query, **timing})
                    outputs.append(result)
    report = {
        "label": args.label,
        "python": sys.version,
        "fixture_nodes": args.nodes,
        "fixture_sha256": digest(graph),
        "result_sha256": digest(outputs),
        "source_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                ROOT / "src/second_brain.py",
                ROOT / "src/system/files_tools.py",
            )
        },
        "rows": rows,
        "summary": {
            query: {
                key: distribution([r[key] for r in rows if r["query"] == query])
                for key in ("wall_ms", "max_loop_lag_ms")
            }
            for query in queries
        },
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"label": args.label, "summary": report["summary"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--nodes", type=int, default=20_000)
    args = parser.parse_args()
    if args.rounds < 1 or args.nodes < 1:
        parser.error("rounds and nodes must be positive")
    asyncio.run(main(args))
