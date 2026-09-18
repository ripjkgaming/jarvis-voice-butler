"""Shared helpers for local Linux PC-control tools.

Design rules:
- Local-only: every public helper refuses unless JARVIS_LOCAL=1. The voice
  agent refuses system tools when deployed (Docker/LiveKit Cloud) because
  there is no user PC to control there.
- No shell: subprocess calls use argv lists, never shell=True.
- Timeouts everywhere; failures return user-facing strings, never tracebacks.
- Everything appends to the private local log (~/.jarvis/actions.log).
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

LOCAL_ENV_VAR = "JARVIS_LOCAL"
LOG_PATH = Path.home() / ".jarvis" / "actions.log"


class LocalSystemError(Exception):
    """A user-facing system operation failure."""


def require_local() -> None:
    """Refuse system control when not running on the user's own machine."""
    if os.environ.get(LOCAL_ENV_VAR) != "1":
        raise LocalSystemError(
            "System control is only available when I run on your own machine."
        )


async def run_cmd(*argv: str, timeout: float = 10.0) -> tuple[int, str, str]:
    """Run a command with argv (no shell). Returns (rc, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return 127, "", f"command not found: {argv[0]}"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        with __import__("contextlib").suppress(Exception):
            proc.kill()
        with __import__("contextlib").suppress(Exception):
            await proc.wait()
        return 124, "", f"timed out after {timeout}s: {argv[0]}"
    rc = proc.returncode if proc.returncode is not None else 1
    return (
        rc,
        out.decode(errors="replace").strip() if out else "",
        err.decode(errors="replace").strip() if err else "",
    )


def log_action(category: str, detail: str) -> None:
    """Append one line to the private local log. Never raises."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {category} {detail[:300]}\n"
        with LOG_PATH.open("a") as fh:
            fh.write(line)
    except Exception:
        pass
