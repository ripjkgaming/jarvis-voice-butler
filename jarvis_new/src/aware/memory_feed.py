"""Always Aware -> long-term memory (PLAN.md §2 "Memory feed").

Hourly, closed spans with importance >= 2 or lasting >= 15 min are written
into ``Memory/Daily/<date>.md`` under ``## Seen`` via ``memory._append``
(which redacts). ``memory.recall`` and ``memory.day_summary`` then find them
with no second index. Already-fed spans are remembered in
``~/.jarvis/aware/memory_feed.json`` so nothing is written twice.
"""

from __future__ import annotations

import datetime as dt
import json
import time

from aware import spans as asp
from aware import state as st

MIN_SPAN_S = 15 * 60
MIN_IMPORTANCE = 2


def fed_path():
    return st.aware_dir() / "memory_feed.json"


def _fed() -> dict:
    try:
        data = json.loads(fed_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def key(span: dict) -> str:
    return f"{float(span.get('start', 0)):.0f}"


def qualifies(span: dict) -> bool:
    dur = float(span.get("end", 0)) - float(span.get("start", 0))
    return int(span.get("importance") or 0) >= MIN_IMPORTANCE or dur >= MIN_SPAN_S


def closed(span: dict, is_last: bool, now: float) -> bool:
    return not is_last or now - float(span.get("end", 0)) > asp.GAP_S


def pending(spans: list[dict], fed: dict, now: float) -> list[dict]:
    """Spans to write now. Pure."""
    out = []
    for i, span in enumerate(spans):
        if key(span) in fed or not qualifies(span):
            continue
        if closed(span, i == len(spans) - 1, now):
            out.append(span)
    return out


def feed(now: float | None = None, memory_mod=None) -> int:
    """Write qualifying closed spans of yesterday and today. Returns count."""
    now = time.time() if now is None else now
    if memory_mod is None:
        import memory as memory_mod
    if not memory_mod.enabled():
        return 0
    fed = _fed()
    today = dt.date.fromtimestamp(now)
    written = 0
    for day in (today - dt.timedelta(days=1), today):
        todo = pending(asp.load_day(day), fed, now)
        if not todo:
            continue
        body = "## Seen\n" + "\n".join(f"- {asp.describe(s)}" for s in todo)
        header = f"# {day.strftime('%A %d %B %Y')}"
        if not memory_mod._append(memory_mod.daily_path(day), body, header):
            continue
        for s in todo:
            fed[key(s)] = now
        written += len(todo)
    if written:
        cutoff = now - 3 * 86_400
        fed = {k: v for k, v in fed.items() if float(v) >= cutoff}
        st.write_private(fed_path(), json.dumps(fed))
    return written
