"""Call event stream (W3). Events are JSON dicts ``{"type": "call", ...}``.

Delivery goes through a pluggable sink. Default sink: append JSONL to
``<calls dir>/events.jsonl`` (the bridge/HUD tails it) and, when a hub room is
registered with ``set_room``, publish on the HUD topic ``jarvis-tasks``.
Voice commands are routed to the active CallSession by ``handle_command``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("calls.events")

COMMANDS = (
    "takeover",
    "hold",
    "resume",
    "hangup",
    "accept",
    "decline",
    "take_message",
    "stop_all",
)

Sink = Callable[[dict], Any]

_sink: Sink | None = None
_room: Any = None
_active: Any = None  # the live CallSession


def calls_dir() -> Path:
    env = os.environ.get("JARVIS_CALLS_DIR", "").strip()
    if env:
        return Path(env)
    home = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(home) if home else Path.home() / ".jarvis") / "calls"


def set_sink(sink: Sink | None) -> None:
    """Install a sink (tests). None restores the default sink."""
    global _sink
    _sink = sink


def set_room(room: Any) -> None:
    global _room
    _room = room


def _default_sink(event: dict) -> None:
    try:
        d = calls_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        log.debug("events.jsonl write failed", exc_info=True)
    room = _room
    if room is not None:
        try:
            from hud_events import TOPIC

            local = getattr(room, "local_participant", None)
            if local is not None:
                res = local.publish_data(json.dumps(event), topic=TOPIC)
                if asyncio.iscoroutine(res):
                    try:
                        asyncio.get_running_loop().create_task(res)
                    except RuntimeError:
                        res.close()
        except Exception:
            log.debug("hud publish failed", exc_info=True)


def emit(event: dict) -> None:
    """Deliver one event. Never raises."""
    event = {"type": "call", **event}
    try:
        res = (_sink or _default_sink)(event)
        if asyncio.iscoroutine(res):
            try:
                asyncio.get_running_loop().create_task(res)
            except RuntimeError:
                res.close()
    except Exception:
        log.exception("call event sink failed")


def state_event(
    call_id, state, direction, caller, goal, started_at, mode, ai_disclosed, recording
) -> dict:
    return {
        "event": "state",
        "call_id": call_id,
        "state": state,
        "direction": direction,
        "caller": caller,
        "goal": goal,
        "started_at": started_at,
        "mode": mode,
        "ai_disclosed": bool(ai_disclosed),
        "recording": bool(recording),
    }


def transcript_event(call_id, speaker, text, partial=False, ts=None) -> dict:
    return {
        "event": "transcript",
        "call_id": call_id,
        "speaker": speaker,
        "text": text,
        "partial": bool(partial),
        "ts": ts or time.time(),
    }


def speaking_event(call_id, level) -> dict:
    return {
        "event": "speaking",
        "call_id": call_id,
        "level": max(0.0, min(1.0, float(level))),
    }


def escalation_event(call_id, reason) -> dict:
    return {"event": "escalation", "call_id": call_id, "reason": reason}


def summary_event(call_id, summary, outcome) -> dict:
    return {
        "event": "summary",
        "call_id": call_id,
        "summary": summary,
        "outcome": outcome,
    }


def set_active(session: Any) -> None:
    global _active
    _active = session


def get_active() -> Any:
    return _active


async def handle_command(name: str) -> dict:
    """Voice command entry. Returns {"ok": bool, "detail": str}."""
    name = (name or "").strip().lower().replace(" ", "_")
    if name not in COMMANDS:
        return {"ok": False, "detail": f"unknown command {name!r}"}
    if name == "stop_all":
        try:
            from calls import calllog, rules

            n = rules.cancel_rule(None)
            calllog.audit("command", {"name": name, "cancelled_rules": n})
        except Exception as exc:
            return {"ok": False, "detail": f"stop_all failed: {exc}"}
        sess = _active
        if sess is not None:
            await sess.handle_command("hangup")
        return {"ok": True, "detail": f"cancelled {n} rules"}
    sess = _active
    if sess is None:
        return {"ok": False, "detail": "no active call"}
    return await sess.handle_command(name)
