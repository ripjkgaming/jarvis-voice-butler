"""Goal routing: read-type requests always run their tool, however phrased.

Gemini Live sometimes answers a clear goal from nothing ("I am afraid I
cannot access your emails" with Gmail connected and gmail_inbox on its tool
list). For Sir's everyday read goals the tool is never in doubt, so this
maps the final transcript straight to it, before Gemini sees the turn:

    mail      -> gmail_inbox / gmail_read
    exams     -> exam_times / exam_schedule(find)
    calendar  -> calendar_upcoming
    weather   -> weather_now
    news      -> news_digest

Only reads. Anything that writes (send, reply, add, delete, ...), anything
compound ("check my mail and open spotify"), and anything about what
Jarvis did ("which emails did you reply to") falls through to Gemini.
Pure: route() returns (tool, args) or None. JARVIS_GOAL_ROUTES=0 disables.
"""

from __future__ import annotations

import os
import re

MAX_WORDS = 18

_WRITE = re.compile(
    r"\b(send|sent|reply|replied|respond|write|draft|compose|forward|delete|trash|"
    r"archive|mark|add|create|schedule an?|book|remove|cancel|move|remind|set|"
    r"import|unsubscribe|block|summari[sz]e and send)\b"
)
_COMPOUND = re.compile(r"\b(and then|then|also|after that)\b|\band\b(?! (?:the|my) )")
_ABOUT_JARVIS = re.compile(r"\b(did you|have you|you (?:sent|replied|read|flagged))\b")

_MAIL = re.compile(r"\b(e ?mails?|mails?|inbox|gmail)\b")
_MAIL_ONE = re.compile(r"\b(read|open|show)\b.*\b(latest|last|newest|most recent|top|first)\b")
_UNREAD = re.compile(r"\b(unread|new)\b")

_EXAM = re.compile(r"\b(exams?|papers?|mocks?)\b")
_CALENDAR = re.compile(r"\b(calendar|agenda|events?|plans|schedule|what's on|whats on)\b")
_WEATHER = re.compile(
    r"\b(weather|forecast|temperature|raining|rain today|rain tomorrow|umbrella)\b"
)
_NEWS = re.compile(r"\b(news|headlines)\b")
_NEWS_TOPIC = re.compile(r"\b(?:news|headlines) (?:about|on|regarding) (?P<topic>.+)$")

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_EXAM_STOP = {
    "my", "the", "next", "exam", "exams", "paper", "papers", "mock", "mocks", "when",
    "is", "what", "whats", "what's", "how", "long", "until", "till", "do", "i", "have",
    "today", "tomorrow", "this", "week", "a", "an", "of", "for", "left", "are", "there",
    "any", "upcoming", "all", "remaining", "time", "times", "start", "starts", "at",
    "on", "in", "it", "s", "me", "tell", "about", "show", "check", "read", "list", "much",
    "free", "between", "them", "is", "was", "be", "will", "first", "last", "after",
    "before", "up", "coming", *_WEEKDAYS,
}


def enabled() -> bool:
    return os.environ.get("JARVIS_GOAL_ROUTES", "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def _when(t: str) -> str:
    if "tomorrow" in t:
        return "tomorrow"
    if "today" in t or "tonight" in t:
        return "today"
    for day in _WEEKDAYS:
        if day in t:
            return day
    if re.search(r"\b(all|week|upcoming|left|remaining|every)\b", t):
        return "all"
    return "next"


def _exam_route(t: str, subjects: set[str]) -> tuple[str, dict]:
    words = [w for w in re.findall(r"[a-z']+", t) if w not in _EXAM_STOP]
    named = [w for w in words if w in subjects]
    if named:
        return "exam_schedule", {"action": "find", "subject": named[0]}
    return "exam_times", {"when": _when(t)}


def _calendar_days(t: str) -> int:
    if "today" in t or "tonight" in t:
        return 1
    if "tomorrow" in t:
        return 2
    if re.search(r"\bmonth\b", t):
        return 30
    return 7


def route(text: str, subjects: set[str] | None = None) -> tuple[str, dict] | None:
    """(tool, args) for a read goal in a cleaned transcript, else None. Pure.

    `subjects` are lowercase exam subject words ("physics", "economics") so
    "when is my physics exam" finds that paper instead of the whole list.
    """
    t = (text or "").strip().lower()
    if not t or len(t.split()) > MAX_WORDS:
        return None
    if _WRITE.search(t) or _COMPOUND.search(t) or _ABOUT_JARVIS.search(t):
        return None
    if _MAIL.search(t):
        if _MAIL_ONE.search(t) and not re.search(r"\bemails\b|\bmails\b", t):
            return "gmail_read", {"ref": "latest"}
        return "gmail_inbox", {"n": 5, "unread_only": bool(_UNREAD.search(t))}
    if _EXAM.search(t):
        return _exam_route(t, subjects or set())
    if _WEATHER.search(t):
        return "weather_now", {}
    if _NEWS.search(t):
        m = _NEWS_TOPIC.search(t)
        return "news_digest", {"topic": m.group("topic")[:80] if m else ""}
    if _CALENDAR.search(t):
        return "calendar_upcoming", {"days": _calendar_days(t)}
    return None


def exam_subjects() -> set[str]:
    """Lowercase subject words from the exam store. Fail-soft."""
    try:
        import exams

        out: set[str] = set()
        for e in exams.exams():
            for field in (e.get("subject", ""), e.get("title", "")):
                out.update(
                    w
                    for w in re.findall(r"[a-z]+", str(field).lower())
                    if len(w) > 3 and w not in _EXAM_STOP
                )
        return out
    except Exception:
        return set()


async def execute(owners, tool: str, args: dict) -> tuple[bool, str]:
    """Run `tool` on the first owner that has it -> (ok, spoken line)."""
    for owner in owners:
        fn = getattr(type(owner), tool, None)
        if fn is None:
            continue
        try:
            out = await fn(owner, None, **args)
        except Exception as exc:  # ToolError carries the spoken reason
            return False, str(exc) or "That didn't work, Sir."
        return True, str((out or {}).get("say") or "Done.")
    return False, f"I can't run {tool} here, Sir."
