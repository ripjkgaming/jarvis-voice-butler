"""Calendar signals: an event starts in 10 min / 5 min / now; clashes.

Reads Google Calendar through google_api.calendar_list for the account in
JARVIS_CALENDAR_ACCOUNT (default "personal"; JARVIS_CALENDAR_ID picks a
calendar, default primary). The fetch is cached for FETCH_TTL_S so the
30-second engine loop costs one API call every few minutes.
"""

from __future__ import annotations

import datetime as dt
import os
import time

from proactive.signals import Signal

FETCH_TTL_S = 240.0
LOOKAHEAD_H = 12
#: (stage name, seconds-before-start window low, high, urgency)
STAGES = (
    ("now", -60, 60, "high"),
    ("5min", 60, 5 * 60 + 30, "high"),
    ("10min", 5 * 60 + 30, 10 * 60 + 30, "normal"),
)
_cache: dict = {"at": 0.0, "events": []}


def calendar_account() -> str:
    return os.environ.get("JARVIS_CALENDAR_ACCOUNT", "").strip() or "personal"


def calendar_id() -> str:
    return os.environ.get("JARVIS_CALENDAR_ID", "").strip() or "primary"


def _rfc(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(now: float, lister=None) -> list[dict]:
    """Events from now to LOOKAHEAD_H ahead, cached. Raises on API failure."""
    if (
        now - _cache["at"] < FETCH_TTL_S
        and _cache["events"] is not None
        and lister is None
    ):
        return _cache["events"]
    if lister is None:
        import google_api

        lister = google_api.calendar_list
    events = lister(
        _rfc(now - 60 * 60),
        _rfc(now + LOOKAHEAD_H * 3600),
        calendar_id=calendar_id(),
        account=calendar_account(),
    )
    _cache.update(at=now, events=list(events or []))
    return _cache["events"]


def _start_end(e: dict) -> tuple[float, float] | None:
    """Timed events only -> (start_ts, end_ts). All-day events -> None. Pure."""
    try:
        s = (e.get("start") or {}).get("dateTime")
        f = (e.get("end") or {}).get("dateTime") or s
        if not s:
            return None
        a = dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        b = dt.datetime.fromisoformat(f.replace("Z", "+00:00")).timestamp()
        return a, max(a, b)
    except (ValueError, AttributeError, TypeError):
        return None


def _clock(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M")


def signals_for(events: list[dict], now: float) -> list[Signal]:
    """Start reminders + clashes from a list of Calendar events. Pure."""
    out: list[Signal] = []
    timed = []
    for e in events:
        se = _start_end(e)
        if se is None or str(e.get("status")) == "cancelled":
            continue
        start, end = se
        title = str(e.get("summary") or "an event")[:80]
        eid = str(e.get("id") or f"{title}@{start}")
        timed.append((start, end, title, eid))
        before = start - now
        where = str(e.get("location") or "")[:60]
        for stage, lo, hi, urgency in STAGES:
            if lo <= before < hi:
                if stage == "now":
                    say = f"{title} is starting now, Sir."
                else:
                    mins = max(1, round(before / 60))
                    say = f"{title} starts in {mins} minutes, Sir" + (
                        f", at {where}." if where else "."
                    )
                out.append(
                    Signal(
                        kind="calendar",
                        key=f"cal:{eid}:{stage}",
                        title=say,
                        detail=f"{title} at {_clock(start)}"
                        + (f" ({where})" if where else ""),
                        urgency=urgency,
                        ts=now,
                        action={"event_id": eid},
                    )
                )
                break
    timed.sort()
    for i, (s1, e1, t1, id1) in enumerate(timed):
        for s2, _e2, t2, id2 in timed[i + 1 :]:
            if s2 >= e1:
                break
            if s1 < now - 3600:
                continue
            out.append(
                Signal(
                    kind="calendar",
                    key=f"cal-clash:{id1}:{id2}",
                    title=f"Heads up, Sir: {t1} and {t2} overlap at {_clock(s2)}.",
                    detail=f"{t1} {_clock(s1)}-{_clock(e1)} overlaps {t2} from {_clock(s2)}",
                    urgency="normal",
                    ts=now,
                )
            )
    return out


def poll(now: float | None = None, lister=None) -> list[Signal]:
    now = time.time() if now is None else now
    return signals_for(fetch(now, lister), now)
