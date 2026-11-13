"""HUD delivery for call events (bridge side, dependency-free).

The agent process appends call events to ``<calls dir>/events.jsonl``
(``calls.events``). The bridge serves them to the HUD by byte-offset cursor and
queues owner commands in ``commands.jsonl``, which the call runtime consumes.
Nothing here imports the agent, so the bridge stays light.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

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
_ALIASES = {"take_over": "takeover", "hang_up": "hangup", "stop": "stop_all"}
MAX_READ = 256 * 1024
SNAPSHOT_TAIL = 256 * 1024
KEEP_TRANSCRIPTS = 40


def calls_dir() -> Path:
    env = os.environ.get("JARVIS_CALLS_DIR", "").strip()
    if env:
        return Path(env)
    home = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(home) if home else Path.home() / ".jarvis") / "calls"


def _parse(lines: list[bytes]) -> list[dict]:
    out = []
    for raw in lines:
        try:
            ev = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            continue
        if isinstance(ev, dict) and ev.get("type") == "call":
            out.append(ev)
    return out


def read_events(since: int | None = None, limit: int = 200) -> dict:
    """Events after byte offset ``since``. ``since=None`` returns only the cursor
    at the end of the file (new subscribers should then call ``snapshot``).
    Never raises. Returns {"events": [...], "next": offset}."""
    path = calls_dir() / "events.jsonl"
    try:
        size = path.stat().st_size
    except OSError:
        return {"events": [], "next": 0}
    if since is None:
        return {"events": [], "next": size}
    if since < 0 or since > size:  # rotated / truncated: restart from the top
        since = 0
    try:
        with path.open("rb") as f:
            f.seek(since)
            data = f.read(MAX_READ)
    except OSError:
        return {"events": [], "next": since}
    end = data.rfind(b"\n")
    if end < 0:
        return {"events": [], "next": since}
    lines = data[: end + 1].splitlines()
    events = _parse(lines)
    nxt = since + end + 1
    limit = max(1, min(1000, int(limit)))
    if len(events) > limit:
        events = events[-limit:]
    return {"events": events, "next": nxt}


def snapshot() -> dict:
    """Current picture for a HUD that just opened: latest state of each live call,
    recent transcript lines, pending escalation, last summary. Never raises."""
    path = calls_dir() / "events.jsonl"
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - SNAPSHOT_TAIL))
            data = f.read()
    except OSError:
        return {"active": None, "transcript": [], "escalation": None, "next": 0}
    lines = data.splitlines()
    if size > SNAPSHOT_TAIL and lines:
        lines = lines[1:]  # first line may be cut
    evs = _parse(lines)
    state: dict | None = None
    transcript: list[dict] = []
    escalation: dict | None = None
    summary: dict | None = None
    for ev in evs:
        kind = ev.get("event")
        if kind == "state":
            if state is not None and ev.get("call_id") != state.get("call_id"):
                transcript, escalation = [], None
            state = ev
        elif kind == "transcript" and not ev.get("partial"):
            transcript.append(ev)
        elif kind == "escalation":
            escalation = ev
        elif kind == "summary":
            summary = ev
    active = state if state and state.get("state") != "ended" else None
    if active is None:
        escalation = None
    return {
        "active": active,
        "transcript": transcript[-KEEP_TRANSCRIPTS:] if active else [],
        "escalation": escalation,
        "last_summary": summary,
        "next": size,
    }


def queue_command(name: str, source: str = "hud") -> dict:
    """Append an owner command for the call runtime. Returns {"ok", "detail"}."""
    name = " ".join(str(name or "").lower().split()).replace(" ", "_")
    name = _ALIASES.get(name, name)
    if name not in COMMANDS:
        return {"ok": False, "detail": f"unknown command {name!r}"}
    d = calls_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        rec = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "ts": time.time(),
            "src": source,
        }
        with (d / "commands.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError as exc:
        return {"ok": False, "detail": f"could not queue command: {exc}"}
    return {"ok": True, "detail": "queued", "id": rec["id"]}
