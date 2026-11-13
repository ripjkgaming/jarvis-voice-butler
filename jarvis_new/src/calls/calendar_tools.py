"""Calendar tools for the call agent (W6).

`CalendarTools(backend)` is what the call agent talks to. The backend is a small
protocol: `list_events(start, end)`, `add_event(title, start, end, notes) -> id`,
`delete(event_id)`. `InMemoryCalendar` is the simulation backend.
`RealCalendarAdapter` wraps the repo's Google calendar and REFUSES to build in
simulations or tests.

Policy: never double-book; offer alternatives; unknown callers cannot book (only
a message / tentative hold flagged for the owner); other bookings need owner
approval unless the contact policy allows topic "booking", there is no clash,
and trust is family or work. Every booking decision and denial is audited.
"""

from __future__ import annotations

import datetime as dt
import itertools
import logging
import os
import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

log = logging.getLogger("calls.calendar")

TZ = ZoneInfo("Asia/Singapore")
SLOT_STEP = dt.timedelta(minutes=30)
DAY_START_H, DAY_END_H = 8, 21  # window for suggested slots
AUTO_TRUST = ("family", "work")
_STOP = {
    "a", "an", "the", "my", "our", "your", "about", "for", "to", "of", "on",
    "in", "at", "and", "with", "is", "do", "i", "have", "any", "me", "remind",
    "reminder", "that", "there", "was", "has", "what", "when", "did", "it",
}  # fmt: skip


def as_dt(v: Any, tz: dt.tzinfo = TZ) -> dt.datetime:
    """ISO string / date / datetime -> aware datetime (naive means tz)."""
    if isinstance(v, dt.datetime):
        d = v
    elif isinstance(v, dt.date):
        d = dt.datetime.combine(v, dt.time())
    else:
        d = dt.datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=tz)


@dataclass
class Event:
    id: str
    title: str
    start: dt.datetime
    end: dt.datetime
    notes: str = ""
    all_day: bool = False
    recurrence: str = ""  # "", "daily", "weekly" (in-memory expansion only)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "all_day": self.all_day,
            "notes": self.notes,
        }


def overlaps(a_start, a_end, b_start, b_end) -> bool:
    """Half-open: back-to-back events do not clash."""
    return a_start < b_end and b_start < a_end


class CalendarBackend(Protocol):
    def list_events(self, start: dt.datetime, end: dt.datetime) -> list[Event]: ...
    def add_event(
        self, title: str, start: dt.datetime, end: dt.datetime, notes: str = ""
    ) -> str: ...
    def delete(self, event_id: str) -> bool: ...


class InMemoryCalendar:
    """Seedable simulation calendar. `seed` rows: dicts with title,start,end
    (ISO), optional all_day, notes, recurrence, id."""

    def __init__(self, seed: list[dict] | None = None) -> None:
        self._events: dict[str, Event] = {}
        self._ids = itertools.count(1)
        for row in seed or []:
            self.seed(**row)

    def seed(
        self,
        title: str,
        start: Any,
        end: Any = None,
        notes: str = "",
        all_day: bool = False,
        recurrence: str = "",
        id: str | None = None,  # noqa: A002
    ) -> str:
        s = as_dt(start)
        if all_day:
            s = s.replace(hour=0, minute=0, second=0, microsecond=0)
            e = as_dt(end) if end else s + dt.timedelta(days=1)
        else:
            e = as_dt(end) if end else s + dt.timedelta(hours=1)
        eid = id or f"ev{next(self._ids)}"
        self._events[eid] = Event(eid, title, s, e, notes, all_day, recurrence)
        return eid

    def list_events(self, start, end) -> list[Event]:
        out: list[Event] = []
        for ev in self._events.values():
            step = {"daily": 1, "weekly": 7}.get(ev.recurrence)
            if not step:
                if overlaps(ev.start, ev.end, start, end):
                    out.append(ev)
                continue
            n = 0
            while n < 400:
                delta = dt.timedelta(days=step * n)
                s, e = ev.start + delta, ev.end + delta
                if s >= end:
                    break
                if overlaps(s, e, start, end):
                    out.append(replace(ev, start=s, end=e, recurrence=""))
                n += 1
        return sorted(out, key=lambda x: x.start)

    def add_event(self, title, start, end, notes="") -> str:
        return self.seed(title, start, end, notes)

    def delete(self, event_id: str) -> bool:
        return self._events.pop(event_id, None) is not None

    def __len__(self) -> int:
        return len(self._events)


def simulation_active() -> bool:
    for name in ("JARVIS_CALLS_SIMULATION", "JARVIS_CALLS_SIM"):
        if os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no"):
            return True
    return "PYTEST_CURRENT_TEST" in os.environ


class RealCalendarAdapter:
    """Adapter over src/calendar_actions.py (Google Calendar). Never in sims."""

    def __init__(self, *, actions: Any = None, google: Any = None) -> None:
        if simulation_active():
            raise RuntimeError("RealCalendarAdapter refused: simulation or test run")
        if actions is None:
            import calendar_actions as actions  # type: ignore[no-redef]
        if google is None:
            import google_api as google  # type: ignore[no-redef]
        self._actions, self._google = actions, google

    def _items_to_events(self, items: list[dict]) -> list[Event]:
        out = []
        for it in items:
            s, e = it.get("start") or {}, it.get("end") or {}
            all_day = "date" in s and "dateTime" not in s
            try:
                sd = as_dt(s.get("dateTime") or s.get("date"))
                ed = as_dt(e.get("dateTime") or e.get("date") or sd)
            except (ValueError, TypeError):
                continue
            out.append(
                Event(
                    str(it.get("id") or ""),
                    str(it.get("title") or it.get("summary") or ""),
                    sd,
                    ed,
                    all_day=all_day,
                )
            )
        return out

    def list_events(self, start, end) -> list[Event]:
        out: list[Event] = []
        day = start.date()
        while dt.datetime.combine(day, dt.time(), tzinfo=start.tzinfo) < end:
            res = self._actions.events_on(day.isoformat())
            out += self._items_to_events(res.get("events") or [])
            day += dt.timedelta(days=1)
        return [e for e in out if overlaps(e.start, e.end, start, end)]

    def add_event(self, title, start, end, notes="") -> str:
        res = self._actions.add(
            {
                "title": title,
                "date": start.date().isoformat(),
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M"),
            }
        )
        if not res.get("ok"):
            raise RuntimeError(res.get("say") or "calendar write not verified")
        return str(res.get("event_id") or "")

    def delete(self, event_id: str) -> bool:
        raise NotImplementedError("calls never delete real calendar events")


@dataclass
class Proposal:
    id: str
    title: str
    start: dt.datetime
    end: dt.datetime
    who: str
    status: str  # proposed | confirmed | denied | hold_pending_owner | escalated
    reason: str = ""
    contact_trust: str = "unknown"
    allow_auto: bool = False
    clashes: list[dict] = field(default_factory=list)
    suggestions: list[dict] = field(default_factory=list)
    event_id: str = ""

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["start"], d["end"] = self.start.isoformat(), self.end.isoformat()
        return d


def _tokens(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9']+", (text or "").casefold()):
        w = w.strip("'")
        if w in _STOP or len(w) < 2:
            continue
        if len(w) > 3 and w.endswith("s"):
            w = w[:-1]
        out.add(w)
    return out


class CalendarTools:
    """Tools the call agent may use. All times aware; naive input means SGT."""

    def __init__(
        self,
        backend: CalendarBackend,
        *,
        contact: Any = None,
        audit: Callable[[str, dict], Any] | None = None,
        escalate: Callable[[str], Any] | None = None,
        clock: Callable[[], dt.datetime] | None = None,
        tz: dt.tzinfo = TZ,
    ) -> None:
        self.backend = backend
        self.contact = contact
        self._audit_fn = audit
        self._escalate_fn = escalate
        self._clock = clock or (lambda: dt.datetime.now(tz))
        self.tz = tz
        self.proposals: dict[str, Proposal] = {}
        self._pid = itertools.count(1)

    # ---- plumbing ----
    def now(self) -> dt.datetime:
        n = self._clock()
        return n if n.tzinfo else n.replace(tzinfo=self.tz)

    def audit(self, kind: str, **detail: Any) -> None:
        if self._audit_fn is None:
            return
        try:
            self._audit_fn(kind, detail)
        except Exception:
            log.exception("calendar audit failed")

    def _escalate(self, reason: str) -> None:
        if self._escalate_fn is not None:
            try:
                self._escalate_fn(reason)
            except Exception:
                log.exception("calendar escalate failed")

    def _trust(self) -> str:
        return str(getattr(self.contact, "trust", "") or "unknown")

    def _allows_booking(self) -> bool:
        pol = getattr(self.contact, "policy", None) or {}
        return "booking" in (pol.get("allow_topics") or [])

    # ---- availability ----
    def _clashes(self, start, end) -> list[Event]:
        return [
            e
            for e in self.backend.list_events(start, end)
            if overlaps(e.start, e.end, start, end)
        ]

    def check_availability(self, start: Any, end: Any) -> dict:
        s, e = as_dt(start, self.tz), as_dt(end, self.tz)
        if e <= s:
            return {"free": False, "error": "end before start", "clashes": [],
                    "suggestions": []}  # fmt: skip
        clashes = self._clashes(s, e)
        res = {
            "free": not clashes,
            "clashes": [c.to_dict() for c in clashes],
            "suggestions": [] if not clashes else self._suggest(s, e),
        }
        self.audit("calendar_check", start=s.isoformat(), end=e.isoformat(),
                   free=res["free"])  # fmt: skip
        return res

    def _suggest(self, s: dt.datetime, e: dt.datetime, n: int = 3) -> list[dict]:
        """n nearest free slots (same length, inside 08:00-21:00, not past)."""
        dur = e - s
        now = self.now()
        anchor = s.replace(minute=(s.minute // 30) * 30, second=0, microsecond=0)
        cands = []
        for k in range(-96, 96 * 3):  # +-2 days back, 3 days forward
            c = anchor + SLOT_STEP * k
            ce = c + dur
            if c < now or c == s:
                continue
            if c.hour < DAY_START_H or (ce.hour, ce.minute) > (DAY_END_H, 0):
                continue
            if ce.date() != c.date():
                continue
            cands.append((abs(c - s), c, ce))
        cands.sort(key=lambda t: (t[0], t[1]))
        out: list[dict] = []
        for _, c, ce in cands:
            if self._clashes(c, ce):
                continue
            out.append({"start": c.isoformat(), "end": ce.isoformat()})
            if len(out) >= n:
                break
        return out

    # ---- booking ----
    def propose_booking(
        self, title: str, start: Any, end: Any, who: str = ""
    ) -> Proposal:
        s, e = as_dt(start, self.tz), as_dt(end, self.tz)
        pid = f"bk{next(self._pid)}"
        trust = self._trust()
        p = Proposal(pid, title, s, e, who, "proposed", contact_trust=trust)
        self.proposals[pid] = p

        def deny(status: str, reason: str) -> Proposal:
            p.status, p.reason = status, reason
            self.audit("booking_denied", proposal=pid, title=title,
                       start=s.isoformat(), who=who, trust=trust,
                       reason=reason, status=status)  # fmt: skip
            return p

        if e <= s:
            return deny("denied", "end is not after start")
        if s < self.now():
            return deny("denied", "time is in the past")
        clashes = self._clashes(s, e)
        if clashes:
            p.clashes = [c.to_dict() for c in clashes]
            p.suggestions = self._suggest(s, e)
            return deny("denied", "clash: would double-book")
        if trust in ("unknown", "blocked", ""):
            self._escalate(
                f"unknown caller wants a booking: {title} at {s.isoformat()}"
            )
            return deny("hold_pending_owner",
                        "unknown caller cannot book; message and tentative hold for owner")  # fmt: skip
        p.allow_auto = self._allows_booking() and trust in AUTO_TRUST
        self.audit("booking_proposed", proposal=pid, title=title, start=s.isoformat(),
                   end=e.isoformat(), who=who, trust=trust, auto=p.allow_auto)  # fmt: skip
        return p

    def confirm_booking(self, proposal_id: str, owner_approved: bool = False) -> dict:
        p = self.proposals.get(proposal_id)
        if p is None:
            self.audit(
                "booking_denied", proposal=proposal_id, reason="unknown proposal"
            )
            return {"ok": False, "status": "denied", "reason": "unknown proposal"}
        if p.status == "confirmed":
            return {"ok": True, "status": "confirmed", "event_id": p.event_id,
                    "reason": "already confirmed"}  # fmt: skip
        if p.status in ("denied", "hold_pending_owner") and not owner_approved:
            return {"ok": False, "status": p.status, "reason": p.reason}
        if not owner_approved and not p.allow_auto:
            p.status, p.reason = "escalated", "needs owner approval"
            self._escalate(
                f"booking needs owner approval: {p.title} at {p.start.isoformat()}"
            )
            self.audit("booking_escalated", proposal=p.id, title=p.title,
                       start=p.start.isoformat(), who=p.who, trust=p.contact_trust)  # fmt: skip
            return {"ok": False, "status": "escalated", "reason": p.reason}
        # recheck: calendar may have changed since the proposal
        if self._clashes(p.start, p.end):
            p.status, p.reason = "denied", "clash: calendar changed"
            p.suggestions = self._suggest(p.start, p.end)
            self.audit("booking_denied", proposal=p.id, reason=p.reason)
            return {"ok": False, "status": "denied", "reason": p.reason}
        p.event_id = self.backend.add_event(
            p.title, p.start, p.end, f"Booked by phone assistant for {p.who}".strip()
        )
        p.status = "confirmed"
        self.audit("booking_confirmed", proposal=p.id, title=p.title, event_id=p.event_id,
                   start=p.start.isoformat(), who=p.who,
                   by="owner" if owner_approved else "policy")  # fmt: skip
        return {"ok": True, "status": "confirmed", "event_id": p.event_id}

    def approve(self, proposal_id: str) -> dict:
        """Owner approval path (UI / voice command)."""
        return self.confirm_booking(proposal_id, owner_approved=True)

    def cancel_proposal(self, proposal_id: str) -> bool:
        """Caller changed their mind before confirmation."""
        p = self.proposals.get(proposal_id)
        if p is None or p.status == "confirmed":
            return False
        p.status, p.reason = "denied", "caller withdrew"
        self.audit("booking_withdrawn", proposal=proposal_id)
        return True

    def request_change(self, what: str, detail: str = "") -> dict:
        """Cancel / move of an existing event: never done by a caller; escalate."""
        self._escalate(f"caller wants to {what}: {detail}")
        self.audit("booking_change_escalated", what=what, detail=detail,
                   trust=self._trust())  # fmt: skip
        return {"ok": False, "status": "escalated"}

    # ---- reminders ----
    def reminder_lookup(self, query: str, when: Any = None) -> dict:
        now = self.now()
        if when is not None:
            w = as_dt(when, self.tz)
            lo = w.replace(hour=0, minute=0, second=0, microsecond=0)
            hi = lo + dt.timedelta(days=1)
        else:
            lo, hi = now - dt.timedelta(days=7), now + dt.timedelta(days=30)
        q = _tokens(query)
        full, part = [], []
        for ev in self.backend.list_events(lo, hi):
            t = _tokens(ev.title + " " + ev.notes)
            hit = q & t
            if not q or not hit:
                continue
            (full if hit == q else part).append(ev)
        full.sort(key=lambda e: (e.end < now, abs((e.start - now).total_seconds())))
        res: dict = {
            "in_calendar": bool(full),
            "event": full[0].to_dict() if full else None,
            "nearby": [e.to_dict() for e in (full[1:] + part)][:5],
            "ambiguous": len(full) > 1,
            "past": bool(full) and full[0].end < now,
        }
        self.audit("calendar_reminder_lookup", query=query, in_calendar=res["in_calendar"],
                   matches=len(full), trust=self._trust())  # fmt: skip
        return res

    # ---- prompt context ----
    def context_line(self, days: int = 2, limit: int = 6) -> str:
        now = self.now()
        evs = self.backend.list_events(now, now + dt.timedelta(days=days))[:limit]
        head = f"Calendar (owner's time {now.strftime('%a %Y-%m-%d %H:%M')} {now.tzname()}): "
        if not evs:
            return head + f"no events in the next {days} days."
        busy = "; ".join(
            f"{e.title} {e.start.strftime('%a %H:%M')}-{e.end.strftime('%H:%M')}"
            if not e.all_day
            else f"{e.title} all day {e.start.strftime('%a')}"
            for e in evs
        )
        return head + "busy: " + busy + "."
