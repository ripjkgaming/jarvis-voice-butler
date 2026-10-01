"""What Jarvis did with Sir's email, so he can ask about it later.

One JSON line per event in ~/.jarvis/mail_digest.jsonl:
    {"ts", "kind", "sender", "to", "subject", "level", "summary", "body"}
kind is one of: flagged (high priority), acked (automatic acknowledgement
sent), drafted (reply draft ready), sent (Sir-approved email or draft sent),
discarded (draft thrown away), received (normal human mail, for the HUD).

Append-only and fail-soft: logging never breaks the mail path.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

KINDS = ("flagged", "acked", "drafted", "sent", "discarded", "received")
KEEP_BYTES = 2_000_000


def path() -> Path:
    h = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(h) if h else Path.home() / ".jarvis") / "mail_digest.jsonl"


def record(kind: str, **fields) -> None:
    """Append one event. Never raises."""
    if kind not in KINDS:
        return
    entry = {"ts": float(fields.pop("ts", None) or time.time()), "kind": kind}
    for key in ("sender", "to", "subject", "level", "summary", "body", "id"):
        if fields.get(key):
            entry[key] = " ".join(str(fields[key]).split())[:400]
    with contextlib.suppress(Exception):
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > KEEP_BYTES:
            lines = p.read_text().splitlines()
            p.write_text("\n".join(lines[len(lines) // 2 :]) + "\n")
        with p.open("a") as f:
            f.write(json.dumps(entry) + "\n")


def recent(
    kinds: tuple[str, ...] | None = None,
    since: float = 0.0,
    who: str = "",
    limit: int = 50,
) -> list[dict]:
    """Newest-first events, optionally filtered by kind, time and person."""
    who = who.strip().lower()
    out: list[dict] = []
    try:
        lines = path().read_text().splitlines()
    except OSError:
        return out
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or float(e.get("ts") or 0) < since:
            continue
        if kinds and e.get("kind") not in kinds:
            continue
        if who and who not in f"{e.get('sender', '')} {e.get('to', '')}".lower():
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out
