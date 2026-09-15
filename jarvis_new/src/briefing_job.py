"""Headless morning briefing: no room, no minutes, just a notification.

Runs from cron on school mornings: builds the same spoken bundle the
voice agent uses (weather + headlines + school + todos) and delivers
it as a desktop notification + log line. Zero LiveKit usage.

Install (weekdays 07:30):
    (crontab -l 2>/dev/null; echo "30 7 * * 1-5 cd /home/ripjk/jarvis-voice-butler/jarvis_new && JARVIS_LOCAL=1 .venv/bin/python src/briefing_job.py") | crontab -
"""

from __future__ import annotations

import asyncio
import datetime
import subprocess
from pathlib import Path

BRIEFING_LOG = Path.home() / ".jarvis" / "briefings.log"


async def build_briefing() -> str:
    """Compose the bundle headlessly. Needs JARVIS_LOCAL=1."""
    from system.inbox import InboxTools

    tools = InboxTools()
    return (await InboxTools.morning_briefing(tools, None))["say"]


def deliver(text: str) -> str:
    """Desktop notification + log. Returns where it went."""
    try:
        BRIEFING_LOG.parent.mkdir(parents=True, exist_ok=True)
        with BRIEFING_LOG.open("a") as handle:
            handle.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M} {text}\n")
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
    return deliver(await build_briefing())


if __name__ == "__main__":
    print(asyncio.run(main()))
