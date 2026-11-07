"""Declarative phone v1 API on the existing authenticated bridge.

No downloaded code, endpoint redirects, credentials, or device permissions.
Legacy bridge endpoints remain available to older APKs.
"""

import json
import os
from pathlib import Path

API_VERSION = 1


def policy():
    """Only two allowlisted data fields; reread so laptop edits need no APK/restart."""
    defaults = {"instant_routing": True, "history_messages": 20}
    path = Path(
        os.environ.get(
            "JARVIS_PHONE_POLICY",
            str(
                Path(__file__).resolve().parent.parent / "config" / "phone_policy.json"
            ),
        )
    )
    try:
        if path.stat().st_size > 4096:
            return defaults
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return defaults
        if type(data.get("instant_routing")) is bool:
            defaults["instant_routing"] = data["instant_routing"]
        value = data.get("history_messages")
        if type(value) is int:
            defaults["history_messages"] = max(1, min(20, value))
    except (OSError, ValueError):
        pass
    return defaults


def capabilities():
    return {
        "ok": True,
        "api_version": API_VERSION,
        "min_client_api": 1,
        "max_client_api": 1,
        "features": {
            "command": True,
            "session": True,
            "incoming_calls": True,
            "incoming_monitor": True,
            "checkin_schedule": True,
            # LOCKE//LINK PWA (docs/phone-app/PLAN.md §6).
            "control": True,
            "state": True,
            "webauthn": True,
        },
        "limits": {"text": 2000, "history": 20},
        "policy": policy(),
    }


def command(body, route, chat):
    if not isinstance(body, dict) or body.get("api_version") != API_VERSION:
        return 426, {
            "ok": False,
            "error": "Unsupported phone API; update the app or laptop bridge.",
        }
    text = body.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        return 400, {"ok": False, "error": "body needs text 1..2000 chars"}
    history = body.get("history", [])
    if not isinstance(history, list) or len(history) > 100:
        return 400, {"ok": False, "error": "history must contain at most 100 messages"}
    settings = policy()
    normalized = []
    for turn in history[-settings["history_messages"] :]:
        if (
            isinstance(turn, list)
            and len(turn) == 2
            and isinstance(turn[0], str)
            and isinstance(turn[1], str)
        ):
            normalized.append(
                ["jarvis" if turn[0].lower() == "jarvis" else "user", turn[1][:1000]]
            )
    payload = {
        "text": text.strip(),
        "history": normalized,
        "guest": body.get("guest") is True,
    }
    if (
        settings["instant_routing"]
        and body.get("instant") is True
        and len(text.strip()) <= 500
    ):
        code, result = route(payload)
        # Only an explicit no-match falls through; never replay a failed action.
        if code != 404 or result.get("error") != "no-route":
            return code, {**result, "via_route": True, "api_version": API_VERSION}
    code, result = chat(payload)
    return code, {**result, "via_route": False, "api_version": API_VERSION}


def session(body, room_reader, waking_reader, mint):
    if not isinstance(body, dict) or body.get("api_version") != API_VERSION:
        return 426, {
            "ok": False,
            "error": "Unsupported phone API; update the app or laptop bridge.",
        }
    if body.get("guest") is True:
        return 403, {"ok": False, "error": "guest mode refuses owner actions"}
    mode = body.get("mode")
    if mode not in ("new", "laptop"):
        return 400, {"ok": False, "error": "mode must be new or laptop"}
    room = "" if mode == "new" else room_reader()
    if mode == "laptop" and not room:
        return 409, {
            "ok": False,
            "error": "The laptop is waking up. Try again shortly."
            if waking_reader()
            else "No live laptop call right now.",
        }
    try:
        payload = mint(room, mode == "new")
    except (RuntimeError, ImportError):
        return 503, {"ok": False, "error": "Laptop voice service is unavailable."}
    except Exception:
        return 500, {
            "ok": False,
            "error": "Laptop could not prepare the voice session.",
        }
    return 200, {"ok": True, "api_version": API_VERSION, **payload}
