"""Bridge between the call store and the Jarvis memory vault (`src/memory.py`).

Everything written to the vault or stored as a fact goes through
`memory.redact`. Vault writes honour `memory.enabled()`.
"""

from __future__ import annotations

import time

import memory

from . import calllog, contacts, rules, store
from .contacts import Contact

MAX_FACTS = 12
MAX_RECALL_CHARS = 1500


def _clean(text: str, cap: int = 300) -> str:
    return " ".join(memory.redact(text).split())[:cap]


def remember_person(contact_id: int, fact: str) -> bool:
    c = contacts.get_contact(contact_id)
    f = _clean(fact, 200)
    if c is None or not f:
        return False
    conn = store.connect()
    if conn.execute(
        "SELECT 1 FROM contact_facts WHERE contact_id=? AND fact=?", (contact_id, f)
    ).fetchone():
        return True  # already known
    conn.execute(
        "INSERT INTO contact_facts(contact_id, fact, ts) VALUES (?,?,?)",
        (contact_id, f, time.time()),
    )
    calllog.audit("memory_fact", {"contact_id": contact_id})
    if memory.enabled():
        name = memory.slug(f"Calls - {c.name}")
        memory._append(
            memory.threads_dir() / f"{name}.md",
            f"- Fact: {f} ({time.strftime('%Y-%m-%d')})",
            f"# {name}",
        )
    return True


def facts_for(contact_id: int) -> list[str]:
    rows = store.connect().execute(
        "SELECT fact FROM contact_facts WHERE contact_id=? ORDER BY id DESC LIMIT ?",
        (contact_id, MAX_FACTS),
    )
    return [r[0] for r in reversed(list(rows))]


def standing_instructions(
    contact: Contact | None, now: float | None = None
) -> list[str]:
    out: list[str] = []
    for r in rules.active_rules(now):
        if r.message and r.kind != "reject":
            out.append(f"Active rule ({r.kind}): {_clean(r.message)}")
    if contact:
        if contact.notes:
            out.append(f"Notes: {_clean(contact.notes)}")
        g = contact.policy.get("greeting")
        if g:
            out.append(f"Greeting: {_clean(g)}")
        topics = contact.policy.get("allow_topics")
        if topics:
            out.append(
                "Allowed topics: " + ", ".join(_clean(str(x), 40) for x in topics)
            )
    return out


def recall_for_call(contact: Contact | None, now: float | None = None) -> str:
    """Prompt-ready text: facts, last 3 call summaries, standing instructions."""
    if contact is None:
        ins = standing_instructions(None, now)
        return ("Unknown caller.\n" + "\n".join(ins)).strip()
    parts = [f"Caller: {contact.name} ({contact.relationship or contact.trust})"]
    facts = facts_for(contact.id)
    if facts:
        parts.append("Facts:\n" + "\n".join(f"- {f}" for f in facts))
    done = [c for c in calllog.recent(20, contact_id=contact.id) if c.get("summary")]
    if done:
        lines = []
        for c in done[:3]:
            day = time.strftime("%Y-%m-%d", time.localtime(c["started_at"]))
            lines.append(
                f"- {day} ({c['direction']}, {c['outcome']}): {_clean(c['summary'])}"
            )
        parts.append("Last calls:\n" + "\n".join(lines))
    ins = standing_instructions(contact, now)
    if ins:
        parts.append("Standing instructions:\n" + "\n".join(f"- {i}" for i in ins))
    return "\n".join(parts)[:MAX_RECALL_CHARS]


def write_call_summary(call_id: int) -> bool:
    """Append the finished call's summary to the vault Threads note."""
    call = calllog.get_call(call_id)
    if not call or call["ended_at"] is None or not memory.enabled():
        return False
    c = contacts.get_contact(call["contact_id"]) if call["contact_id"] else None
    who = c.name if c else call["number"]
    name = memory.slug(f"Calls - {who}")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(call["started_at"]))
    entry = (
        f"### {when}\n"
        f"- Direction: {call['direction']}\n"
        f"- Outcome: {_clean(call['outcome'] or '?', 100)}\n"
        f"- Summary: {_clean(call['summary'] or '(none)', 600)}"
    )
    ok = memory._append(memory.threads_dir() / f"{name}.md", entry, f"# {name}")
    if ok:
        calllog.audit("memory_summary", {"call_id": call_id})
    return ok
