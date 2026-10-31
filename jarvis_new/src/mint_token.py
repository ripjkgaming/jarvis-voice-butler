"""Mint a call token for the HUD webview (Tauri `mint_token` backend).

Replaces the old LiveKit participant token. The agent worker hosts the
in-repo WebSocket voice hub (see docs/voice-transport.md), so this module
now signs an HMAC call token with the local voice secret
(`$JARVIS_HOME/voice_secret`, created on first use) and points the client
at the hub. No livekit imports. The shell reads one JSON line from stdout::

    {"serverUrl": ..., "roomName": ..., "participantName": ...,
     "participantToken": ...}

`serverUrl` is `ws://127.0.0.1:<JARVIS_VOICE_PORT>/voice` (port default
7880), or `JARVIS_VOICE_URL` verbatim when it is a ws:// or wss:// URL
(tunnels). Failures exit non-zero with a `{"error": ...}` line on stdout.

Usage: `python src/mint_token.py [agent-name] [room-name] [join]`

- summon mode (default): fresh random room, speaker mode (mic + control).
- join mode (`join` as third arg with an explicit room): receive-only HUD
  observer (`mode="observer"`) for a live call; no second dispatch.
"""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

from jarvis_rt.transport.tokens import (
    jarvis_home,
    load_secret,
    mint_call_token,
    valid_room_name,
)

__all__ = [
    "active_call_exists",
    "main",
    "mint_token_payload",
    "valid_room_name",
    "voice_url",
]

DEFAULT_VOICE_PORT = 7880  # Matches jarvis_rt.worker.DEFAULT_PORT.
SUMMON_TTL = 120
PHONE_TTL = 900
ACTIVE_FILE = "voice_active.json"


def voice_port() -> int:
    raw = os.environ.get("JARVIS_VOICE_PORT", "").strip()
    try:
        port = int(raw) if raw else DEFAULT_VOICE_PORT
    except ValueError:
        return DEFAULT_VOICE_PORT
    return port if 0 < port < 65536 else DEFAULT_VOICE_PORT


def voice_url() -> str:
    override = os.environ.get("JARVIS_VOICE_URL", "").strip()
    if override.startswith(("ws://", "wss://")):
        return override
    return f"ws://127.0.0.1:{voice_port()}/voice"


def active_call_exists(home: Path | str | None = None) -> bool:
    """True when the worker's hub registry reports a live call.

    Reads ``$JARVIS_HOME/voice_active.json`` (``{"active": true, ...}``),
    written by the worker. Missing or malformed means no call.
    """
    base = jarvis_home() if home is None else Path(home)
    try:
        data = json.loads((base / ACTIVE_FILE).read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict) or data.get("active") is not True:
        return False
    # A worker that died mid-call leaves "active" behind; trust only a live pid.
    pid = data.get("pid")
    if isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            pass
    return True


def mint_token_payload(
    room: str = "",
    dispatch: bool = True,
    agent_name: str = "",
    *,
    phone_call_id: str = "",
    private_phone: bool = False,
) -> dict:
    """Mint a call token. Import-safe core of main() for bridge reuse.

    ``dispatch`` True means speaker mode in a fresh or named room; False
    means receive-only observer mode. ``agent_name`` is accepted for
    signature compatibility and ignored (the worker hosts the one agent).

    Raises ValueError on a bad room name or a private-room misuse.
    """
    import phone_session

    if private_phone and (room or not dispatch):
        raise ValueError("Private calls require a fresh owner phone session")
    if not private_phone and phone_session.private_room(room):
        raise ValueError("Private phone rooms cannot be joined or dispatched again")
    # CLI parity: an invalid room falls back to random (bridge validates
    # explicitly and answers 400 instead).
    room = (
        room
        if valid_room_name(room)
        else f"voice_assistant_room_{random.randint(0, 9999)}"
    )
    identity = f"voice_assistant_user_{random.randint(0, 9999)}"
    attrs: dict[str, str] = {}
    if private_phone:
        room = phone_session.new_room()
        identity = phone_session.identity_for(room)
        attrs["jarvis.phone_owner"] = room
        attrs["jarvis.phone_sig"] = phone_session.sign(room)
    if phone_call_id:
        import time

        import phone_calls

        if not phone_calls.queue().accepted(phone_call_id, time.time()):
            raise RuntimeError("Invitation unavailable")
        if not private_phone:
            identity = "jarvis-phone-" + phone_call_id
        attrs["jarvis.phone_call"] = phone_call_id

    phone = bool(private_phone or phone_call_id)
    token = mint_call_token(
        room,
        identity,
        "speaker" if dispatch else "observer",
        PHONE_TTL if phone else SUMMON_TTL,
        attrs or None,
        load_secret(),
    )
    return {
        "serverUrl": voice_url(),
        "roomName": room,
        "participantName": "user",
        "participantToken": token,
    }


def main(argv: list[str]) -> int:
    agent_name = (
        argv[1] if len(argv) > 1 else os.environ.get("AGENT_NAME", "")
    ).strip() or "my-agent"
    room_arg = argv[2] if len(argv) > 2 else ""
    join_mode = len(argv) > 3 and argv[3] == "join" and valid_room_name(room_arg)
    try:
        print(json.dumps(mint_token_payload(room_arg, not join_mode, agent_name)))
    except (RuntimeError, ValueError) as exc:
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
