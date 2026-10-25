"""Open (or focus) the chess board window.

Prefers the Jarvis Tauri shell's ``chess`` window when the running shell
supports it; otherwise opens a Brave/Chrome ``--app`` window on the locally
served static board (the lightweight chess-only shell: no LiveKit, wake
client or agent involved).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from games.chess.settings import jarvis_home
from games.server import UI_PORT

REPO = Path(__file__).resolve().parents[2]
SHELL_MARKER = b"open_chess_window"


def ui_dir() -> Path | None:
    """Directory holding the exported board UI (chess.html)."""
    env = os.environ.get("JARVIS_CHESS_UI", "").strip()
    cands = [Path(env)] if env else []
    cands += [REPO / "shell" / "ui", REPO / "frontend" / "out"]
    for c in cands:
        if (c / "chess.html").is_file():
            return c
    return None


def shell_binary() -> Path | None:
    which = shutil.which("jarvis-shell")
    if which:
        return Path(which)
    for rel in ("release", "debug"):
        p = REPO / "shell" / "src-tauri" / "target" / rel / "jarvis-shell"
        if p.is_file():
            return p
    return None


def shell_running() -> bool:
    try:
        r = subprocess.run(
            ["pgrep", "-x", "jarvis-shell"], capture_output=True, timeout=2, check=False
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def shell_supports_chess() -> bool:
    b = shell_binary()
    if not b or not shell_running():
        return False
    try:
        return SHELL_MARKER in b.read_bytes()
    except OSError:
        return False


def browser() -> list[str] | None:
    for name in (
        "brave-browser",
        "brave",
        "google-chrome",
        "chromium",
        "chromium-browser",
    ):
        p = shutil.which(name)
        if p:
            return [p]
    return None


def board_url(mode: str, port: int = UI_PORT) -> str:
    return f"http://127.0.0.1:{port}/chess.html?mode={mode}"


def open_window(mode: str, *, port: int = UI_PORT, backend: str = "auto") -> str:
    """Open the board. Returns the backend used: tauri | browser | none."""
    if backend in ("auto", "tauri") and shell_supports_chess():
        b = shell_binary()
        if b:
            try:
                subprocess.Popen(
                    [str(b), "chess", mode],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return "tauri"
            except OSError:
                pass
    br = browser()
    if br and backend != "tauri":
        profile = jarvis_home() / "chess-profile"
        profile.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(
                [
                    *br,
                    f"--app={board_url(mode, port)}",
                    f"--user-data-dir={profile}",
                    "--window-size=1100,820",
                    "--no-first-run",
                    "--disable-features=Translate",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return "browser"
        except OSError:
            pass
    opener = shutil.which("xdg-open")
    if opener:
        subprocess.Popen([opener, board_url(mode, port)], start_new_session=True)
        return "browser"
    return "none"


def focus_window(mode: str = "classic", *, port: int = UI_PORT) -> str:
    """A second launch brings the existing board forward instead of a new game."""
    return open_window(mode, port=port)
