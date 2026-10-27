#!/usr/bin/env python3
"""Switch the active Jarvis character from a shell: jarvis | ultron | friday.

Brings the HUD forward first (`jarvis-shell show`) so the cinematic handoff
plays on screen; pass --no-ui or set JARVIS_NO_UI=1 to switch silently.

Posts to the bridge's /character/switch, so it runs the same state machine,
transition and voice handoff as a spoken or typed switch. Stdlib only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CHARACTERS = ("jarvis", "ultron", "friday")


def _token() -> str:
    env = os.environ.get("JARVIS_BRIDGE_TOKEN", "").strip()
    if env:
        return env
    home = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
    try:
        return (home / "bridge_token").read_text().strip()
    except OSError:
        return ""


REPO = Path(__file__).resolve().parents[1]
SHELL_SRC = REPO / "shell" / "src-tauri" / "src" / "main.rs"
SHELL_WAKE_S = 0.7  # let the webview wake and start polling before the switch


def _shell_bin() -> Path | None:
    env = os.environ.get("JARVIS_SHELL_BIN")
    cands = [Path(env)] if env else []
    cands.append(REPO / "shell" / "src-tauri" / "target" / "debug" / "jarvis-shell")
    for c in cands:
        if c.is_file():
            return c
    return None


def _running_pid(shell: Path) -> int | None:
    try:
        out = subprocess.run(["pgrep", "-f", f"^{shell}$"], capture_output=True,
                             text=True, timeout=3).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    return int(out[0]) if out else None


def show_hud() -> bool:
    """Bring the HUD forward so the handoff plays on screen.

    Only a shell built after the `show` verb existed is asked: an older build
    treats unknown verbs as `toggle`, which could hide a visible HUD.
    """
    if os.environ.get("JARVIS_NO_UI") == "1":
        return False
    shell = _shell_bin()
    if shell is None:
        return False
    try:
        built = shell.stat().st_mtime
        if SHELL_SRC.is_file() and built < SHELL_SRC.stat().st_mtime:
            return False
        # The single-instance hand-off lands in the RUNNING process: it must
        # be started from a build that knows `show`.
        pid = _running_pid(shell)
        if pid is not None and os.stat(f"/proc/{pid}").st_ctime < built:
            print("HUD is running an older build; restart jarvis-shell for on-screen switches",
                  file=sys.stderr)
            return False
        subprocess.run([str(shell), "show"], timeout=6, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    time.sleep(SHELL_WAKE_S)
    return True


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a != "--no-ui"]
    if len(args) != len(argv) - 1:
        os.environ["JARVIS_NO_UI"] = "1"
    target = (args[0] if args else "").strip().lower()
    if target not in CHARACTERS:
        print(f"usage: character_switch.py {'|'.join(CHARACTERS)}", file=sys.stderr)
        return 2
    port = os.environ.get("JARVIS_BRIDGE_PORT", "4317")
    show_hud()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/character/switch",
        data=json.dumps({"target": target, "source": "cli"}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_token()}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("bridge has no character support yet; restart Jarvis", file=sys.stderr)
        else:
            print(f"bridge refused: HTTP {e.code}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError):
        print(f"bridge unreachable on 127.0.0.1:{port}", file=sys.stderr)
        return 1
    reply = res.get("reply") or ""
    if reply:
        print(reply)
    return 0 if res.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
