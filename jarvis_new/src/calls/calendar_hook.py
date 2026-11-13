"""Glue between the call agent and CalendarTools (W6).

The model emits tool tokens inside its reply, each with a flat JSON object:
  CHECK {"start": ISO, "end": ISO}
  BOOK {"title": str, "start": ISO, "end": ISO, "who": str}
  REMIND {"query": str, "when": ISO date or ""}
  CHANGE {"what": "cancel|move", "detail": str}
`apply_tokens` strips them, runs the tool, and returns the text to say. Spoken
results avoid phrases the outgoing guard treats as commitments.
"""

from __future__ import annotations

import json
import re

from . import intent
from .calendar_tools import CalendarTools, as_dt

TOKEN_RE = re.compile(r"\b(BOOK|CHECK|REMIND|CHANGE)\s*(\{[^{}]*\})")

PROMPT = (
    "CALENDAR TOOLS: you may add one token to a reply, followed by flat JSON "
    'with ISO times in the owner\'s timezone. CHECK {"start":..,"end":..} to '
    'test a slot. BOOK {"title":..,"start":..,"end":..,"who":..} for a '
    'requested meeting. REMIND {"query":..,"when":..} when asked whether '
    'something is in the calendar. CHANGE {"what":"cancel|move","detail":..} '
    "to cancel or move an existing event. If the time is vague (for example "
    "'tomorrow evening') ask for an exact time first. Never double-book. Never "
    "reveal event details beyond free/busy to unknown callers."
)


def bind(tools: CalendarTools, session) -> None:
    """Point tool audit/escalation at the call session (calllog + HUD event)."""
    import asyncio

    tools.contact = session.contact
    if tools._audit_fn is None:
        tools._audit_fn = lambda kind, detail: session._audit(kind, **detail)
    if tools._escalate_fn is None:
        tools._escalate_fn = lambda reason: asyncio.ensure_future(
            session._escalate(reason)
        )


def prompt_lines(tools: CalendarTools) -> str:
    return tools.context_line() + "\n" + PROMPT


def _fmt(iso: str) -> str:
    d = as_dt(iso)
    return d.strftime("%A %d %B at %H:%M").replace(" 0", " ")


def _alts(items: list[dict]) -> str:
    return ", ".join(_fmt(i["start"]) for i in items[:3])


def _load(raw: str) -> dict:
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def _run(tools: CalendarTools, name: str, a: dict, owner: str) -> str:
    trust = tools._trust()
    try:
        if name == "CHECK":
            r = tools.check_availability(a["start"], a["end"])
            if r.get("error"):
                return "That time range doesn't look right. Could you repeat it?"
            if r["free"]:
                return "That slot looks free."
            return (
                "That slot is taken. Nearby free times: "
                + _alts(r["suggestions"])
                + "."
            )
        if name == "BOOK":
            p = tools.propose_booking(
                a.get("title") or "Meeting", a["start"], a["end"], a.get("who", "")
            )
            if p.status == "hold_pending_owner":
                return (
                    f"I can't make bookings for you directly. I've noted the request "
                    f"as a tentative hold and {owner} will confirm."
                )
            if p.status == "denied" and p.clashes:
                alt = _alts(p.suggestions)
                return "That time is already taken." + (
                    f" I could offer {alt}." if alt else " I have no nearby free slots."
                )
            if p.status == "denied":
                return f"I can't take that booking: {p.reason}. Could you suggest another time?"
            r = tools.confirm_booking(p.id)
            if r["status"] == "confirmed":
                return f"Done, {p.title} is in the calendar for {_fmt(p.start.isoformat())}."
            if r["status"] == "denied":
                return "That time just became unavailable. " + _alts(p.suggestions)
            return (
                f"I've noted {p.title} for {_fmt(p.start.isoformat())} as a request. "
                f"{owner} has to approve it, and will get back to you."
            )
        if name == "REMIND":
            r = tools.reminder_lookup(a.get("query", ""), a.get("when") or None)
            if trust in ("unknown", "blocked", ""):
                return "I can't share calendar details. I'll pass your question to the owner."
            if r["in_calendar"]:
                e = r["event"]
                s = _fmt(e["start"])
                if r["ambiguous"]:
                    other = r["nearby"][0]
                    return (
                        f"There are two: {e['title']} on {s}, and "
                        f"{other['title']} on {_fmt(other['start'])}. Which one?"
                    )
                return f"Yes, {e['title']} {'was' if r['past'] else 'is'} on {s}."
            if r["nearby"]:
                n = r["nearby"][0]
                return (
                    f"I don't see that exactly. Something close is {n['title']} on "
                    f"{_fmt(n['start'])}. It isn't in the calendar as asked."
                )
            return (
                "I don't see that in the calendar. I'll let the owner know you asked."
            )
        if name == "CHANGE":
            tools.request_change(str(a.get("what", "change")), str(a.get("detail", "")))
            return f"I can't change existing events. I've asked {owner} to handle that."
    except (KeyError, ValueError, TypeError):
        return "I couldn't read that time. Could you give me the exact day and time?"
    return ""


def apply_tokens(
    tools: CalendarTools, reply: str, owner: str = "the owner"
) -> tuple[str, list[str]]:
    """Return (reply without tokens, tool result sentences to say)."""
    results: list[str] = []

    def sub(m: re.Match) -> str:
        results.append(_run(tools, m.group(1), _load(m.group(2)), owner))
        return ""

    clean = " ".join(TOKEN_RE.sub(sub, reply).split())
    return clean, [r for r in results if r]


_FALSE_CONFIRM = re.compile(
    r"\b(i('ve| have) (booked|added|scheduled|put|confirmed)|(is|it's|it is) (now )?"
    r"(booked|confirmed|in the calendar|on the calendar)|you('re| are) (all set|booked)|"
    r"all set|booking (is )?confirmed|done[,.!]|consider it done)\b",
    re.I,
)
FALSE_CONFIRM_FIX = (
    "I can't confirm bookings myself. I'll pass the request to the owner to approve."
)


def false_confirmation(reply: str, ran_tool: bool) -> bool:
    """True if the model claims a booking happened but no tool ran."""
    return not ran_tool and bool(_FALSE_CONFIRM.search(reply or ""))


def handle_caller_text(
    tools: CalendarTools, state: intent.State, text: str, owner: str = "the owner"
) -> str | None:
    """Code-level intent: run the tool and return the sentence(s) to say.

    None means no calendar intent: the LLM answers. The tool result is
    authoritative; the model never phrases or skips it.
    """
    open_p = [p for p in tools.proposals.values() if p.status != "confirmed"
              and p.status != "denied"]  # fmt: skip
    last = list(tools.proposals.values())[-1:] or [None]
    try:
        res = intent.parse(text, tools.now(), state, bool(open_p))
    except Exception:
        return None  # parser bug must never break a call: fall back to the LLM
    if res is None:
        return None
    if isinstance(res, intent.Clarify):
        tools.audit("calendar_clarify", question=res.question)
        return res.question
    if res.kind == "WITHDRAW":
        if open_p:
            for p in open_p:
                tools.cancel_proposal(p.id)
            return "No problem, I've withdrawn that request."
        if last[0] is not None and last[0].status == "confirmed":
            return _run(
                tools,
                "CHANGE",
                {"what": "cancel", "detail": res.args.get("detail", "")},
                owner,
            )
        return "Okay, I'll leave it."
    out = _run(tools, res.kind, res.args, owner)
    if res.note and res.kind in ("BOOK", "CHECK"):
        out = f"{out} {res.note}".strip()
    return out


__all__ = [
    "TOKEN_RE", "apply_tokens", "bind", "false_confirmation",
    "handle_caller_text", "prompt_lines",
]  # fmt: skip
