"""Contact book with trust levels and per-contact call policy."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Literal

from . import calllog, store
from .store import normalize_number, try_normalize

Trust = Literal["family", "work", "unknown", "blocked"]
TRUSTS = ("family", "work", "unknown", "blocked")
ANSWER = ("auto", "message", "owner", "reject")
PERSONA = ("jarvis", "owner")
POLICY_KEYS = {"answer", "persona", "greeting", "allow_topics"}


@dataclass
class Contact:
    id: int
    name: str
    number: str
    relationship: str = ""
    notes: str = ""
    trust: Trust = "unknown"
    policy: dict = field(default_factory=dict)


def _check_policy(policy: dict | None) -> dict:
    p = dict(policy or {})
    bad = set(p) - POLICY_KEYS
    if bad:
        raise ValueError(f"unknown policy keys: {sorted(bad)}")
    if "answer" in p and p["answer"] not in ANSWER:
        raise ValueError(f"bad policy answer: {p['answer']!r}")
    if "persona" in p and p["persona"] not in PERSONA:
        raise ValueError(f"bad policy persona: {p['persona']!r}")
    if "allow_topics" in p and not isinstance(p["allow_topics"], list):
        raise ValueError("allow_topics must be a list")
    return p


def _contact(r: sqlite3.Row) -> Contact:
    return Contact(
        id=r["id"],
        name=r["name"],
        number=r["number"],
        relationship=r["relationship"],
        notes=r["notes"],
        trust=r["trust"],
        policy=json.loads(r["policy"] or "{}"),
    )


def add_contact(
    name: str,
    number: str,
    relationship: str = "",
    notes: str = "",
    trust: str = "unknown",
    policy: dict | None = None,
) -> Contact:
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("name required")
    if trust not in TRUSTS:
        raise ValueError(f"bad trust: {trust!r}")
    num = normalize_number(number)
    pol = _check_policy(policy)
    try:
        cur = store.connect().execute(
            "INSERT INTO contacts(name, number, relationship, notes, trust, policy) "
            "VALUES (?,?,?,?,?,?)",
            (name, num, relationship or "", notes or "", trust, json.dumps(pol)),
        )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"number already in contacts: {num}") from e
    c = get_contact(int(cur.lastrowid or 0))
    assert c is not None
    calllog.audit(
        "contact_add", {"id": c.id, "name": c.name, "number": c.number, "trust": trust}
    )
    return c


def get_contact(contact_id: int) -> Contact | None:
    r = (
        store.connect()
        .execute("SELECT * FROM contacts WHERE id=?", (contact_id,))
        .fetchone()
    )
    return _contact(r) if r else None


def get_by_number(n: str) -> Contact | None:
    num = try_normalize(n)
    if not num:
        return None
    r = (
        store.connect()
        .execute("SELECT * FROM contacts WHERE number=?", (num,))
        .fetchone()
    )
    return _contact(r) if r else None


def find(name: str) -> list[Contact]:
    """Case-insensitive substring match, exact matches first."""
    q = " ".join(str(name or "").split())
    if not q:
        return []
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = store.connect().execute(
        "SELECT * FROM contacts WHERE name LIKE ? ESCAPE '\\' COLLATE NOCASE "
        "ORDER BY (lower(name)=lower(?)) DESC, name COLLATE NOCASE, id",
        (f"%{esc}%", q),
    )
    return [_contact(r) for r in rows]


def update_contact(contact_id: int, **kw: Any) -> Contact:
    allowed = {"name", "number", "relationship", "notes", "trust", "policy"}
    bad = set(kw) - allowed
    if bad:
        raise ValueError(f"unknown fields: {sorted(bad)}")
    if get_contact(contact_id) is None:
        raise KeyError(f"no such contact: {contact_id}")
    sets, args = [], []
    for k, v in kw.items():
        if k == "number":
            v = normalize_number(v)
        elif k == "trust":
            if v not in TRUSTS:
                raise ValueError(f"bad trust: {v!r}")
        elif k == "policy":
            v = json.dumps(_check_policy(v))
        elif k == "name":
            v = " ".join(str(v or "").split())
            if not v:
                raise ValueError("name required")
        sets.append(f"{k}=?")
        args.append(v)
    if sets:
        try:
            store.connect().execute(
                f"UPDATE contacts SET {', '.join(sets)} WHERE id=?", [*args, contact_id]
            )
        except sqlite3.IntegrityError as e:
            raise ValueError("number already in contacts") from e
        calllog.audit("contact_update", {"id": contact_id, "fields": sorted(kw)})
    c = get_contact(contact_id)
    assert c is not None
    return c


def delete_contact(contact_id: int) -> bool:
    cur = store.connect().execute("DELETE FROM contacts WHERE id=?", (contact_id,))
    if cur.rowcount:
        store.connect().execute(
            "DELETE FROM contact_facts WHERE contact_id=?", (contact_id,)
        )
        calllog.audit("contact_delete", {"id": contact_id})
    return bool(cur.rowcount)


def list_contacts() -> list[Contact]:
    rows = store.connect().execute(
        "SELECT * FROM contacts ORDER BY name COLLATE NOCASE, id"
    )
    return [_contact(r) for r in rows]


def import_whatsapp(rows: list[dict]) -> int:
    """Import `{name, number}` rows. Returns the count newly added.

    Invalid numbers, blank names and numbers already present are skipped;
    existing contacts keep their trust/policy. Imported contacts are
    `unknown` trust: the owner raises trust explicitly.
    """
    added = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = " ".join(str(row.get("name") or "").split())
        num = try_normalize(str(row.get("number") or ""))
        if not name or not num or get_by_number(num):
            continue
        try:
            add_contact(name=name, number=num)
        except ValueError:
            continue
        added += 1
    calllog.audit("contacts_import_whatsapp", {"rows": len(rows or []), "added": added})
    return added
