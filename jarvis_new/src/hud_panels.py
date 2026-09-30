"""Voice-driven HUD panels (IRONMAN_SPEC §6): "show mail", "hide calendar".

Which panels are open lives in ~/.jarvis/hud_panels.json; the HUD polls
the bridge's GET /panels and draws the open ones (frontend
components/hud/hud-panels.tsx). Each panel's data is gathered here from
the existing stores, fail-soft: a broken source shows as empty, never an
error. No cursor, no orb: panels only open and close by voice.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import time
from pathlib import Path

PANELS = ("calendar", "mail", "tasks", "drafts", "suggestions", "exams", "system")
ALIASES = {
    "email": "mail",
    "emails": "mail",
    "inbox": "mail",
    "schedule": "calendar",
    "agenda": "calendar",
    "jobs": "tasks",
    "task": "tasks",
    "draft": "drafts",
    "replies": "drafts",
    "suggestion": "suggestions",
    "exam": "exams",
    "stats": "system",
    "system stats": "system",
}


def _home() -> Path:
    h = os.environ.get("JARVIS_HOME", "").strip()
    return Path(h) if h else Path.home() / ".jarvis"


def state_path() -> Path:
    return _home() / "hud_panels.json"


def normalize(name: str) -> str | None:
    n = " ".join(str(name or "").lower().split()).removesuffix(" panel")
    n = ALIASES.get(n, n)
    return n if n in PANELS else None


def visible() -> list[str]:
    try:
        data = json.loads(state_path().read_text())
        return [p for p in data.get("visible", []) if p in PANELS]
    except (OSError, ValueError, AttributeError):
        return []


def _save(names: list[str]) -> None:
    with contextlib.suppress(OSError):
        state_path().parent.mkdir(parents=True, exist_ok=True)
        state_path().write_text(json.dumps({"visible": names, "ts": time.time()}))


def show(name: str) -> list[str]:
    if name == "all":
        _save(list(PANELS))
        return list(PANELS)
    names = [p for p in visible() if p != name] + [name]
    _save(names)
    return names


def hide(name: str) -> list[str]:
    names = [] if name == "all" else [p for p in visible() if p != name]
    _save(names)
    return names


# --- data per panel (each fail-soft) ---


def _calendar(now: float) -> dict:
    from proactive.sources import calendar_source

    rows = []
    for e in calendar_source.fetch(now):
        se = calendar_source._start_end(e)
        start = (e.get("start") or {}).get("date") if se is None else None
        rows.append(
            {
                "title": str(e.get("summary") or "")[:60],
                "when": dt.datetime.fromtimestamp(se[0]).strftime("%a %H:%M")
                if se
                else str(start or ""),
                "where": str(e.get("location") or "")[:40],
            }
        )
    return {"items": rows[:8]}


def _mail(now: float) -> dict:
    import mail_log

    rows = mail_log.recent(
        kinds=("flagged", "received", "acked"), since=now - 2 * 86400, limit=8
    )
    return {
        "items": [
            {
                "who": str(e.get("sender") or e.get("to") or "")[:40],
                "subject": str(e.get("subject") or "")[:70],
                "kind": e.get("kind"),
            }
            for e in rows
        ]
    }


def _tasks(now: float) -> dict:
    import agent_tasks
    import jobs

    items = []
    for t in agent_tasks.all_tasks()[-4:][::-1]:
        steps = t.get("steps") or []
        done = sum(1 for s in steps if s.get("status") == "done")
        items.append(
            {
                "title": str(t.get("title") or "")[:60],
                "status": t.get("status"),
                "progress": f"{done}/{len(steps)}",
            }
        )
    for j in jobs.recent(6):
        items.append(
            {
                "title": str(j.get("title") or "")[:60],
                "status": j.get("status"),
                "progress": str(j.get("summary") or "")[:40],
            }
        )
    return {"items": items[:8]}


def _drafts(now: float) -> dict:
    import draft_engine

    rows = [d for s in ("pending", "announced") for d in draft_engine.list_drafts(s)]
    return {
        "items": [
            {
                "to": str(d.get("sender") or "")[:40],
                "subject": str(d.get("subject") or "")[:70],
            }
            for d in rows[:8]
        ]
    }


def _suggestions(now: float) -> dict:
    import event_extractor

    return {
        "items": [
            {
                "title": r["event"]["title"],
                "when": event_extractor.when_text(r["event"]),
            }
            for r in event_extractor.pending()[:8]
        ]
    }


def _exams(now: float) -> dict:
    import exams

    today = dt.date.fromtimestamp(now)
    items = []
    for e in exams.upcoming(today, 14)[:8]:
        day = dt.date.fromisoformat(e["date"])
        days = exams.days_until(e, today)
        until = "today" if days == 0 else ("tomorrow" if days == 1 else f"in {days}d")
        when = day.strftime("%a %d %b") + (f" {e['start']}" if e.get("start") else "")
        items.append({"title": e["title"], "when": f"{when} · {until}"})
    return {"items": items}


def _system(now: float) -> dict:
    import shutil

    total, _used, free = shutil.disk_usage(str(Path.home()))
    load = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0
    return {
        "disk_free_gb": round(free / 1e9, 1),
        "disk_free_pct": round(free / total * 100, 1) if total else 0,
        "load_1m": round(load, 2),
    }


GATHER = {
    "calendar": _calendar,
    "mail": _mail,
    "tasks": _tasks,
    "drafts": _drafts,
    "suggestions": _suggestions,
    "exams": _exams,
    "system": _system,
}


def payload(now: float | None = None) -> dict:
    """What GET /panels returns: open panels, in order, with their data."""
    now = time.time() if now is None else now
    out = {}
    for name in visible():
        try:
            out[name] = GATHER[name](now)
        except Exception:
            out[name] = {"items": [], "error": "unavailable"}
    return {"ok": True, "visible": visible(), "panels": out}
