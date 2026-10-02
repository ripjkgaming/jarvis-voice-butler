"""Exam timetable: exact times, paper lengths and the free time between papers.

Parses the mock-exam timetable markdown table (Date | Time | Subject / Paper (length))
and answers "when are my exams / how long is each / how much time between them"
deterministically, so the voice model never has to search the web for it.
Pure logic; the tool wrapper lives in system/daily.py.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

DATA_NAME = "exam_timetable.md"
DOWNLOAD_NAME = "mock-exam-timetable.md"

_TIME = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*(am|pm)\s*$", re.IGNORECASE)
_DUR = re.compile(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?", re.IGNORECASE)
_DATE = re.compile(r"(\d{1,2})\s+([A-Za-z]+)")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _minutes_of(text: str) -> int:
    """'1h 30m' -> 90, '55m' -> 55, '30m + 1h 15m' -> 105. Pure."""
    total = 0
    for part in text.split("+"):
        m = _DUR.fullmatch(part.strip())
        if m and (m.group(1) or m.group(2)):
            total += int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    return total


def parse_timetable(md: str, today: dt.date | None = None) -> list[dict]:
    """Markdown table -> [{date, start, end, title, minutes}] sorted by start. Pure.

    start/end are minutes after midnight. A blank Date cell continues the row above.
    """
    today = today or dt.date.today()
    rows: list[dict] = []
    current: dt.date | None = None
    for line in (md or "").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or set(cells[0]) <= set("-: ") and cells[0] != "":
            continue
        date_s, time_s, subj = cells[0], cells[1], "|".join(cells[2:])
        dm = _DATE.search(date_s)
        if dm and dm.group(2)[:3].lower() in _MONTHS:
            month = _MONTHS[dm.group(2)[:3].lower()]
            year = today.year
            cand = dt.date(year, month, int(dm.group(1)))
            if (today - cand).days > 180:
                cand = dt.date(year + 1, month, int(dm.group(1)))
            current = cand
        tm = _TIME.match(time_s)
        if not tm or current is None:
            continue
        hour = int(tm.group(1)) % 12 + (12 if tm.group(3).lower() == "pm" else 0)
        start = hour * 60 + int(tm.group(2))
        dur = re.search(r"\(([^)]*)\)\s*$", subj)
        minutes = _minutes_of(dur.group(1)) if dur else 0
        title = re.sub(r"\s*\([^)]*\)\s*$", "", subj).strip()
        rows.append(
            {"date": current, "start": start, "end": start + minutes,
             "title": title, "minutes": minutes}
        )
    rows.sort(key=lambda r: (r["date"], r["start"]))
    return rows


def fmt_time(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    suffix = "am" if h < 12 else "pm"
    return f"{(h % 12) or 12}:{m:02d}{suffix}"


def fmt_dur(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}h {m}m"
    return f"{h}h" if h else f"{m}m"


def describe_day(day: dt.date, papers: list[dict]) -> str:
    """One day, spoken: each paper's window and the free time before the next. Pure."""
    parts = [day.strftime("%a %d %b") + ":"]
    for i, p in enumerate(papers):
        parts.append(
            f"{p['title']} {fmt_time(p['start'])} to {fmt_time(p['end'])} ({fmt_dur(p['minutes'])})."
        )
        if i + 1 < len(papers):
            gap = papers[i + 1]["start"] - p["end"]
            parts.append(f"Then {fmt_dur(gap)} free." if gap > 0 else "Straight into the next one.")
    return " ".join(parts)


def by_day(rows: list[dict]) -> dict[dt.date, list[dict]]:
    out: dict[dt.date, list[dict]] = {}
    for r in rows:
        out.setdefault(r["date"], []).append(r)
    return out


def _now() -> dt.datetime:
    """Wall clock (tests pin it)."""
    return dt.datetime.now()


def answer(
    rows: list[dict],
    when: str = "today",
    today: dt.date | None = None,
    now: dt.datetime | None = None,
) -> str:
    """Spoken answer for today/tomorrow/next/all/a date/a weekday. Pure.

    Papers that already ended today are dropped (a 5pm "next exam" must not
    name the 8:30am paper). Live calls (no `today`) use the wall clock;
    passing only `today` keeps the whole day, for tests and date lookups.
    """
    if now is None and today is None:
        now = _now()
    today = today or now.date()
    now_min = now.hour * 60 + now.minute if now is not None else -1
    done = [
        r for r in rows if r["date"] == today and max(r["end"], r["start"]) <= now_min
    ]
    days = by_day([r for r in rows if r["date"] >= today and r not in done])
    w = (when or "today").strip().lower()
    if not days:
        if done:
            return "Today's papers are done and nothing is left on the timetable, Sir."
        return "No exams left on the timetable, Sir."
    if done and w in ("", "today"):
        finished = ", ".join(r["title"] for r in done)
        if today in days:
            return f"Already done: {finished}. Still to come: " + describe_day(
                today, days[today]
            )
        nxt = min(days)
        return f"Today's papers are done, Sir ({finished}). Next: " + describe_day(
            nxt, days[nxt]
        )
    if w in ("all", "week", "everything", "upcoming", "full"):
        return " ".join(describe_day(d, days[d]) for d in sorted(days))[:1400]
    target: dt.date | None = None
    if w in ("", "today"):
        target = today
    elif w.startswith("tomor"):
        target = today + dt.timedelta(days=1)
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", w):
        target = dt.date.fromisoformat(w)
    else:
        names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        for i, n in enumerate(names):
            if w.startswith(n[:3]):
                target = min((d for d in days if d.weekday() == i), default=None)
    if target is not None and target in days:
        return describe_day(target, days[target])
    after = [d for d in days if target is None or d > target]
    nxt = min(after) if after else min(days)
    lead = "Nothing then, Sir. " if target is not None else ""
    return lead + "Next: " + describe_day(nxt, days[nxt])


def load(data_dir: Path, downloads: Path | None = None, today: dt.date | None = None) -> list[dict]:
    """Rows from the saved copy, adopting the Downloads file the first time. []."""
    saved = data_dir / DATA_NAME
    if not saved.exists() and downloads is not None:
        src = downloads / DOWNLOAD_NAME
        if src.is_file():
            try:
                data_dir.mkdir(parents=True, exist_ok=True)
                saved.write_text(src.read_text())
            except OSError:
                pass
    try:
        return parse_timetable(saved.read_text(), today)
    except OSError:
        return []
