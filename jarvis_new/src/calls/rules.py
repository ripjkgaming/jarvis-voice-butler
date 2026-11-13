"""Auto-reply rules and the call decision function."""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Literal

from . import calllog, contacts, store
from .contacts import Contact
from .store import try_normalize

KINDS = ("answer_all", "answer_unknown", "reject", "message_only")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclass
class Rule:
    id: int
    kind: Literal["answer_all", "answer_unknown", "reject", "message_only"]
    start: float | None = None
    end: float | None = None
    daily_from: str | None = None
    daily_to: str | None = None
    message: str = ""
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    created_by: str = ""
    active: bool = True


@dataclass
class Decision:
    action: Literal["answer", "message", "owner", "reject"]
    reason: str
    rule_id: int | None = None
    contact: Contact | None = None


def _json_list(s: str) -> list[str]:
    import json

    return [str(x) for x in json.loads(s or "[]")]


def _rule(r: sqlite3.Row) -> Rule:
    return Rule(
        id=r["id"],
        kind=r["kind"],
        start=r["start"],
        end=r["end"],
        daily_from=r["daily_from"],
        daily_to=r["daily_to"],
        message=r["message"],
        allow=_json_list(r["allow"]),
        deny=_json_list(r["deny"]),
        created_by=r["created_by"],
        active=bool(r["active"]),
    )


def _norm_list(items) -> list[str]:
    out: list[str] = []
    for it in items or []:
        n = try_normalize(str(it))
        if not n:
            raise ValueError(f"bad number in list: {it!r}")
        if n not in out:
            out.append(n)
    return out


def _hhmm(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not _HHMM.match(str(v)):
        raise ValueError(f"bad HH:MM: {v!r}")
    return str(v)


def add_rule(
    kind: str,
    start: float | None = None,
    end: float | None = None,
    duration: float | None = None,
    daily_from: str | None = None,
    daily_to: str | None = None,
    message: str = "",
    allow: list[str] | None = None,
    deny: list[str] | None = None,
    created_by: str = "owner",
    now: float | None = None,
) -> Rule:
    """`duration` (seconds) sets end = (start or now) + duration."""
    if kind not in KINDS:
        raise ValueError(f"bad rule kind: {kind!r}")
    t = time.time() if now is None else now
    if duration is not None:
        if duration <= 0:
            raise ValueError("duration must be positive")
        if end is not None:
            raise ValueError("give end or duration, not both")
        if start is None:
            start = t
        end = start + duration
    if start is not None and end is not None and end <= start:
        raise ValueError("end must be after start")
    df, dtt = _hhmm(daily_from), _hhmm(daily_to)
    if (df is None) != (dtt is None):
        raise ValueError("daily_from and daily_to go together")
    if df is not None and df == dtt:
        raise ValueError("daily window is empty")
    import json

    cur = store.connect().execute(
        "INSERT INTO rules(kind,start,end,daily_from,daily_to,message,allow,deny,created_by,active) "
        "VALUES (?,?,?,?,?,?,?,?,?,1)",
        (
            kind,
            start,
            end,
            df,
            dtt,
            message or "",
            json.dumps(_norm_list(allow)),
            json.dumps(_norm_list(deny)),
            created_by or "",
        ),
    )
    rule = get_rule(int(cur.lastrowid or 0))
    assert rule is not None
    calllog.audit(
        "rule_add",
        {
            "rule_id": rule.id,
            "kind": kind,
            "start": start,
            "end": end,
            "daily_from": df,
            "daily_to": dtt,
            "created_by": created_by,
        },
    )
    return rule


def get_rule(rule_id: int) -> Rule | None:
    r = store.connect().execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
    return _rule(r) if r else None


def cancel_rule(id: int | None = None) -> int:  # noqa: A002 (contract name)
    """Cancel one rule, or every active rule when id is None. Returns count."""
    conn = store.connect()
    if id is None:
        cur = conn.execute("UPDATE rules SET active=0 WHERE active=1")
    else:
        cur = conn.execute("UPDATE rules SET active=0 WHERE id=? AND active=1", (id,))
    n = cur.rowcount
    calllog.audit("rule_cancel", {"rule_id": id, "all": id is None, "count": n})
    return n


def list_rules(include_inactive: bool = False) -> list[Rule]:
    sql = "SELECT * FROM rules" + ("" if include_inactive else " WHERE active=1")
    return [_rule(r) for r in store.connect().execute(sql + " ORDER BY id")]


def _in_daily(rule: Rule, now: float) -> bool:
    if not rule.daily_from or not rule.daily_to:
        return True
    cur = dt.datetime.fromtimestamp(now).strftime("%H:%M")
    a, b = rule.daily_from, rule.daily_to
    return a <= cur < b if a < b else (cur >= a or cur < b)  # overnight wraps


def _live(rule: Rule, now: float) -> bool:
    if not rule.active:
        return False
    if rule.start is not None and now < rule.start:
        return False
    if rule.end is not None and now >= rule.end:
        return False
    return _in_daily(rule, now)


def active_rules(now: float | None = None) -> list[Rule]:
    """Rules live at `now`, newest first."""
    t = time.time() if now is None else now
    return [r for r in reversed(list_rules()) if _live(r, t)]


_RULE_ACTION = {"answer_all": "answer", "reject": "reject", "message_only": "message"}


def decide(number: str, now: float | None = None, log: bool = True) -> Decision:
    """Precedence: deny list > allow list > blocked/contact policy > rules > owner."""
    t = time.time() if now is None else now
    d = _decide(number, t)
    if log:
        calllog.audit(
            "decision",
            {
                "number": try_normalize(number) or str(number),
                "action": d.action,
                "reason": d.reason,
                "rule_id": d.rule_id,
                "contact_id": d.contact.id if d.contact else None,
            },
            ts=t,
        )
    return d


def _decide(number: str, t: float) -> Decision:
    num = try_normalize(number)
    contact = contacts.get_by_number(num) if num else None
    live = active_rules(t)
    if num:
        for r in live:
            if num in r.deny:
                return Decision("reject", "number on deny list", r.id, contact)
        for r in live:
            if num in r.allow:
                return Decision("answer", "number on allow list", r.id, contact)
    if contact is not None:
        if contact.trust == "blocked":
            return Decision("reject", "contact blocked", None, contact)
        ans = contact.policy.get("answer")
        if ans:
            action = {"auto": "answer"}.get(ans, ans)
            return Decision(action, f"contact policy: {ans}", None, contact)
    known = contact is not None
    for r in live:
        if r.kind == "answer_unknown":
            if not known:
                return Decision("answer", "rule: answer unknown callers", r.id, contact)
            continue  # known callers fall through to older rules / owner
        return Decision(_RULE_ACTION[r.kind], f"rule: {r.kind}", r.id, contact)
    return Decision("owner", "no rule applies", None, contact)
