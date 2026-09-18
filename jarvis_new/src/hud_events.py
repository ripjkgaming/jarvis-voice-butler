"""HUD data-channel task events (Region 2 publisher).

Agent publishes small JSON packets on the LiveKit data channel topic
``jarvis-tasks`` around tool invocation::

    {"kind": "tool_start", "tool": "nmap_scan", "label": "scanning network…"}
    {"kind": "tool_finish", "tool": "nmap_scan", "ok": true}
    {"kind": "intent_echo", "echo": "hot rod red → crimson"}

Packets are best-effort: publish failures are swallowed so HUD telemetry
can never break a voice session. Every event is also mirrored to
``~/.jarvis/actions.log`` so the node graph has a last-known-topology
fallback when the channel is silent. Zero inference cost.
"""

from __future__ import annotations

import json
import time
from typing import Any

TOPIC = "jarvis-tasks"

FRIENDLY_LABELS: dict[str, str] = {
    "nmap_scan": "scanning network…",
    "nikto_sweep": "sweeping web server…",
    "gobuster_dir": "enumerating paths…",
    "open_app": "opening app…",
    "open_url": "opening page…",
    "take_screenshot": "capturing screen…",
    "take_os_screenshot": "capturing screen…",
    "read_screen_text": "reading screen…",
}


def tool_start_payload(tool: str, label: str | None = None, detail: str = "") -> str:
    return json.dumps(
        {
            "kind": "tool_start",
            "tool": tool,
            "label": label or FRIENDLY_LABELS.get(tool, f"{tool}…"),
            "detail": detail[:200],
            "ts": int(time.time() * 1000),
        }
    )


def tool_finish_payload(tool: str, ok: bool = True, detail: str = "") -> str:
    return json.dumps(
        {
            "kind": "tool_finish",
            "tool": tool,
            "ok": ok,
            "detail": detail[:200],
            "ts": int(time.time() * 1000),
        }
    )


def intent_echo_payload(resolved: str, raw: str = "") -> str:
    echo = f"{raw} → {resolved}" if raw and raw != resolved else resolved
    return json.dumps(
        {"kind": "intent_echo", "echo": echo[:200], "ts": int(time.time() * 1000)}
    )


async def publish(room: Any, payload: str) -> None:
    """Best-effort data-channel publish. Never raises."""
    try:
        local = getattr(room, "local_participant", None)
        if local is None:
            return
        await local.publish_data(payload, topic=TOPIC)
    except Exception:
        pass


def mirror_to_log(tool: str, kind: str, detail: str = "") -> None:
    """Append to actions.log so the graph has a fallback topology."""
    try:
        from system import log_action

        log_action(f"hud:{kind}", f"{tool} {detail[:200]}".strip())
    except Exception:
        pass
