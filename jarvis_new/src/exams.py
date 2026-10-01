"""Sir's exam schedule: one local list Jarvis can answer from offline.

Sources, merged by exams():
- ~/.jarvis/exams.json: added by voice (exam_schedule add) and saved
  automatically when a schedule file is imported to Google Calendar.
- jarvis_new/data/exams.json: Sir's timetable shipped with the repo
  (JARVIS_EXAM_SCHEDULE points elsewhere).
- the IGCSE study plan's "exams" section (~/jarvis/data/study_plan.json).

Answers "when's my next exam", "when's physics", "what exams this week",
"how many days until chemistry"; feeds the briefing and the catch-up recap;
and start_thread() reminds Sir the evening before and the morning of each
exam (JARVIS_EXAM_REMIND=0 switches reminders off).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import re
from pathlib import Path

EXAM_WORDS = re.compile(
    r"\b(exam|exams|paper|mock|mocks|test|quiz|assessment|igcse|practical|"
    r"oral|listening|finals?|midterm|board)\b",
    re.I,
)
EVENING_HOUR = 18
MORNING_HOUR = 6


def home() -> Path:
    h = os.environ.get("JARVIS_HOME", "").strip()
    return Path(h) if h else Path.home() / ".jarvis"


def store_path() -> Path:
    return home() / "exams.json"


def repo_schedule_path() -> Path:
    """The timetable shipped with the repo (jarvis_new/data/exams.json)."""
    return Path(
        os.environ.get("JARVIS_EXAM_SCHEDULE", "").strip()
        or Path(__file__).resolve().parent.parent / "data" / "exams.json"
    )


def _repo_exams() -> list[dict]:
    try:
        data = json.loads(repo_schedule_path().read_text())
    except (OSError, ValueError):
        return []
    rows = data.get("exams", []) if isinstance(data, dict) else []
    return [
        c for c in (_clean(r, "timetable") for r in rows if isinstance(r, dict)) if c
    ]


def study_plan_path() -> Path:
    return Path(
        os.environ.get("JARVIS_STUDY_PLAN", "").strip()
        or Path.home() / "jarvis" / "data" / "study_plan.json"
    )


def _today() -> dt.date:
    return dt.date.today()


def _clean(e: dict, source: str = "") -> dict | None:
    """Normalise one exam record, or None when it has no valid date/title."""
    title = " ".join(str(e.get("title") or "").split())[:120]
    date = str(e.get("date") or "").strip()[:10]
    try:
        dt.date.fromisoformat(date)
    except ValueError:
        return None
    if not title:
        return None
    out = {"title": title, "date": date}
    for key in ("start", "end"):
        v = str(e.get(key) or "").strip()
        out[key] = v if re.fullmatch(r"\d{1,2}:\d{2}", v) else ""
    for key in ("location", "notes", "subject"):
        out[key] = " ".join(str(e.get(key) or "").split())[:160]
    out["source"] = source or str(e.get("source") or "manual")
    return out


def _key(e: dict) -> tuple[str, str]:
    return (re.sub(r"\W+", " ", e["title"].lower()).strip(), e["date"])


def load() -> list[dict]:
    try:
        data = json.loads(store_path().read_text())
    except (OSError, ValueError):
        return []
    rows = data.get("exams", []) if isinstance(data, dict) else []
    return [
        c
        for c in (_clean(r, r.get("source", "")) for r in rows if isinstance(r, dict))
        if c
    ]


def save(rows: list[dict]) -> bool:
    try:
        store_path().parent.mkdir(parents=True, exist_ok=True)
        tmp = store_path().with_suffix(".tmp")
        tmp.write_text(json.dumps({"exams": rows}, indent=2))
        os.replace(tmp, store_path())
        return True
    except OSError:
        return False


def add(entry: dict, source: str = "manual") -> str:
    """Add or update one exam -> 'added' | 'updated' | 'invalid' | 'failed'."""
    clean = _clean(entry, source)
    if clean is None:
        return "invalid"
    rows = load()
    for i, r in enumerate(rows):
        if _key(r) == _key(clean):
            rows[i] = {**r, **{k: v for k, v in clean.items() if v}}
            return "updated" if save(rows) else "failed"
    rows.append(clean)
    return "added" if save(rows) else "failed"


def remove(query: str, date: str = "") -> list[dict]:
    """Remove stored exams whose title contains `query` (and on `date`)."""
    q = " ".join(str(query or "").lower().split())
    if not q:
        return []
    rows = load()
    gone = [
        r for r in rows if q in r["title"].lower() and (not date or r["date"] == date)
    ]
    if gone:
        save([r for r in rows if r not in gone])
    return gone


def import_events(events: list[dict], source: str = "calendar-import") -> int:
    """Save the exam-like entries of an imported schedule. Returns how many.

    When nothing in the file looks like an exam by name, everything is kept:
    the importer is only used for exam/mock/school schedules.
    """
    cleaned = [c for c in (_clean(e, source) for e in events or []) if c]
    exam_like = [c for c in cleaned if EXAM_WORDS.search(f"{c['title']} {c['notes']}")]
    chosen = exam_like or cleaned
    n = 0
    for c in chosen:
        if add(c, source) in ("added", "updated"):
            n += 1
    return n


def _plan_exams() -> list[dict]:
    try:
        plan = json.loads(study_plan_path().read_text())
    except (OSError, ValueError):
        return []
    out = []
    for date, v in (plan.get("exams") or {}).items():
        if not isinstance(v, dict):
            continue
        papers = str(v.get("papers") or v.get("status") or "Exam")
        c = _clean(
            {"title": papers, "date": date, "notes": v.get("status", "")}, "study-plan"
        )
        if c:
            out.append(c)
    return out


def exams() -> list[dict]:
    """Every known exam, sorted by date then start; duplicates merged."""
    seen: dict[tuple[str, str], dict] = {}
    # Sir's own additions win over the shipped timetable, then the study plan.
    for e in [*load(), *_repo_exams(), *_plan_exams()]:
        seen.setdefault(_key(e), e)
    return sorted(seen.values(), key=lambda e: (e["date"], e["start"] or "99:99"))


def upcoming(today: dt.date | None = None, days: int | None = None) -> list[dict]:
    today = today or _today()
    last = (today + dt.timedelta(days=days)).isoformat() if days is not None else "9999"
    return [e for e in exams() if today.isoformat() <= e["date"] <= last]


def next_exam(today: dt.date | None = None) -> dict | None:
    rows = upcoming(today)
    return rows[0] if rows else None


def find(
    subject: str, today: dt.date | None = None, include_past: bool = False
) -> list[dict]:
    """Exams whose title/subject/notes mention every word of `subject`."""
    words = [
        w
        for w in re.findall(r"\w+", str(subject or "").lower())
        if w not in ("my", "exam", "exams", "the")
    ]
    if not words:
        return []
    rows = exams() if include_past else upcoming(today)
    return [
        e
        for e in rows
        if all(w in f"{e['title']} {e['subject']} {e['notes']}".lower() for w in words)
    ]


def days_until(e: dict, today: dt.date | None = None) -> int:
    return (dt.date.fromisoformat(e["date"]) - (today or _today())).days


def _in_days(n: int) -> str:
    if n == 0:
        return "today"
    if n == 1:
        return "tomorrow"
    if n < 0:
        return f"{-n} days ago"
    return f"in {n} days"


def describe(e: dict, today: dt.date | None = None) -> str:
    """'Physics Paper 2, Tue 14 Oct at 09:00 in Hall B (in 3 days)'. Pure-ish."""
    day = dt.date.fromisoformat(e["date"]).strftime("%a %d %b")
    when = f" at {e['start']}" if e.get("start") else ""
    until = f"-{e['end']}" if e.get("start") and e.get("end") else ""
    where = f" in {e['location']}" if e.get("location") else ""
    return f"{e['title']}, {day}{when}{until}{where} ({_in_days(days_until(e, today))})"


#: Assumed length of an exam with a start time but no end time.
DEFAULT_EXAM_MIN = 120


def current_exam(now: dt.datetime | None = None) -> dict | None:
    """The exam Sir is sitting right now (start <= now < end), else None.

    Needs a start time; a missing end means DEFAULT_EXAM_MIN minutes. The
    returned dict carries "until" ('HH:MM') for 'back after' messages.
    """
    now = now or dt.datetime.now()
    today = now.date().isoformat()
    for e in exams():
        if e["date"] != today or not e.get("start"):
            continue
        h, m = (int(x) for x in e["start"].split(":"))
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if e.get("end"):
            eh, em = (int(x) for x in e["end"].split(":"))
            end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
        else:
            end = start + dt.timedelta(minutes=DEFAULT_EXAM_MIN)
        if start <= now < end:
            return {**e, "until": end.strftime("%H:%M")}
    return None


def busy_days(rows: list[dict]) -> list[str]:
    """Dates with two or more exams. Pure."""
    counts: dict[str, int] = {}
    for e in rows:
        counts[e["date"]] = counts.get(e["date"], 0) + 1
    return sorted(d for d, n in counts.items() if n > 1)


def summary(today: dt.date | None = None, days: int = 30) -> str:
    """Short spoken overview of the next `days` of exams."""
    today = today or _today()
    rows = upcoming(today, days)
    if not rows:
        nxt = next_exam(today)
        return (
            f"No exams in the next {days} days. Next: {describe(nxt, today)}."
            if nxt
            else "No exams coming up."
        )
    lines = [describe(e, today) for e in rows[:6]]
    say = (
        f"{len(rows)} exam{'s' if len(rows) != 1 else ''} in the next {days} days: "
        + "; ".join(lines)
    )
    if len(rows) > 6:
        say += f"; and {len(rows) - 6} more"
    doubles = busy_days(rows)
    if doubles:
        say += ". Double days: " + ", ".join(
            dt.date.fromisoformat(d).strftime("%a %d %b") for d in doubles[:4]
        )
    return say + "."


def briefing_line(today: dt.date | None = None) -> str:
    """One line for the morning briefing ('' when nothing within two weeks)."""
    today = today or _today()
    rows = upcoming(today, 14)
    if not rows:
        return ""
    first = rows[0]
    more = f" {len(rows) - 1} more in the next two weeks." if len(rows) > 1 else ""
    return f"Next exam: {describe(first, today)}.{more}"


# --- reminders ---


def due_reminders(now: dt.datetime, sent: set[str]) -> list[tuple[str, str, dict]]:
    """(key, kind, exam) reminders due now and not yet sent. Pure-ish.

    kind 'eve': the evening before (from EVENING_HOUR); 'day': the morning
    of (from MORNING_HOUR, and before the exam starts).
    """
    today = now.date()
    out = []
    for e in upcoming(today, 1):
        d = days_until(e, today)
        if d == 1 and now.hour >= EVENING_HOUR:
            kind = "eve"
        elif d == 0 and now.hour >= MORNING_HOUR:
            if e.get("start") and now.strftime("%H:%M") >= e["start"].zfill(5):
                continue
            kind = "day"
        else:
            continue
        key = f"{kind}:{e['date']}:{_key(e)[0]}"
        if key not in sent:
            out.append((key, kind, e))
    return out


def _reminded_path() -> Path:
    return home() / "exams.reminded.json"


def remind_once(now: dt.datetime | None = None, send=None) -> int:
    """Send any due reminders through notify. Returns how many. Never raises."""
    now = now or dt.datetime.now()
    try:
        sent = set(json.loads(_reminded_path().read_text()))
    except (OSError, ValueError, TypeError):
        sent = set()
    due = due_reminders(now, sent)
    if not due:
        return 0
    if send is None:
        import notify

        send = notify.send
    n = 0
    for key, kind, e in due:
        line = describe(e, now.date())
        text = (
            f"Exam tomorrow: {line}. Pack what you need and get some sleep."
            if kind == "eve"
            else f"Exam today: {line}. Good luck, Sir."
        )
        with contextlib.suppress(Exception):
            send(
                text,
                title="Jarvis - exam reminder",
                kind="exam",
                source="exams",
                urgency="info",
                speak_text=f"Sir, {text}",
                fingerprint=f"exam:{key}",
            )
            sent.add(key)
            n += 1
    with contextlib.suppress(OSError):
        _reminded_path().parent.mkdir(parents=True, exist_ok=True)
        _reminded_path().write_text(json.dumps(sorted(sent)[-400:]))
    return n


def start_thread(interval: float = 900.0, first_delay: float = 60.0):
    """Exam reminders inside the Jarvis service (bridge sidecar)."""
    import threading

    stop = threading.Event()

    def _loop() -> None:
        if stop.wait(first_delay):
            return
        while not stop.is_set():
            with contextlib.suppress(Exception):
                remind_once()
            if stop.wait(interval):
                return

    thread = threading.Thread(target=_loop, name="exam-remind", daemon=True)
    thread.start()
    return thread, stop


def parse_time(text: str) -> str:
    """'9am' / '2:30 pm' / '14:00' -> 'HH:MM', else ''. Pure."""
    m = re.fullmatch(
        r"\s*(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*", str(text or ""), re.I
    )
    if not m:
        return ""
    hh, mm = int(m.group(1)), int(m.group(2) or 0)
    ap = (m.group(3) or "").lower().replace(".", "")
    if ap == "pm" and hh < 12:
        hh += 12
    elif ap == "am" and hh == 12:
        hh = 0
    if hh > 23 or mm > 59 or (not ap and not m.group(2)):
        return ""
    return f"{hh:02d}:{mm:02d}"


def parse_date(text: str, today: dt.date | None = None) -> str:
    """'2026-10-14' / 'today' / 'tomorrow' / 'monday' / '14 oct' -> ISO, else ''."""
    today = today or _today()
    t = " ".join(str(text or "").lower().split())
    if not t:
        return ""
    with contextlib.suppress(ValueError):
        return dt.date.fromisoformat(t[:10]).isoformat()
    if t == "today":
        return today.isoformat()
    if t == "tomorrow":
        return (today + dt.timedelta(days=1)).isoformat()
    days = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
    t2 = t.removeprefix("next ").removeprefix("this ")
    for i, name in enumerate(days):
        if t2 in (name, name[:3]):
            ahead = (i - today.weekday()) % 7 or 7
            return (today + dt.timedelta(days=ahead)).isoformat()
    for fmt in ("%d %b", "%d %B", "%b %d", "%B %d", "%d/%m", "%d %b %Y", "%d %B %Y"):
        with contextlib.suppress(ValueError):
            d = dt.datetime.strptime(t.replace(",", ""), fmt).date()
            if "%Y" not in fmt:
                d = d.replace(year=today.year)
                if d < today - dt.timedelta(days=60):
                    d = d.replace(year=today.year + 1)
            return d.isoformat()
    return ""
