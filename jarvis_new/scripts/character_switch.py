#!/usr/bin/env python3
"""Switch the active Jarvis character from a shell: jarvis | ultron | friday.

Posts to the bridge's /character/switch, so it runs the same state machine,
transition and voice handoff as a spoken or typed switch. Stdlib only.
"""

from __future__ import annotations

import json
import os
import sys
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


def main(argv: list[str]) -> int:
    target = (argv[1] if len(argv) > 1 else "").strip().lower()
    if target not in CHARACTERS:
        print(f"usage: character_switch.py {'|'.join(CHARACTERS)}", file=sys.stderr)
        return 2
    port = os.environ.get("JARVIS_BRIDGE_PORT", "4317")
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
