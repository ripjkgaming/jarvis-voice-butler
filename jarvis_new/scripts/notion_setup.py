#!/usr/bin/env python3
"""One-time Notion connect: save the internal-integration token to $JARVIS_HOME/keys.env.

Usage:
    .venv/bin/python scripts/notion_setup.py [--token TOKEN]
    .venv/bin/python scripts/notion_setup.py --help

Without --token it prompts (hidden input). The token is validated against
GET /v1/users/me, then stored as NOTION_TOKEN in keys.env (0600). Sharing
pages/databases: in Notion open the page -> "..." -> "Add connections" ->
pick the integration. Pages the integration can't see stay invisible to it.

Safe to run with --help any time; --token on a shared machine leaks into
shell history, so prefer the prompt.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from notion_api import BASE, NOTION_VERSION, jarvis_home


def _keys_path() -> Path:
    override = os.environ.get("JARVIS_KEYS_FILE", "").strip()
    return Path(override) if override else jarvis_home() / "keys.env"


def _save_token(token: str) -> Path:
    path = _keys_path()
    lines: list[str] = []
    with contextlib.suppress(OSError):
        lines = path.read_text().splitlines()
    lines = [ln for ln in lines if not ln.strip().startswith("NOTION_TOKEN=")]
    lines.append(f"NOTION_TOKEN={token}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)
    return path


def _validate(token: str) -> str:
    req = urllib.request.Request(
        f"{BASE}/users/me",
        headers={"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        me = json.loads(resp.read().decode() or "{}")
    if me.get("object") == "error" or not me.get("id"):
        raise SystemExit(
            f"Notion refused that token, Sir: {me.get('message', 'unknown error')}"
        )
    return str(me.get("name", "") or me.get("id", ""))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--token", default="", help="integration token (else prompted)")
    args = ap.parse_args()
    token = (
        args.token.strip()
        or getpass.getpass("Paste the Notion integration token: ").strip()
    )
    if not token:
        raise SystemExit("No token given.")
    who = _validate(token)
    path = _save_token(token)
    print(f"Saved. Notion connected as {who}: {path}")
    print("Share pages with the integration: page -> ... -> Add connections.")


if __name__ == "__main__":
    main()
