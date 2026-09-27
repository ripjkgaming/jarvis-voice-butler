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
import os
import time
from pathlib import Path
from typing import Any

TOPIC = "jarvis-tasks"

#: Conversation mirror for HUDs without a LiveKit client (the Tauri
#: webview has no WebRTC, so it can never join a room — it reads these).
CAPTIONS_FILE = "captions.log"
CAPTIONS_KEEP = 200

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


#: Live subtitles: a partial transcript earns a caption line when it grew
#: by this many chars or this many seconds passed (overlay shows the tail,
#: so interim lines read as live typing, not lag).
PARTIAL_MIN_CHARS = 8
PARTIAL_MIN_SECONDS = 1.5


def partial_changed(last: str, last_ts: float, text: str, now: float) -> bool:
    """Should an interim transcript line be captioned? Pure."""
    text = " ".join(str(text or "").split())
    if not text or text == last:
        return False
    if len(text) >= len(last) + PARTIAL_MIN_CHARS:
        return True
    return (now - last_ts) >= PARTIAL_MIN_SECONDS


def captions_path() -> Path:
    """Captions file under $JARVIS_HOME (default ~/.jarvis). Pure (env)."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    base = Path(home) if home else Path.home() / ".jarvis"
    return base / CAPTIONS_FILE


def caption(role: str, text: str) -> bool:
    """Append one `ts\\trole\\ttext` line, pruning to the last keep window.

    Fail-soft, never raises. Roles: "sir" (user) / "jarvis" (assistant).
    """
    try:
        text = " ".join(str(text or "").split())
        if not text:
            return False
        path = captions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(f"{int(time.time())}\t{role}\t{text[:500]}\n")
        lines = path.read_text().splitlines()
        if len(lines) > CAPTIONS_KEEP + 50:
            path.write_text("\n".join(lines[-CAPTIONS_KEEP:]) + "\n")
        return True
    except OSError:
        return False


def read_captions(limit: int = 20) -> list[dict]:
    """Tail of the captions file as [{ts, role, text}]. Never raises."""
    try:
        lines = captions_path().read_text().splitlines()[-max(1, limit) :]
    except OSError:
        return []
    out: list[dict] = []
    for line in lines:
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        try:
            ts = int(parts[0])
        except ValueError:
            continue
        out.append({"ts": ts, "role": parts[1], "text": parts[2]})
    return out
