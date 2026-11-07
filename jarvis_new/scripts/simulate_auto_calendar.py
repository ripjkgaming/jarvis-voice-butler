#!/usr/bin/env python3
"""Hermetic WhatsApp -> Calendar -> return-to-desk rehearsal (no network/model)."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import notify
import whatsapp_calendar as wc


def main():
    now = dt.datetime(2026, 11, 6, 12, tzinfo=dt.timezone.utc).timestamp()
    event = {
        "has_event": True,
        "title": "Dental appointment",
        "date": "2026-11-07",
        "start": "15:00",
        "end": "16:00",
        "all_day": False,
        "location": "Smile Clinic",
        "confidence": 0.98,
    }

    def runner(argv, **kwargs):
        output = (
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": json.dumps(event)},
                }
            )
            + "\n"
            + json.dumps({"type": "turn.completed", "usage": {}})
        )
        return subprocess.CompletedProcess(argv, 0, output, "")

    writes = []

    def insert(ev):
        writes.append(ev)
        return {"id": "simulation-event-1"}

    def notice(text, **kwargs):
        return notify.enqueue(
            {
                "ts": now,
                "kind": "calendar-added",
                "source": "whatsapp",
                "text": text,
                "speak_text": kwargs["speak_text"],
                "fingerprint": kwargs["fingerprint"],
            }
        )

    with (
        tempfile.TemporaryDirectory(prefix="jarvis-calendar-simulation-") as directory,
        patch.dict(
            os.environ,
            {
                "JARVIS_HOME": directory,
                "JARVIS_TZ": "Asia/Singapore",
                "JARVIS_LOCAL": "1",
                "JARVIS_CHATGPT": "1",
                "JARVIS_WHATSAPP_EVENTS": "1",
                "JARVIS_AUTO_CALENDAR": "1",
            },
        ),
        patch("chatgpt_cli.shutil.which", return_value="/simulation/codex"),
        patch("notify._in_quiet", return_value=False),
    ):
        message = {
            "text": "Your dental appointment is confirmed tomorrow at 3pm at Smile Clinic.",
            "sender": "Smile Clinic",
            "when": "12:00, 06/11/2026",
            "me": False,
        }
        staged = wc.ingest_messages("Smile Clinic", [message], now=now)
        duplicate = wc.ingest_messages("Smile Clinic", [message], now=now)
        results = wc.process_pending(
            now=now,
            runner=runner,
            lister=lambda *a, **k: [],
            inserter=insert,
            notify_fn=notice,
        )
        # Simulate restart by creating a new announcer reading the durable queue.
        announcer = notify.Announcer()
        spoken = []

        def drain(**kwargs):
            return notify.drain(
                **kwargs, speaker=lambda text, **kw: spoken.append(text) or True
            )

        away = announcer.tick(now=now, state="away", recent=False, do_drain=drain)
        for step in range(notify.SUSTAINED_POLLS):
            returned = announcer.tick(
                now=now + step + 1, state="active", recent=True, do_drain=drain
            )
        report = {
            "mode": "simulation",
            "real_messages_read": 0,
            "real_calendar_writes": 0,
            "staged": staged,
            "duplicate_staged": duplicate,
            "outcomes": results,
            "simulated_calendar_writes": len(writes),
            "away_state": away,
            "return_state": returned,
            "spoken": spoken,
            "remaining_queue": len(notify.load_queue()),
        }
        assert (
            staged == 1
            and duplicate == 0
            and len(writes) == 1
            and len(spoken) == 1
            and not notify.load_queue()
        )
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
