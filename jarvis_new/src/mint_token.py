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

Usage: `python src/mint_token.py [agent-name] [room-name]`
"""

from __future__ import annotations

import json
import os
import random
import sys
from datetime import timedelta
from pathlib import Path


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


def main(argv: list[str]) -> int:
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
        print(json.dumps({"error": f"missing LiveKit creds: {', '.join(missing)}"}))
        return 2

    agent_name = (
        argv[1] if len(argv) > 1 else os.environ.get("AGENT_NAME", "")
    ).strip() or "my-agent"
    room = (
        argv[2] if len(argv) > 2 else f"voice_assistant_room_{random.randint(0, 9999)}"
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
    token.with_room_config(
        RoomConfiguration(agents=[RoomAgentDispatch(agent_name=agent_name)])
    )
    token.with_ttl(timedelta(minutes=15))
    print(
        json.dumps(
            {
                "serverUrl": creds["LIVEKIT_URL"],
                "roomName": room,
                "participantName": "user",
                "participantToken": token.to_jwt(),
            }
        )
    )
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
