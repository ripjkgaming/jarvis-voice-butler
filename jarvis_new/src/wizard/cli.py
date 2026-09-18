"""CLI for the first-run wizard.

Usage:
    python -m wizard status            # JSON-ish summary of all steps
    python -m wizard check             # run every step's check (no installs)
    python -m wizard run               # run steps, skipping green (interactive)
    python -m wizard run --step livekit
    python -m wizard run --force       # redo even green steps
    python -m wizard deps              # print system-deps report + install cmds
    python -m wizard hf-audit          # HF cache inventory + optional prune
    python -m wizard prune-hf          # delete unreferenced HF models

Uses only argparse + the wizard package (stdlib core). Interactive key
prompts use getpass.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from wizard.state import WizardState, state_path


def _repo() -> Path:
    override = os.environ.get("JARVIS_REPO", "").strip()
    return Path(override) if override else Path.cwd()


def _load_state() -> WizardState:
    home = os.environ.get("JARVIS_HOME", "").strip()
    return WizardState(state_path(Path(home) if home else None))


def cmd_status(args: argparse.Namespace) -> int:
    from wizard.steps import summary

    state = _load_state()
    data = summary(state)
    print(json.dumps(data, indent=2))
    return 0 if data["all_green"] else 1


def cmd_check(args: argparse.Namespace) -> int:
    from wizard.steps import ORDER

    for step in ORDER:
        status, detail = step.check()
        print(f"[{status:5s}] {step.id:8s} {detail}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from wizard.steps import ORDER, run_all, run_step, step_by_id

    state = _load_state()
    if args.step:
        step = step_by_id(args.step)
        if step is None:
            print(
                f"unknown step: {args.step}; choose from "
                f"{', '.join(s.id for s in ORDER)}",
                file=sys.stderr,
            )
            return 2
        result = run_step(step, state, force=args.force)
        print(f"[{result.status}] {result.step_id}: {result.detail}")
        return 0 if result.status != "red" else 1
    results = run_all(state, force=args.force)
    for r in results:
        print(f"[{r.status:5s}] {r.step_id:8s} {r.detail}")
    return 1 if any(r.status == "red" for r in results) else 0


def cmd_deps(args: argparse.Namespace) -> int:
    from wizard.deps import TOOLS

    for t in TOOLS:
        marker = "OK " if t.installed() else "-- "
        where = ""
        if not t.installed() and (t.dnf_packages or t.flatpak_app):
            where = "  ->  " + " ".join(t.install_command())
        print(f"{marker}{t.name:12s} {t.label}{where}")
    return 0


def cmd_hf_audit(args: argparse.Namespace) -> int:
    from wizard.gate import hf_cache_summary

    data = hf_cache_summary()
    total_mb = data["total_bytes"] / (1024 * 1024)
    wasted_mb = data["wasted_bytes"] / (1024 * 1024)
    print(f"HF cache: {total_mb:.0f} MiB total, {wasted_mb:.0f} MiB unreferenced")
    for m in data["models"]:
        used = "used" if m["used"] else "unused"
        print(f"  {used:7s} {m['bytes'] / 1024 / 1024:7.0f} MiB  {m['name']}")
    return 0


def cmd_prune_hf(args: argparse.Namespace) -> int:
    from wizard.gate import prune_unused

    removed = prune_unused()
    for name in removed:
        print(f"removed {name}")
    if not removed:
        print("nothing unreferenced to prune")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis-wizard",
        description="Jarvis desktop first-run installer wizard",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show persisted step state (JSON)")
    sub.add_parser("check", help="run checks only, no installs")
    run = sub.add_parser("run", help="run wizard steps (skips green unless --force)")
    run.add_argument("--step", help="run a single step by id")
    run.add_argument("--force", action="store_true", help="redo green steps too")
    sub.add_parser("deps", help="system-deps report + install commands")
    sub.add_parser("hf-audit", help="inventory the HuggingFace cache")
    sub.add_parser("prune-hf", help="delete unreferenced HF models")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "status": cmd_status,
        "check": cmd_check,
        "run": cmd_run,
        "deps": cmd_deps,
        "hf-audit": cmd_hf_audit,
        "prune-hf": cmd_prune_hf,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
