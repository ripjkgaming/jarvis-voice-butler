"""Headless morning briefing: no room, no minutes, just a notification.

Runs from cron on school mornings: builds the same spoken bundle the
voice agent uses (weather + headlines + school + todos) and delivers
it as a desktop notification + log line. Zero LiveKit usage.

Delivery discipline (Phase 5): the raw bundle is shaped for speech
before it goes out — empty/placeholder sections are skipped, the
spoken text is capped at ~600 chars on sentence boundaries
(high-signal first, bundle order kept), and an identical repeat is
deduped against the last delivery. The full bundle still lands in
briefings.log for the record; the notification carries the shaped
text. Every skip is reason-coded to actions.log.

Install (weekdays 07:30):
    (crontab -l 2>/dev/null; echo "30 7 * * 1-5 cd /home/ripjk/jarvis-voice-butler/jarvis_new && JARVIS_LOCAL=1 .venv/bin/python src/briefing_job.py") | crontab -
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import re
import subprocess
from pathlib import Path

BRIEFING_LOG = Path.home() / ".jarvis" / "briefings.log"
BRIEFING_STATE = Path.home() / ".jarvis" / "briefings.state.json"
SPOKEN_CAP_CHARS = 600

# Sentences matching any of these carry no signal (failed/empty source)
# and are skipped so the spoken briefing stays dense.
_EMPTY_RE = re.compile(
    r"nothing to brief|unavailable|no (classes|headlines|todos|events)"
    r"|nothing (scheduled|on)|quiet morning|couldn.?t|could not|failed"
    r"|nothing new",
    re.IGNORECASE,
)


def shape_briefing_for_speech(say: str, limit: int = SPOKEN_CAP_CHARS) -> str:
    """Bundle -> dense spoken text: drop empty sections, cap at a boundary.

    Pure. Bundle order is kept (weather, headlines, school, todos);
    truncation stops at the last sentence that fits, so the cap never
    mid-sentences except when the FIRST sentence alone exceeds it.
    Returns "" when nothing worth saying survived.
    """
    sentences = re.split(r"(?<=[.!?])\s+", (say or "").strip())
    kept = [s.strip() for s in sentences if s.strip() and not _EMPTY_RE.search(s)]
    out: list[str] = []
    total = 0
    for sent in kept:
        if not out:
            out.append(sent[:limit])
            total = len(out[0])
        elif total + 1 + len(sent) <= limit:
            out.append(sent)
            total += 1 + len(sent)
        else:
            break
    return " ".join(out).strip()


def briefing_fingerprint(text: str) -> str:
    """Stable id for a shaped briefing (whitespace-normalized). Pure."""
    return hashlib.sha256(" ".join((text or "").split()).encode()).hexdigest()[:16]


def last_briefing_hash() -> str | None:
    """Hash of the last delivered briefing, or None. Never raises."""
    try:
        return json.loads(BRIEFING_STATE.read_text()).get("last_hash")
    except Exception:
        return None


def record_briefing_hash(fp: str) -> None:
    """Remember a delivery for dedupe. Never raises."""
    try:
        BRIEFING_STATE.parent.mkdir(parents=True, exist_ok=True)
        BRIEFING_STATE.write_text(json.dumps({"last_hash": fp}))
    except Exception:
        pass


def _note(reason: str, detail: str = "") -> None:
    try:
        from system import log_action

        log_action("briefing", f"{reason} {detail}".strip())
    except Exception:
        pass


async def build_briefing() -> str:
    """Compose the bundle headlessly. Needs JARVIS_LOCAL=1."""
    from system.inbox import InboxTools

    tools = InboxTools()
    return (await InboxTools.morning_briefing(tools, None))["say"]


def deliver(text: str, *, full_text: str = "") -> str:
    """Desktop notification (shaped text) + log (full bundle). Returns where."""
    try:
        BRIEFING_LOG.parent.mkdir(parents=True, exist_ok=True)
        with BRIEFING_LOG.open("a") as handle:
            handle.write(
                f"{datetime.datetime.now():%Y-%m-%d %H:%M} {full_text or text}\n"
            )
    except Exception:
        pass
    try:
        subprocess.run(
            ["notify-send", "Jarvis — morning briefing", text[:2000]],
            timeout=10,
            check=False,
        )
        return "notification+log"
    except Exception:
        return "log"


async def main() -> str:
    bundle = await build_briefing()
    shaped = shape_briefing_for_speech(bundle)
    if not shaped:
        _note("empty-briefing-skip")
        return "skipped-empty"
    fp = briefing_fingerprint(shaped)
    if fp == last_briefing_hash():
        _note("duplicate-skip", f"fp={fp}")
        return "skipped-duplicate"
    record_briefing_hash(fp)
    _note("delivered", f"chars={len(shaped)} fp={fp}")
    return deliver(shaped, full_text=bundle)


if __name__ == "__main__":
    print(asyncio.run(main()))
