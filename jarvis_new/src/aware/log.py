"""Always Aware tick log (PLAN.md §2): ``~/.jarvis/aware/log/YYYY-MM-DD.jsonl``.

One 0600 JSON line per tick, skips included (``{ts, outcome: "skip",
reason}``). The window title is stored hashed; the raw title is kept only
for importance >= 2, after ``memory.redact``. Query strings never reach
the log (``url_host`` is host + path). Retention: 14 days.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import time

from aware import state as st

KEEP_DAYS = 14
OUTCOMES = ("seen", "unchanged", "skip", "error")


def log_dir():
    return st.sub("log")


def day_path(day: dt.date):
    return log_dir() / f"{day.isoformat()}.jsonl"


def title_hash(title: str) -> str:
    return hashlib.sha256((title or "").encode()).hexdigest()[:12] if title else ""


def _redact(text: str) -> str:
    try:
        import memory

        return memory.redact(text)
    except Exception:
        return ""  # cannot redact: do not keep raw text


def tick_entry(
    *,
    tick_id: str,
    interval_s: float,
    obs: dict,
    app: str = "",
    title: str = "",
    url_host: str = "",
    model: str = "",
    tokens: int = 0,
    bytes_up: int = 0,
    latency_ms: int = 0,
    monitors: list | None = None,
    opportunities: list | None = None,
) -> dict:
    """The ``seen`` tick line from a parsed observation. Pure."""
    importance = int(obs.get("importance") or 0)
    entry = {
        "tick_id": tick_id,
        "interval_s": round(float(interval_s or 0)),
        "outcome": "seen",
        "app": (app or obs.get("app") or "")[:80],
        "title_hash": title_hash(title),
        "subject": obs.get("subject", ""),
        "url_host": url_host[:200],
        "activity": obs.get("activity", "other"),
        "summary": obs.get("summary", ""),
        "importance": importance,
        "signals": list(obs.get("signals") or []),
        "opportunities": [
            {
                "kind": o.get("kind", ""),
                "title": o.get("title", ""),
                "self_feasibility": o.get("self_feasibility", 0.0),
                "gate": o.get("gate", "pending"),
            }
            for o in (opportunities or [])
        ],
        "model": model,
        "tokens": int(tokens or 0),
        "bytes_up": int(bytes_up or 0),
        "latency_ms": int(latency_ms or 0),
    }
    if monitors is not None:
        entry["monitors"] = list(monitors)
    if obs.get("sensitive"):
        entry["sensitive"] = True
        entry["opportunities"] = []
    if importance >= 2 and title:
        entry["title"] = _redact(title)[:160]
    return entry


def write(entry: dict, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    row = {"ts": round(now, 3), **entry}
    if row.get("outcome") not in OUTCOMES:
        row["outcome"] = "error"
    day = dt.date.fromtimestamp(now)
    st.append_private(day_path(day), json.dumps(row, default=str))
    return row


def skip(reason: str, now: float | None = None) -> dict:
    return write({"outcome": "skip", "reason": str(reason)[:60]}, now)


def error(reason: str, now: float | None = None, **extra) -> dict:
    return write({"outcome": "error", "reason": str(reason)[:120], **extra}, now)


def read_day(day: dt.date) -> list[dict]:
    out = []
    with contextlib.suppress(OSError):
        for line in day_path(day).read_text().splitlines():
            with contextlib.suppress(ValueError):
                row = json.loads(line)
                if isinstance(row, dict):
                    out.append(row)
    return out


def recent(
    n: int = 3, outcome: str | None = "seen", now: float | None = None, days: int = 2
) -> list[dict]:
    """Last ``n`` lines (oldest first) with that outcome, from the last days."""
    now = time.time() if now is None else now
    today = dt.date.fromtimestamp(now)
    rows: list[dict] = []
    for back in range(days - 1, -1, -1):
        rows += read_day(today - dt.timedelta(days=back))
    if outcome:
        rows = [r for r in rows if r.get("outcome") == outcome]
    return rows[-n:] if n > 0 else []


def prune_dir(directory, suffix: str, keep_days: int, now: float) -> int:
    """Delete YYYY-MM-DD<suffix> files older than ``keep_days``. Never raises."""
    cutoff = dt.date.fromtimestamp(now) - dt.timedelta(days=keep_days)
    removed = 0
    with contextlib.suppress(OSError):
        for path in directory.glob(f"*{suffix}"):
            try:
                day = dt.date.fromisoformat(path.name[: -len(suffix)])
            except ValueError:
                continue
            if day < cutoff:
                with contextlib.suppress(OSError):
                    path.unlink()
                    removed += 1
    return removed


def prune(now: float | None = None, keep_days: int = KEEP_DAYS) -> int:
    now = time.time() if now is None else now
    return prune_dir(log_dir(), ".jsonl", keep_days, now)


def spoken_recent(rows: list[dict]) -> str:
    """'What did you see': at most two sentences over the last seen ticks."""
    seen = [r for r in rows if r.get("summary")]
    if not seen:
        return "I haven't seen anything yet, Sir."
    last = seen[-1]
    when = time.strftime("%H:%M", time.localtime(float(last.get("ts") or 0)))
    first = f"At {when}: {last['summary'].rstrip('.')}."
    earlier = [r["summary"].rstrip(".") for r in seen[:-1]][-2:]
    if earlier:
        return f"{first} Before that: {'; '.join(earlier)}."
    return first
