"""Mint a participant token for the HUD webview (Tauri `mint_token` backend).

Counterpart of `frontend/app/api/token/route.ts` for the static-export
build, where no Next.js route exists. The shell (`shell/src-tauri`)
execs this with a venv python that has the `livekit` package and reads
one JSON line from stdout::

    {"serverUrl": ..., "roomName": ..., "participantName": ...,
     "participantToken": ...}

Credentials: env first (`LIVEKIT_URL/API_KEY/API_SECRET`), then
`<repo>/.env.local`, then `<repo>/frontend/.env.local` (same precedence
as `wake_client.load_livekit_env`). Fails with a non-zero exit and a
`{"error": ...}` line — never a traceback on stdout.

Usage: `python src/mint_token.py [agent-name] [room-name] [join]`

- summon mode (default): fresh random room + agent dispatch (the HUD
  used to start its own calls this way).
- join mode (`join` as third arg with an explicit room): token for the
  named room WITHOUT dispatch — for joining a live wake summoned room
  receive-only (the agent is already there; a second dispatch would
  summon a second voice).
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
from datetime import timedelta
from pathlib import Path

_ROOM_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def valid_room_name(name: object) -> bool:
    """Room names are wake-generated `jarvis-<epoch>`; strict allowlist. Pure."""
    return isinstance(name, str) and _ROOM_RE.fullmatch(name) is not None


def _load_dotenv(path: Path, found: dict) -> None:
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("\"'")
            if key in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
                found.setdefault(key, value)
    except OSError:
        pass


def load_creds() -> dict:
    creds = {
        k: os.environ[k]
        for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
        if os.environ.get(k)
    }
    repo = Path(__file__).resolve().parent.parent
    _load_dotenv(repo / ".env.local", creds)
    _load_dotenv(repo / "frontend" / ".env.local", creds)
    return creds


def mint_token_payload(
    room: str = "", dispatch: bool = True, agent_name: str = ""
) -> dict:
    """Mint a join token. Import-safe core of main() for bridge reuse.

    Raises RuntimeError when LiveKit creds are missing (callers map to
    503), ValueError on a bad room name.
    """
    from livekit.api import (
        AccessToken,
        RoomAgentDispatch,
        RoomConfiguration,
        VideoGrants,
    )

    creds = load_creds()
    missing = [
        k
        for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
        if not creds.get(k)
    ]
    if missing:
        raise RuntimeError(f"missing LiveKit creds: {', '.join(missing)}")
    agent = (agent_name or os.environ.get("AGENT_NAME", "")).strip() or "my-agent"
    # CLI parity: an invalid room falls back to random (bridge validates
    # explicitly and answers 400 instead).
    room = (
        room
        if valid_room_name(room)
        else f"voice_assistant_room_{random.randint(0, 9999)}"
    )
    identity = f"voice_assistant_user_{random.randint(0, 9999)}"

    token = AccessToken(creds["LIVEKIT_API_KEY"], creds["LIVEKIT_API_SECRET"])
    token.with_identity(identity).with_name("user")
    token.with_grants(
        VideoGrants(
            room_join=True,
            room=room,
            can_publish=True,
            can_publish_data=True,
            can_subscribe=True,
        )
    )
    if dispatch:
        token.with_room_config(
            RoomConfiguration(agents=[RoomAgentDispatch(agent_name=agent)])
        )
    token.with_ttl(timedelta(minutes=15))
    return {
        "serverUrl": creds["LIVEKIT_URL"],
        "roomName": room,
        "participantName": "user",
        "participantToken": token.to_jwt(),
    }


def main(argv: list[str]) -> int:
    agent_name = (
        argv[1] if len(argv) > 1 else os.environ.get("AGENT_NAME", "")
    ).strip() or "my-agent"
    room_arg = argv[2] if len(argv) > 2 else ""
    join_mode = len(argv) > 3 and argv[3] == "join" and valid_room_name(room_arg)
    try:
        print(json.dumps(mint_token_payload(room_arg, not join_mode, agent_name)))
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except SystemExit:
        raise
    except Exception as exc:  # keep stdout parseable; details on stderr
        print(json.dumps({"error": str(exc)[:200]}))
        print(f"mint_token failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
