"""Deterministic caller-intent parsing for the call agent (W8).

Free models are weak and nondeterministic, so the code, not the model, decides
what a caller asked for (book / check / remind / cancel-or-change / withdraw)
and which time they meant. `parse` returns one of:

  None                      nothing calendar-related: let the LLM answer
  Action(kind, args)        run the calendar tool now (kind BOOK|CHECK|REMIND|CHANGE|WITHDRAW)
  Clarify(question)         ask the caller (vague time, missing day, unknown zone)

All times resolve in code. Caller zone (e.g. "3pm my time, I'm in London") is
converted to the owner's zone (Asia/Singapore). Nothing here touches tools.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

OWNER_TZ = ZoneInfo("Asia/Singapore")

_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}  # fmt: skip
_MIN = {
    "fifteen": 15, "thirty": 30, "forty five": 45, "forty-five": 45, "fortyfive": 45,
    "ten": 10, "twenty": 20, "twenty five": 25, "five": 5,
}  # fmt: skip
_WEEKDAYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"]  # fmt: skip

ZONES = {
    "london": "Europe/London", "uk": "Europe/London", "britain": "Europe/London",
    "england": "Europe/London", "gmt": "Europe/London", "utc": "UTC",
    "paris": "Europe/Paris", "berlin": "Europe/Berlin", "madrid": "Europe/Madrid",
    "new york": "America/New_York", "nyc": "America/New_York", "est": "America/New_York",
    "boston": "America/New_York", "chicago": "America/Chicago",
    "los angeles": "America/Los_Angeles", "san francisco": "America/Los_Angeles",
    "pst": "America/Los_Angeles", "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo",
    "sydney": "Australia/Sydney", "dubai": "Asia/Dubai", "mumbai": "Asia/Kolkata",
    "india": "Asia/Kolkata", "delhi": "Asia/Kolkata", "hong kong": "Asia/Hong_Kong",
    "singapore": "Asia/Singapore", "sgt": "Asia/Singapore",
}  # fmt: skip

_PART = {"morning": "am", "evening": "pm", "tonight": "pm", "night": "pm",
         "afternoon": "pm", "noon": "pm", "midday": "pm", "breakfast": "am",
         "dinner": "pm", "supper": "pm"}  # fmt: skip
_VAGUE = re.compile(
    r"\b(morning|afternoon|evening|tonight|night|later|sometime|some time|soon|"
    r"after work|after lunch|around then|next week|this week|whenever)\b",
    re.I,
)
_BOOK = re.compile(
    r"\b(book|schedule|arrange|set up|reserve|put (it|that|us|a \w+) (in|on)|"
    r"add (it|that|a \w+|an? \w+) to|can we (do|have|meet|catch)|could we (do|have|meet)|"
    r"let'?s (do|meet|have|catch)|meet (me|up|at|on|tomorrow|tonight)|"
    r"(a |the )?(meeting|appointment|call|dinner|lunch|breakfast|coffee) (with|at|on|tomorrow|tonight|for)|"
    r"i'?d like to (meet|see|have))\b",
    re.I,
)
_CHECK = re.compile(
    r"\b(is|are) (he|she|the owner|you|they|[a-z]+) (free|available|around|busy)\b|"
    r"\b(free|available) (at|on|tomorrow|tonight|this|next)\b|"
    r"\banything (on|scheduled|planned)\b|\bis (the )?(slot|time) (free|open)\b|"
    r"\bdoes (he|she|the owner|\w+) have (anything|something) (on|at)\b",
    re.I,
)
_REMIND = re.compile(
    r"\b(reminder|reminders|in (the|his|her|my) calendar|on (the|his|her|my) calendar|"
    r"in (the|his|her|my) diary|do you have an? [\w ]+ (booked|scheduled)|"
    r"did (he|she|i|the owner) have|do (i|you|they) have an? [\w ]+ (this|on|today|tomorrow)|"
    r"what time is|when is|when was|is there an? [\w ]+ (today|tomorrow|on|this|next))\b",
    re.I,
)
_CANCEL = re.compile(
    r"\b(cancel|call off|reschedule|postpone|move|push|change|shift|delay|bring forward)\b",
    re.I,
)
_WITHDRAW = re.compile(
    r"\b(never ?mind|forget it|forget that|scrap that|don'?t (book|bother)|"
    r"cancel (that|it|my request|the request|this)|withdraw|i changed my mind|"
    r"leave it|no need to book)\b",
    re.I,
)


@dataclass
class Action:
    kind: str  # BOOK | CHECK | REMIND | CHANGE | WITHDRAW
    args: dict = field(default_factory=dict)
    note: str = ""  # extra sentence to say after the tool result (e.g. zone)


@dataclass
class Clarify:
    question: str


@dataclass
class State:
    """Partial booking/check carried between turns (e.g. date known, time not)."""

    kind: str = ""  # book | check
    title: str = ""
    date: dt.date | None = None
    time: tuple[int, int] | None = None
    minutes: int | None = None
    tz: str = ""  # caller zone name once known ("" = owner zone)
    wants_tz: bool = False  # said "my time" but zone unknown
    asked: str = ""
    hint: str = ""  # daypart words seen so far ("evening")

    def reset_pending(self) -> None:
        self.kind = self.title = self.asked = self.hint = ""
        self.date = self.time = self.minutes = None
        self.wants_tz = False


# ---------------------------------------------------------------- pieces
def _words_to_digits(t: str) -> str:
    """'seven thirty' -> '7:30', 'half past seven' -> '7:30', 'noon' -> '12pm'."""
    t = t.lower()
    t = re.sub(r"\bhalf past (\w+)", lambda m: f"{_n(m.group(1))}:30", t)
    t = re.sub(r"\bquarter past (\w+)", lambda m: f"{_n(m.group(1))}:15", t)
    t = re.sub(r"\bquarter to (\w+)", lambda m: f"{(_n(m.group(1)) or 1) - 1}:45", t)
    t = re.sub(r"\bnoon\b|\bmidday\b", "12pm", t)
    t = re.sub(r"\bmidnight\b", "12am", t)
    names = "|".join(sorted(_NUM, key=len, reverse=True))
    mins = "|".join(sorted(_MIN, key=len, reverse=True))
    t = re.sub(
        rf"\b({names})\s+({mins})\b(?!\s*(?:minutes?|mins?|hours?))",
        lambda m: f"{_NUM[m.group(1)]}:{_MIN[m.group(2)]:02d}",
        t,
    )
    t = re.sub(rf"\b({names})\s+o'?clock\b", lambda m: f"{_NUM[m.group(1)]}:00", t)
    t = re.sub(
        rf"\b(at|by|from|to|until|till|around|for)\s+({names})\b(?!\s*(?:minutes?|mins?|hours?|people|of))",
        lambda m: f"{m.group(1)} {_NUM[m.group(2)]}",
        t,
    )
    return t


def _n(w: str) -> int:
    return _NUM.get(w, int(w) if w.isdigit() else 0)


_TIME = re.compile(
    r"(?<![\d:/-])(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)?(?![\d:/-])",
    re.I,
)


def parse_time(text: str, hint: str = "") -> tuple[tuple[int, int] | None, str]:
    """Return ((h, m) 24h or None, problem). problem: '' | 'ampm' (needs am/pm).

    Only numbers introduced by at/by/from/@/am/pm/colon count, so '2 people' and
    dates are not read as times.
    """
    t = _words_to_digits(text)
    cands = []
    for m in _TIME.finditer(t):
        pre = t[max(0, m.start() - 6) : m.start()].lower()
        has_marker = bool(
            m.group("ap")
            or m.group("m")
            or re.search(r"(\bat|\bby|\bfrom|\bto|@|\buntil|\baround|\bfor)\s*$", pre)
        )
        if not has_marker:
            continue
        # "for 2 hours" / "at 3 people": skip durations
        tail = t[m.end() : m.end() + 8].lower()
        if re.match(r"\s*(hours?|hrs?|minutes?|mins?|people|of us)\b", tail):
            continue
        # "20th"/"20 november": date
        if re.match(r"\s*(st|nd|rd|th|" + "|".join(_MONTHS) + r")\b", tail):
            continue
        cands.append(m)
    if not cands:
        return None, ""
    m = cands[0]
    h, mi = int(m.group("h")), int(m.group("m") or 0)
    ap = (m.group("ap") or "").lower().replace(".", "")
    if h > 24 or mi > 59:
        return None, ""
    if ap:
        if not 1 <= h <= 12:
            return None, ""
        if ap == "pm" and h != 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        return (h, mi), ""
    if h >= 13 or h == 0 or (m.group("m") and h >= 13):
        return (h % 24, mi), ""
    # bare 1..12: use daypart hints, else small numbers are pm, 7-11 ambiguous
    low = (text + " " + hint).lower()
    if re.search(r"\b(morning|breakfast|am)\b", low):
        return ((0 if h == 12 else h), mi), ""
    if re.search(r"\b(evening|tonight|night|dinner|supper|afternoon|pm)\b", low):
        return ((h if h == 12 else h + 12), mi), ""
    if re.search(r"\blunch\b", low):
        return ((h if h == 12 else h + 12), mi), ""
    if h == 12:
        return (12, mi), ""
    if 1 <= h <= 6:
        return (h + 12, mi), ""
    if 8 <= h <= 11:
        return (h, mi), ""  # no hint: office hours, morning
    return None, "ampm"  # a bare 7 could be either


def parse_minutes(text: str) -> int | None:
    t = _words_to_digits(text.lower())
    t = re.sub(r"\ban? hour and a half\b", "90 minutes", t)
    t = re.sub(r"\bhalf an? hour\b", "30 minutes", t)
    t = re.sub(r"\ban? hour\b", "60 minutes", t)
    names = "|".join(_NUM)
    t = re.sub(rf"\b({names})\s+hours?\b", lambda m: f"{_NUM[m.group(1)]} hours", t)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h)\b", t)
    if m:
        return max(15, int(float(m.group(1)) * 60))
    m = re.search(r"(\d+)\s*(minutes?|mins?)\b", t)
    if m:
        return max(5, int(m.group(1)))
    return None


def parse_range_end(text: str, start_ap_hint: str = "") -> tuple[int, int] | None:
    """'from 3 to 4pm' / '3-4pm': the end time (24h)."""
    t = _words_to_digits(text.lower())
    m = re.search(
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:to|until|till|-)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
        t,
    )
    if not m:
        return None
    h, mi, ap = int(m.group(4)), int(m.group(5) or 0), m.group(6)
    if ap == "pm" and h != 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return h, mi


def parse_date(text: str, today: dt.date) -> dt.date | None:
    t = text.lower()
    if re.search(r"\bday after tomorrow\b", t):
        return today + dt.timedelta(days=2)
    if re.search(r"\b(tomorrow|tmrw|tmr)\b", t):
        return today + dt.timedelta(days=1)
    if re.search(r"\b(today|tonight|this evening|this afternoon|this morning)\b", t):
        return today
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    for i, mon in enumerate(_MONTHS, 1):
        m = re.search(
            rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{mon[:3]}\w*\b", t
        ) or re.search(rf"\b{mon[:3]}\w*\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", t)
        if m:
            try:
                d = dt.date(today.year, i, int(m.group(1)))
            except ValueError:
                return None
            return d if d >= today else d.replace(year=today.year + 1)
    for i, wd in enumerate(_WEEKDAYS):
        if re.search(rf"\b{wd}\b|\b{wd[:3]}\b(?!\w)", t) and (
            wd not in ("may",) and not re.search(rf"\b{wd[:3]}\b\s+\d", t)
        ):
            delta = (i - today.weekday()) % 7
            if re.search(rf"\bnext {wd}", t) and delta < 7:
                delta = delta or 7
            return today + dt.timedelta(days=delta)
    return None


def parse_zone(text: str) -> str:
    t = text.lower()
    for k in sorted(ZONES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(k)}\b", t):
            return ZONES[k]
    return ""


def _title(text: str) -> str:
    t = text.lower()
    for w in ("dinner", "lunch", "breakfast", "coffee", "meeting", "appointment",
              "call", "catch up", "drinks", "visit"):  # fmt: skip
        if w in t:
            return w.capitalize()
    return "Meeting"


def _query(text: str) -> str:
    """Event words for a reminder lookup."""
    t = text.lower()
    t = re.sub(r"[?!.,]", " ", t)
    t = re.sub(r"(\w)'s\b", r"\1", t)
    drop = (
        r"\b(is there|are there|do you have|do i have|did i have|did he have|did she have|"
        r"did the owner have|any|an?|the|his|her|my|our|in|on|to|of|about|reminder|reminders|"
        r"calendar|diary|booked|scheduled|please|can you|could you|check|tell me|"
        r"what time is|when is|when was|is|was|this week|next week|this|today|tomorrow|"
        r"yesterday|there|for|me|it|thing|stuff|event|appointment)\b"
    )
    t = re.sub(drop, " ", t)
    return " ".join(t.split())


def _to_owner(d: dt.date, hm: tuple[int, int], tz: str) -> dt.datetime:
    src = ZoneInfo(tz) if tz else OWNER_TZ
    return dt.datetime.combine(d, dt.time(*hm), tzinfo=src).astimezone(OWNER_TZ)


# ---------------------------------------------------------------- main
def parse(
    text: str, now: dt.datetime, st: State, last_proposal_open: bool = False
) -> Action | Clarify | None:
    """Decide the calendar intent of one caller turn. Mutates ``st`` (pending)."""
    t = " ".join(text.split())
    low = t.lower()
    now = now.astimezone(OWNER_TZ)

    # --- withdraw / cancel-change (reachable paths)
    if _WITHDRAW.search(low) and (last_proposal_open or st.kind):
        st.reset_pending()
        return Action("WITHDRAW", {"open": last_proposal_open, "detail": t})
    if (
        _CANCEL.search(low)
        and re.search(r"\b(the|my|his|her|our|that|this|an?)\b.*\b[a-z]{3,}", low)
        and not re.search(r"\b(book|schedule|arrange|set up|reserve)\b", low)
        and not st.kind
    ):
        # existing-event change: caller may never do it; owner decides
        what = "cancel" if re.search(r"\b(cancel|call off)\b", low) else "move"
        return Action("CHANGE", {"what": what, "detail": t[:200]})

    # --- decide the kind (pending state wins for short follow-ups)
    kind = ""
    if _REMIND.search(low) and not _BOOK.search(low):
        kind = "remind"
    elif _CHECK.search(low):
        kind = "check"
    elif _BOOK.search(low):
        kind = "book"
    elif st.kind:
        kind = st.kind  # follow-up like "Seven thirty then."
    if not kind:
        return None

    if kind == "remind":
        st.reset_pending()
        when = parse_date(low, now.date())
        return Action(
            "REMIND",
            {"query": _query(t), "when": when.isoformat() if when else ""},
        )

    # --- book / check: gather date, time, duration, zone
    if st.kind != kind:
        st.reset_pending()
        st.kind = kind
        st.title = _title(t)
    elif st.title in ("", "Meeting"):
        st.title = _title(t)

    z = parse_zone(low)
    if z:
        st.tz, st.wants_tz = z, False
    if re.search(
        r"\bmy (local )?time\b|\bmy time ?zone\b|\bin my zone\b|\blocal time\b", low
    ):
        st.wants_tz = not st.tz
    tz_for_date = st.tz or ""
    today = now.astimezone(ZoneInfo(tz_for_date)).date() if tz_for_date else now.date()

    d = parse_date(low, today)
    if d:
        st.date = d
    part = re.search(r"\b(morning|afternoon|evening|tonight|night)\b", low)
    if part:
        st.hint = part.group(1)
    tm, problem = parse_time(low, st.title + " " + st.hint)
    if tm:
        st.time = tm
    mins = parse_minutes(low)
    if mins:
        st.minutes = mins
    end = parse_range_end(low)
    if end and st.time:
        s = st.time[0] * 60 + st.time[1]
        e = end[0] * 60 + end[1]
        if e <= s and e < 12 * 60:
            e += 12 * 60
        if e > s:
            st.minutes = e - s

    if st.wants_tz:
        return Clarify("Which time zone or city are you in?")
    if st.time is None:
        if problem == "ampm":
            return Clarify("Is that in the morning or the evening?")
        vague = _VAGUE.search(low)
        day = _day_word(st.date, today) if st.date else ""
        return Clarify(
            " ".join(
                x
                for x in ("What time exactly", day, vague.group(0) if vague else "")
                if x
            )
            + "?"
        )
    if st.date is None:
        return Clarify("Which day do you mean?")

    start = _to_owner(st.date, st.time, st.tz)  # type: ignore[arg-type]
    dur = st.minutes or 60
    end_dt = start + dt.timedelta(minutes=dur)
    who = ""
    note = ""
    if st.tz and st.tz != "Asia/Singapore":
        note = "That is " + start.strftime("%H:%M").lstrip("0") + " Singapore time."
    args = {"start": start.isoformat(), "end": end_dt.isoformat()}
    act = "BOOK" if kind == "book" else "CHECK"
    if kind == "book":
        args.update(title=st.title or "Meeting", who=who)
    st.reset_pending()
    return Action(act, args, note)


def _day_word(d: dt.date, today: dt.date) -> str:
    if d == today:
        return "today"
    if d == today + dt.timedelta(days=1):
        return "tomorrow"
    return "on " + d.strftime("%A")
