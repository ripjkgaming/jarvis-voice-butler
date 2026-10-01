#!/usr/bin/env python3
"""One-time Telegram pairing for Jarvis.

1. Message @BotFather on Telegram: /newbot, pick a name, copy the token.
2. Run:  .venv/bin/python scripts/telegram_setup.py   (or --token <token>)
3. Send the printed /start line to your new bot from Sir's account.

Saves TELEGRAM_BOT_TOKEN into $JARVIS_HOME/keys.env (0600, other lines
kept) and writes a one-time 6-digit pairing code (15 min) to
$JARVIS_HOME/telegram_pair.json. The bot (src/telegram_bot.py) pairs
the first chat that sends the code and refuses everyone else.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import os
import re
import secrets
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telegram_bot import (
    PAIR_TTL_S,
    keys_env_path,
    load_token,
    pair_path,
)

API_TIMEOUT = 15.0


def jarvis_home() -> Path:
    """$JARVIS_HOME or ~/.jarvis. Pure (env)."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def upsert_env_line(text: str, key: str, value: str) -> str:
    """Set KEY=value in keys.env content, keeping other lines. Pure."""
    lines = (text or "").splitlines()
    entry = f"{key}={value}"
    hit = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, _ = stripped.partition("=")
        if name.strip() == key:
            lines[i] = entry
            hit = True
    if not hit:
        lines.append(entry)
    return "\n".join(lines) + "\n"


def save_token(token: str, home: Path) -> Path:
    """Upsert TELEGRAM_BOT_TOKEN into keys.env (0600). Returns the path."""
    path = keys_env_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = path.read_text()
    except OSError:
        current = ""
    path.write_text(upsert_env_line(current, "TELEGRAM_BOT_TOKEN", token))
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return path


def validate_token(token: str, opener=None) -> dict:
    """getMe check. {ok, username?} — never the token back. Never raises."""

    def _open(req, timeout):
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    opener = opener or _open
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        payload = json.loads(opener(urllib.request.Request(url), API_TIMEOUT).decode())
    except Exception as exc:
        return {"ok": False, "error": f"no answer ({type(exc).__name__})"}
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        desc = ""
        if isinstance(payload, dict):
            desc = str(payload.get("description", ""))[:100]
        return {"ok": False, "error": desc or "rejected"}
    result = payload.get("result", {})
    username = result.get("username", "") if isinstance(result, dict) else ""
    return {"ok": True, "username": str(username)}


def generate_code() -> str:
    """One-time 6-digit pairing code. Pure-ish (secrets)."""
    return f"{secrets.randbelow(900000) + 100000}"


def write_pair_code(code: str, home: Path, now: float | None = None) -> Path:
    """Persist the pairing code with a 15-min expiry. Returns the path."""
    path = pair_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"code": code, "expires_at": (now or time.time()) + PAIR_TTL_S})
    )
    return path


def valid_token_shape(token: str) -> bool:
    """BotFather shape: <digits>:<35-char secret>. Pure."""
    return bool(re.fullmatch(r"\d+:[A-Za-z0-9_-]{30,}", (token or "").strip()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--token", default="", help="bot token (else prompted)")
    args = ap.parse_args(argv)

    print("Create the bot first: message @BotFather, send /newbot,")
    print("pick a name, and copy the token it gives you.")
    token = (args.token or "").strip()
    if not token:
        try:
            token = getpass.getpass("Bot token (hidden): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return 1
    if not token:
        print("No token given. Re-run when BotFather hands you one.")
        return 1
    if not valid_token_shape(token):
        print("That doesn't look like a BotFather token (digits:secret).")
        return 1
    print("Checking the token with Telegram...")
    result = validate_token(token)
    if not result.get("ok"):
        print(f"Telegram refused it: {result.get('error', '?')}")
        return 1
    home = jarvis_home()
    save_token(token, home)
    code = generate_code()
    write_pair_code(code, home)
    bot = result.get("username") or "your bot"
    print(f"Saved. Pairing code (15 min, one chat only): {code}")
    print(f"Send this to @{bot}: /start {code}")
    if load_token(home) != token:
        print("Warning: the saved token didn't read back. Check keys.env.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
