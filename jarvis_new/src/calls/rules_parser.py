"""Deterministic natural-language parser for call rules and scheduled calls.

Pure functions, no I/O, no LLM. Times use the scheduler timezone (``JARVIS_TZ``,
default Asia/Singapore) via ``scheduled_tasks.timezone``.

``parse(text)`` returns a ``Parsed`` or ``None`` when the text is not a call
command. It raises ``ParseError`` (speakable) when it is clearly a call command
but a required part (time, contact) is missing or invalid.
"""

from __future__ import annotations

import datetime as dt
import re
import time
from dataclasses import dataclass, field

import scheduled_tasks as _st

_NUM_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty five": 45,
    "sixty": 60,
}
_UNIT_S = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}

_NUM = (
    r"(?:\d+(?:\.\d+)?|"
    + "|".join(sorted((re.escape(k) for k in _NUM_WORDS), key=len, reverse=True))
    + r")"
)
_DUR = rf"(?:half an hour|half a hour|{_NUM}\s+(?:seconds?|minutes?|mins?|hours?|hrs?|days?))"
_CLOCK = r"(?:noon|midnight|\d{1,2}(?::\d{2})?\s?(?:am|pm)|\d{1,2}:\d{2})"


class ParseError(ValueError):
    """Speakable parse failure."""


@dataclass
class RuleSpec:
    kind: str = "answer_all"
    start: float | None = None
    end: float | None = None
    daily_from: str | None = None
    daily_to: str | None = None
    message: str = ""
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)

    def as_kwargs(self, created_by="voice") -> dict:
        return {
            "kind": self.kind,
            "start": self.start,
            "end": self.end,
            "daily_from": self.daily_from,
            "daily_to": self.daily_to,
            "message": self.message,
            "allow": list(self.allow),
            "deny": list(self.deny),
            "created_by": created_by,
        }


@dataclass
class Parsed:
    # auto_answer | cancel_auto_answer | schedule_call | call_now
    intent: str
    rule: RuleSpec | None = None
    contact: str = ""
    due: float | None = None
    goal: str = ""
    summary: str = ""


def _norm(text: str) -> str:
    return " ".join(str(text or "").replace("\u2019", "'").split())


def _tz():
    return _st.timezone()


def _duration_s(text: str) -> float:
    t = text.lower().strip()
    if t.startswith("half"):
        return 1800.0
    m = re.fullmatch(rf"({_NUM})\s+(\w+?)s?", t)
    if not m:
        raise ParseError("I could not read that duration.")
    n = m[1]
    value = float(n) if n[0].isdigit() else float(_NUM_WORDS[n])
    unit = m[2]
    unit = {"min": "minute", "hr": "hour"}.get(unit, unit)
    if unit not in _UNIT_S:
        raise ParseError("I could not read that duration.")
    return value * _UNIT_S[unit]


def _clock(text: str) -> tuple[int, int]:
    t = text.lower().replace(" ", "")
    if t == "noon":
        return 12, 0
    if t == "midnight":
        return 0, 0
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)?", t)
    if not m:
        raise ParseError("I could not read that time.")
    hour, minute = int(m[1]), int(m[2] or 0)
    if m[3]:
        if not 1 <= hour <= 12:
            raise ParseError("I could not read that time.")
        hour = hour % 12 + (12 if m[3] == "pm" else 0)
    if hour > 23 or minute > 59:
        raise ParseError("I could not read that time.")
    return hour, minute


def _at(now: float, hour: int, minute: int, day_offset: int = 0) -> float:
    cur = dt.datetime.fromtimestamp(now, _tz())
    day = cur.date() + dt.timedelta(days=day_offset)
    return dt.datetime.combine(day, dt.time(hour, minute), _tz()).timestamp()


def _next_at(now: float, hour: int, minute: int) -> float:
    value = _at(now, hour, minute)
    return value if value > now else _at(now, hour, minute, 1)


# ---------------------------------------------------------------- cancel

_CANCEL_RE = re.compile(
    r"\b(?:stop|cancel|end|disable|turn off|switch off|clear)\b.*?"
    r"\b(?:auto[\s-]?(?:answer|reply|respond)\w*|answering|call (?:handling|rules?|screening)"
    r"|handling (?:my )?calls|(?:my )?call rules?)\b",
    re.I,
)
_CANCEL_RE2 = re.compile(
    r"\b(?:stop|cancel)\s+(?:the\s+)?(?:auto|answer\w*|call handling)\b", re.I
)


def is_cancel(text: str) -> bool:
    t = _norm(text)
    return bool(_CANCEL_RE.search(t) or _CANCEL_RE2.search(t))


# ---------------------------------------------------------------- call


_CALL_RE = re.compile(
    r"^(?:(?:please|jarvis|hey|can you|could you|will you)\s+)*"
    r"(?:(?:schedule|set up|book|arrange)\s+(?:a\s+)?(?:phone\s+)?call\s+(?:to|with|for)\s+|"
    r"(?:call|ring|phone|dial)\s+)(?P<rest>.+)$",
    re.I,
)
_WHEN_RE = re.compile(
    rf"\b(?P<when>"
    rf"(?:(?:today|tomorrow)\s+)?at\s+{_CLOCK}(?:\s+(?:today|tomorrow))?"
    rf"|in\s+{_DUR}"
    rf"|tomorrow(?:\s+{_CLOCK})?"
    rf"|(?:today|tonight)\s+{_CLOCK}"
    rf")\b",
    re.I,
)


def _when_to_due(when: str, now: float) -> float:
    w = _norm(when).lower()
    if m := re.fullmatch(rf"in\s+({_DUR})", w):
        secs = _duration_s(m[1])
        w = f"in {round(secs / 60)} minutes"
        return _st.parse_when(w, now=now)
    day = ""
    if "tomorrow" in w:
        day = "tomorrow "
    elif "today" in w or "tonight" in w:
        day = "today "
    clock = re.search(_CLOCK, w)
    if not clock:
        # bare "tomorrow": default 09:00
        return _st.parse_when("tomorrow 9am", now=now)
    h, mi = _clock(clock[0])
    spec = f"{day}{h % 12 or 12}:{mi:02d}{'pm' if h >= 12 else 'am'}"
    return _st.parse_when(spec, now=now)


def _parse_call(text: str, now: float) -> Parsed | None:
    m = _CALL_RE.match(text)
    if not m:
        return None
    rest = m["rest"].strip().rstrip(".?!")
    # "call me" is the owner-call scheduler (scheduled_tasks), not ours.
    if re.match(r"(?:me|us|myself)\b", rest, re.I):
        return None
    w = _WHEN_RE.search(rest)
    if not w:
        name, goal = _split_goal(rest)
        if not name:
            raise ParseError("Who should I call?")
        return Parsed(
            "call_now",
            contact=name,
            goal=goal,
            summary=f"Call {name}" + (f": {goal}" if goal else ""),
        )
    name = rest[: w.start()].strip(" ,")
    after = rest[w.end() :].strip(" ,")
    if not name:
        raise ParseError("Who should I call?")
    try:
        due = _when_to_due(w["when"], now)
    except _st.ScheduleError as exc:
        raise ParseError(str(exc)) from exc
    goal = _clean_goal(after)
    when_txt = dt.datetime.fromtimestamp(due, _tz()).strftime("%a %d %b %H:%M")
    return Parsed(
        "schedule_call",
        contact=name,
        due=due,
        goal=goal,
        summary=f"Call {name} at {when_txt}" + (f": {goal}" if goal else ""),
    )


def _clean_goal(goal: str) -> str:
    g = _norm(goal).strip(" ,.")
    g = re.sub(r"^(?:and|then|to|so|but)\s+", "", g, flags=re.I)
    g = re.sub(r"^(?:and|then)\s+", "", g, flags=re.I)
    return g.strip(" ,.")


def _split_goal(rest: str) -> tuple[str, str]:
    m = re.search(
        r"\s+(?:and\s+(?:ask|tell|say|let|remind|check)|to\s+(?:ask|tell|say|let|remind|check|see|find)|about)\b",
        rest,
        re.I,
    )
    if not m:
        return rest.strip(), ""
    name = rest[: m.start()].strip(" ,")
    return name, _clean_goal(rest[m.start() :])


# ---------------------------------------------------------------- auto-answer

_AUTO_RE = re.compile(
    r"\b(?:auto[\s-]?(?:answer|reply|respond)\w*"
    r"|(?:answer|handle|take|pick up|screen|reject|decline|block|divert)\b.*?\bcalls?\b"
    r"|(?:answer|handle|screen)\b.*?\bcallers?\b"
    r"|messages? only|take (?:a )?messages?)",
    re.I,
)
_MSG_RE = re.compile(
    r"(?:,\s*|\s+)(?:and\s+)?(?:(?:say|tell(?:\s+(?:them|callers|people|everyone))?|"
    r"reply(?:\s+with)?|respond(?:\s+with)?|with the message)\s+(?:that\s+)?)(?P<m>.+)$",
    re.I,
)


def _extract_message(text: str) -> tuple[str, str]:
    m = _MSG_RE.search(text)
    if not m:
        return text, ""
    msg = m["m"].strip().strip("\"'").rstrip(".!").strip()
    return text[: m.start()], msg


def _kind(t: str) -> str:
    if re.search(
        r"\b(?:reject|decline|block|don't answer|do not answer|divert)\b", t, re.I
    ):
        return "reject"
    if re.search(r"\b(?:messages? only|take (?:a )?messages?|voicemail)\b", t, re.I):
        return "message_only"
    if re.search(
        r"\bunknown\s+(?:numbers?|callers?|contacts?)|\bstrangers?\b|\bnot in my contacts\b"
        r"|\bnumbers? i don't know\b|\bunsaved\b",
        t,
        re.I,
    ):
        return "answer_unknown"
    return "answer_all"


def _window(t: str, now: float, spec: RuleSpec) -> None:
    daily = bool(
        re.search(r"\b(?:every ?day|daily|each day|every (?:week)?day)\b", t, re.I)
    )
    if m := re.search(
        rf"\bfor\s+(?:the\s+)?(?:next\s+|coming\s+)?(?:(?P<d>{_DUR}))\b", t, re.I
    ):
        spec.start = now
        spec.end = now + _duration_s(m["d"])
        return
    if m := re.search(
        rf"\b(?:between|from)\s+(?P<a>{_CLOCK})\s+(?:and|to|until|till|-)\s+(?P<b>{_CLOCK})\b",
        t,
        re.I,
    ) or re.search(
        rf"\b(?P<a>{_CLOCK})\s*(?:to|-|until|till)\s*(?P<b>{_CLOCK})\b", t, re.I
    ):
        ha, ma = _clock(m["a"])
        hb, mb = _clock(m["b"])
        if (ha, ma) == (hb, mb):
            raise ParseError("The start and end times are the same.")
        if daily:
            spec.daily_from = f"{ha:02d}:{ma:02d}"
            spec.daily_to = f"{hb:02d}:{mb:02d}"
            return
        start = _at(now, ha, ma)
        end = _at(now, hb, mb)
        if end <= start:  # overnight window
            end += 86400
        if end <= now:  # window already over today
            start += 86400
            end += 86400
        spec.start, spec.end = start, end
        return
    if m := re.search(rf"\b(?:until|till|up to)\s+(?P<b>{_CLOCK})\b", t, re.I):
        hb, mb = _clock(m["b"])
        spec.start, spec.end = now, _next_at(now, hb, mb)


def _parse_auto(text: str, now: float) -> Parsed | None:
    if not _AUTO_RE.search(text):
        return None
    body, message = _extract_message(text)
    spec = RuleSpec(kind=_kind(body), message=message)
    _window(body, now, spec)
    if spec.end is not None and spec.end <= now:
        raise ParseError("That window has already ended.")
    when = "until stopped"
    if spec.daily_from:
        when = f"daily {spec.daily_from} to {spec.daily_to}"
    elif spec.end:
        when = "until " + dt.datetime.fromtimestamp(spec.end, _tz()).strftime(
            "%a %d %b %H:%M"
        )
    label = {
        "answer_all": "Answer all calls",
        "answer_unknown": "Handle calls from unknown numbers",
        "reject": "Reject calls",
        "message_only": "Take messages only",
    }[spec.kind]
    summary = f"{label} {when}" + (f', saying "{message}"' if message else "")
    return Parsed("auto_answer", rule=spec, summary=summary)


def parse(text: str, now: float | None = None) -> Parsed | None:
    now = time.time() if now is None else now
    t = _norm(text)
    if not t:
        return None
    if is_cancel(t):
        return Parsed("cancel_auto_answer", summary="Cancel auto answer")
    if call := _parse_call(t, now):
        return call
    return _parse_auto(t, now)
