"""Fake-call simulation CLI. See src/calls/sim.py.

uv run python scripts/calls_sim.py --list | --scenario ID | --all
        [--play] [--hud] [--llm-caller] [--model ID] [--scripted]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
# Temporary rewire for this process only; defaults elsewhere are untouched.
os.environ.setdefault("JARVIS_CALL_LLM", "openrouter_free")

from calls import sim


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--scenario", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--play", action="store_true", help="play Jarvis audio")
    ap.add_argument("--hud", action="store_true", help="append to live events.jsonl")
    ap.add_argument("--llm-caller", action="store_true")
    ap.add_argument(
        "--scripted", action="store_true", help="use scenario llm_replies, no network"
    )
    ap.add_argument("--model", default=None, help="first model to try")
    a = ap.parse_args(argv)
    scns = sim.load_scenarios()
    if a.list:
        for s in scns.values():
            print(f"{s.id}\t{s.title}")
        return 0
    if a.all:
        chosen = list(scns.values())
    else:
        missing = [i for i in a.scenario if i not in scns]
        if missing or not a.scenario:
            print(
                f"pick --scenario ID (known: {', '.join(scns)}) or --all",
                file=sys.stderr,
            )
            return 2
        chosen = [scns[i] for i in a.scenario]
    runner = sim.SimRunner(
        play=a.play,
        hud=a.hud,
        llm_caller=a.llm_caller,
        model=a.model,
        scripted=a.scripted,
    )
    report = asyncio.run(runner.run_many(chosen))
    print(sim.render_table(report))
    print(f"\nreport: {runner.run_dir}/report.md")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
