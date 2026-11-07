"""Always Aware sessions (PLAN.md §2): ticks collapse into spans.

Consecutive ``seen``/``unchanged`` ticks on the same subject extend one span
``{start, end, subject, app, apps, activity, importance, notes[]}`` in
``~/.jarvis/aware/spans/YYYY-MM-DD.json`` (0600). A gap longer than
``GAP_S`` (Sir away, ticks skipped) closes the span. Answers "what was I
doing at 3pm". Retention: 90 days.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time

from aware import log as alog
from aware import state as st

GAP_S = 15 * 60
KEEP_DAYS = 90
MAX_NOTES = 6
_LOCK = threading.Lock()


def spans_dir():
    return st.sub("spans")


def day_path(day: dt.date):
    return spans_dir() / f"{day.isoformat()}.json"


def load_day(day: dt.date) -> list[dict]:
    try:
        data = json.loads(day_path(day).read_text())
    except (OSError, ValueError):
        return []
    return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []


def save_day(day: dt.date, spans: list[dict]) -> bool:
    return st.write_private(day_path(day), json.dumps(spans))


def _norm(subject: str) -> str:
    return re.sub(r"\W+", " ", (subject or "").lower()).strip()


def merge(spans: list[dict], entry: dict, now: float) -> list[dict]:
    """Fold one tick line into a day's spans. Pure (returns a new list)."""
    spans = [dict(s) for s in spans]
    outcome = entry.get("outcome")
    if outcome not in ("seen", "unchanged"):
        return spans
    last = spans[-1] if spans else None
    fresh = last is not None and now - float(last.get("end") or 0) <= GAP_S
    if outcome == "unchanged":
        if fresh:
            last["end"] = now
        return spans
    subject = entry.get("subject") or entry.get("app") or "something"
    app = entry.get("app") or ""
    importance = int(entry.get("importance") or 0)
    summary = entry.get("summary") or ""
    if fresh and _norm(last.get("subject", "")) == _norm(subject):
        last["end"] = now
        last["importance"] = max(int(last.get("importance") or 0), importance)
        if app and app not in last.setdefault("apps", []):
            last["apps"].append(app)
        notes = last.setdefault("notes", [])
        if summary and (importance >= 2 or not notes) and summary not in notes:
            notes.append(summary)
            del notes[:-MAX_NOTES]
        last["ticks"] = int(last.get("ticks") or 1) + 1
        return spans
    spans.append(
        {
            "start": now,
            "end": now,
            "subject": subject,
            "app": app,
            "apps": [app] if app else [],
            "activity": entry.get("activity") or "other",
            "importance": importance,
            "notes": [summary] if summary else [],
            "ticks": 1,
        }
    )
    return spans


def update(entry: dict, now: float | None = None) -> None:
    now = time.time() if now is None else now
    day = dt.date.fromtimestamp(now)
    with _LOCK:
        spans = load_day(day)
        new = merge(spans, entry, now)
        if new != spans:
            save_day(day, new)


def around(ts: float, window: float = 15 * 60) -> list[dict]:
    """Spans overlapping [ts - window, ts + window], nearest first."""
    day = dt.date.fromtimestamp(ts)
    rows = []
    for d in (day - dt.timedelta(days=1), day, day + dt.timedelta(days=1)):
        rows += load_day(d)
    hits = [
        s
        for s in rows
        if float(s.get("start", 0)) <= ts + window
        and float(s.get("end", 0)) >= ts - window
    ]

    def distance(s):
        if s["start"] <= ts <= s["end"]:
            return 0.0
        return min(abs(s["start"] - ts), abs(s["end"] - ts))

    return sorted(hits, key=distance)


def _hm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def describe(span: dict) -> str:
    """'14:05-15:20 jarvis_new: debugging auth tests (Konsole, VS Code)'."""
    note = (span.get("notes") or [""])[-1].rstrip(".")
    apps = ", ".join(a for a in span.get("apps") or [] if a)
    text = f"{_hm(span['start'])}-{_hm(span['end'])} {span.get('subject', '')}"
    if note:
        text += f": {note}"
    if apps:
        text += f" ({apps})"
    return " ".join(text.split())[:240]


def spoken_at(ts: float, window: float = 15 * 60) -> str:
    """At most two sentences about what Sir was doing near ``ts``; '' if none."""
    hits = around(ts, window)
    if not hits:
        return ""
    first = hits[0]
    note = (first.get("notes") or [""])[-1].rstrip(".")
    when = f"{_hm(first['start'])} to {_hm(first['end'])}"
    line = f"From {when} you were on {first.get('subject') or 'something'}"
    line += f": {note}." if note else "."
    if len(hits) > 1:
        other = hits[1]
        line += f" Around then also {other.get('subject') or 'something else'}."
    return line


def prune(now: float | None = None, keep_days: int = KEEP_DAYS) -> int:
    now = time.time() if now is None else now
    return alog.prune_dir(spans_dir(), ".json", keep_days, now)
