"""Explicit CLI operations. There is deliberately no decision scheduler."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

from .engine import Engine, refresh
from .ledger import Ledger, utcnow
from .service import PORT, serve


def install_service(state_dir: Path) -> dict:
    project = Path(__file__).resolve().parents[2]
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required to install the paper service.")

    # Systemd path arguments are quoted and percent specifiers escaped. Only
    # resolved local paths enter the unit, never a shell command or credentials.
    def quoted(path):
        return json.dumps(str(path).replace("%", "%%").replace("$", "$$"))

    if any(character in str(project) for character in "\r\n"):
        raise RuntimeError("Service working directory must be a single-line path.")

    directory = Path.home() / ".config/systemd/user"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    unit = directory / "jarvis-paper-market.service"
    content = (
        "[Unit]\nDescription=Jarvis local paper-market dashboard and quote marks\nAfter=network-online.target\n\n"
        "[Service]\nType=simple\n"
        f"WorkingDirectory={str(project).replace('%', '%%')}\n"
        f"ExecStart={quoted(uv)} run --no-sync python -m paper_market --state-dir {quoted(state_dir)} serve\n"
        "Restart=on-failure\nRestartSec=10\nNoNewPrivileges=true\nUMask=0077\n\n"
        "[Install]\nWantedBy=default.target\n"
    )
    if unit.exists() and unit.read_text() != content:
        raise RuntimeError(
            "An existing paper service has different configuration; inspect it before replacing."
        )
    unit.write_text(content)
    os.chmod(unit, 0o600)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, timeout=15)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", unit.name], check=True, timeout=30
    )
    return {
        "status": "service_started",
        "unit": str(unit),
        "url": f"http://127.0.0.1:{PORT}/api/paper-market",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Jarvis local paper-only market experiment"
    )
    parser.add_argument(
        "--state-dir", type=Path, default=Path.home() / ".jarvis/paper-market"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in (
        "init",
        "snapshot",
        "refresh",
        "run-once",
        "preflight",
        "install-service",
    ):
        subparsers.add_parser(name)
    server_parser = subparsers.add_parser("serve")
    server_parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    os.umask(0o077)
    state_dir = args.state_dir.expanduser().resolve()
    ledger = Ledger(state_dir / "ledger.sqlite3")
    if args.command in {"init", "install-service"}:
        ledger.initialize(utcnow())
    else:
        ledger.experiment()  # Missing state must never silently restart a run.
    if args.command == "init" or args.command == "snapshot":
        result = ledger.snapshot()
    elif args.command == "refresh":
        result = refresh(ledger)
    elif args.command == "run-once":
        result = asyncio.run(Engine(ledger).run_once())
    elif args.command == "preflight":
        result = asyncio.run(Engine(ledger).preflight())
    elif args.command == "install-service":
        result = install_service(state_dir)
    else:
        serve(ledger, port=args.port)
        return
    print(json.dumps(result, allow_nan=False, indent=2))
    if result.get("status") in {"error", "preflight_failed", "clock_error"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
