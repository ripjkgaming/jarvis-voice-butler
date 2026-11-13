"""Away log: what Jarvis did for Sir while he was away (asleep).

Append-only JSON Lines at $JARVIS_HOME/away_log.jsonl (default
~/.jarvis/away_log.jsonl). Two row shapes:

    {"ts": 1731400000.0, "type": "away_start"}
    {"ts": 1731400300.0, "type": "event", "kind": "email_replied",
     "summary": "...", "detail": {...} | null}

Writers take an exclusive flock, so the voice agent and the away worker can
both append safely. Readers skip corrupt or unknown rows instead of failing.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import re
import threading
import time
from pathlib import Path

KINDS = (
    "email_replied",
    "email_drafted",
    "calendar_scheduled",
    "whatsapp_replied",
    "task_done",
    "deferred_decision",
    "other",
)
SUMMARY_MAX = 300

_LOCK = threading.Lock()
_NUMBER_WORDS = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
)


def log_path() -> Path:
    """Where the log lives: $JARVIS_HOME/away_log.jsonl. Read per call."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(home) if home else Path.home() / ".jarvis") / "away_log.jsonl"


def _clean(text: object, limit: int = SUMMARY_MAX) -> str:
    return " ".join(str(text or "").split())[:limit]


def _append(row: dict) -> dict:
    """Write one JSON line under the in-process lock and an flock. Raises OSError."""
    line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    return row


def _now(now: float | None) -> float:
    return time.time() if now is None else float(now)


def start_away(now: float | None = None) -> dict:
    """Mark the start of an away session. Raises OSError if the write fails."""
    return _append({"ts": _now(now), "type": "away_start"})


def end_away(now: float | None = None) -> dict:
    """Mark the end of an away session. Raises OSError if the write fails."""
    return _append({"ts": _now(now), "type": "away_end"})


def log_event(
    kind: str,
    summary: str,
    detail: object = None,
    *,
    now: float | None = None,
) -> dict:
    """Append one away event and return the row written.

    Raises ValueError for an unknown kind (a programming error) and OSError
    when the write fails, so the caller can tell a lost record apart from a
    success.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown away_log kind: {kind!r}")
    return _append(
        {
            "ts": _now(now),
            "type": "event",
            "kind": kind,
            "summary": _clean(summary),
            "detail": detail,
        }
    )


def _rows() -> list[dict]:
    """All readable rows in file order. Corrupt or foreign lines are skipped."""
    try:
        text = log_path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        ts = row.get("ts")
        if not isinstance(ts, (int, float)) or isinstance(ts, bool):
            continue
        if not math.isfinite(float(ts)):
            continue
        kind = row.get("type")
        if (
            kind == "away_start"
            or kind == "away_end"
            or (kind == "event" and row.get("kind") in KINDS)
        ):
            out.append(row)
    return out


def away_report(since: float | None = None) -> dict:
    """Summarise events since a timestamp.

    since=None means "since the most recent away_start" (or all time when no
    session was ever started). Returns:
        {"since": float, "counts": {kind: n} (every kind present),
         "items": [{"ts", "kind", "summary", "detail"}, ...] oldest first,
         "deferred_decisions": [the deferred_decision items]}
    """
    rows = _rows()
    if since is None:
        starts = [float(r["ts"]) for r in rows if r.get("type") == "away_start"]
        since = max(starts) if starts else 0.0
    since = float(since)
    events = sorted(
        (r for r in rows if r.get("type") == "event" and float(r["ts"]) >= since),
        key=lambda r: float(r["ts"]),
    )
    counts = dict.fromkeys(KINDS, 0)
    items = []
    for row in events:
        counts[row["kind"]] += 1
        items.append(
            {
                "ts": float(row["ts"]),
                "kind": row["kind"],
                "summary": _clean(row.get("summary")),
                "detail": row.get("detail"),
            }
        )
    return {
        "since": since,
        "counts": counts,
        "items": items,
        "deferred_decisions": [i for i in items if i["kind"] == "deferred_decision"],
    }


def _count_phrase(n: int, singular: str, plural: str) -> str:
    word = _NUMBER_WORDS[n] if 0 <= n < len(_NUMBER_WORDS) else str(n)
    return f"{word} {singular if n == 1 else plural}"


_PHRASES = (
    ("email_replied", "replied to", "email", "emails"),
    ("email_drafted", "drafted", "email reply", "email replies"),
    ("calendar_scheduled", "scheduled", "calendar event", "calendar events"),
    ("whatsapp_replied", "answered", "WhatsApp message", "WhatsApp messages"),
    ("task_done", "finished", "task", "tasks"),
    ("other", "handled", "other item", "other items"),
)


def _speakable(text: object, limit: int) -> str:
    """Plain words for the voice: no markdown or markup symbols. Pure."""
    cleaned = re.sub(r"[*#_`~|<>\[\]{}\\]", " ", str(text or ""))
    return _clean(cleaned, limit)


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def spoken_summary(report: dict) -> str:
    """A short, speakable recap of an away_report. Pure.

    Groups done work by kind, then lists deferred decisions last so Sir hears
    what needs him. Never empty: a quiet stretch gets its own sentence.
    """
    counts = report.get("counts") or {}
    done = []
    for kind, verb, singular, plural in _PHRASES:
        n = int(counts.get(kind) or 0)
        if n:
            done.append(f"{verb} {_count_phrase(n, singular, plural)}")
    deferred = [
        _speakable(item.get("summary"), 160) or "an item"
        for item in report.get("deferred_decisions") or []
    ]
    pieces = []
    if done:
        pieces.append(f"While you were away, I {_join(done)}.")
    else:
        pieces.append("Nothing needed doing while you were away.")
    if deferred:
        count = len(deferred)
        if count == 1:
            pieces.append(f"One thing needs your decision, Sir: {deferred[0]}.")
        else:
            ordinals = ("First", "Second", "Third", "Fourth", "Fifth")
            listed = [
                f"{ordinals[i] if i < len(ordinals) else 'Next'}, {text.rstrip('.')}."
                for i, text in enumerate(deferred[:5])
            ]
            head = (
                f"{_count_phrase(count, 'thing', 'things').capitalize()} "
                "need your decision, Sir."
            )
            if count > 5:
                listed.append(f"And {count - 5} more are in the log.")
            pieces.append(head + " " + " ".join(listed))
    return re.sub(r"\s+", " ", " ".join(pieces)).strip()
