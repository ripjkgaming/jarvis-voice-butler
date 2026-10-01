"""Job signals: a test run / build / terminal job finished (src/jobs.py)."""

from __future__ import annotations

import time

from proactive.signals import Signal

WINDOW_S = 3600


def poll(now: float | None = None) -> list[Signal]:
    import jobs

    now = time.time() if now is None else now
    out = []
    for job in jobs.recent():
        status = job.get("status")
        finished = float(job.get("finished") or 0)
        if status not in ("done", "failed") or now - finished > WINDOW_S:
            continue
        title = str(job.get("title") or "The job")[:80]
        fails = int(job.get("failures") or 0)
        if status == "done" and not fails:
            say, urgency = f"{title} finished cleanly, Sir.", "normal"
        else:
            n = f"{fails} failure{'s' if fails != 1 else ''}" if fails else "it failed"
            say, urgency = f"{title} finished, Sir: {n}.", "high"
        out.append(
            Signal(
                kind="job",
                key=f"job:{job.get('id')}:{status}",
                title=say,
                detail=str(job.get("summary") or "")[:200],
                urgency=urgency,
                ts=finished,
                action={"job_id": job.get("id")},
            )
        )
    return out
