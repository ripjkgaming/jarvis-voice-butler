"""Mail signals, reusing mail_watch_job's verdicts (via mail_log).

mail_watch_job already announces HIGH mail itself (spoken/toast through
notify) and offers email->calendar events, so this source only surfaces
NORMAL human mail as quiet HUD lines (urgency low), never interrupting.
"""

from __future__ import annotations

import time

from proactive.signals import Signal

WINDOW_S = 2 * 3600


def poll(now: float | None = None) -> list[Signal]:
    import mail_log

    now = time.time() if now is None else now
    out = []
    for e in mail_log.recent(kinds=("received",), since=now - WINDOW_S, limit=20):
        who = str(e.get("sender") or "someone")[:50]
        subject = str(e.get("subject") or "(no subject)")[:80]
        out.append(
            Signal(
                kind="mail",
                key=f"mail:{e.get('id') or e.get('ts')}",
                title=f"Mail from {who}: {subject}",
                detail=str(e.get("summary") or "")[:160],
                urgency="low",
                ts=float(e.get("ts") or now),
            )
        )
    return out
