"""Start Jarvis Chess in Voice Mode from the main assistant (LiveKit-free).

``launch_chess`` is a plain launcher, not a handoff specialist: it spawns
``chess --voice`` detached; the caller then ends Jarvis's call so nothing
LiveKit or Gemini runs while chess is played.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def chess_command() -> list[str]:
    exe = shutil.which("chess")
    if exe:
        return [exe, "--voice"]
    launcher = REPO / "scripts" / "chess"
    if launcher.is_file():
        return [str(launcher), "--voice"]
    return [sys.executable, "-m", "games.cli", "--voice"]


def launch_chess_detached(argv: list[str] | None = None) -> tuple[bool, str]:
    """Spawn ``chess --voice`` in its own session. Returns (ok, detail)."""
    cmd = argv or chess_command()
    home = os.environ.get("JARVIS_HOME", "").strip()
    logdir = (Path(home) if home else Path.home() / ".jarvis") / "logs"
    try:
        logdir.mkdir(parents=True, exist_ok=True)
        log = open(logdir / "chess.log", "ab")  # noqa: SIM115 - handed to the child
    except OSError:
        log = None
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log or subprocess.DEVNULL,
            stderr=log or subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(REPO),
        )
    except OSError as exc:
        return False, str(exc)
    finally:
        if log is not None:
            log.close()
    return True, " ".join(cmd)
