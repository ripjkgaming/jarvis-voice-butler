"""Append-only call log and audit trail (enforced by DB triggers)."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from . import store
from .store import try_normalize

SPEAKERS = ("caller", "jarvis", "owner")


def _row(r: sqlite3.Row) -> dict[str, Any]:
    return dict(r)


def audit(kind: str, detail: dict | None = None, ts: float | None = None) -> int:
    cur = store.connect().execute(
        "INSERT INTO audit(ts, kind, detail) VALUES (?,?,?)",
        (
            time.time() if ts is None else ts,
            str(kind),
            json.dumps(detail or {}, default=str),
        ),
    )
    return int(cur.lastrowid or 0)


def list_audit(n: int = 50, kind: str | None = None) -> list[dict]:
    """Newest first."""
    sql, args = "SELECT * FROM audit", []
    if kind:
        sql += " WHERE kind=?"
        args.append(kind)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(0, int(n)))
    out = []
    for r in store.connect().execute(sql, args):
        d = _row(r)
        d["detail"] = json.loads(d["detail"] or "{}")
        out.append(d)
    return out


def start_call(
    direction: str,
    number: str,
    contact_id: int | None = None,
    goal: str = "",
    ts: float | None = None,
) -> int:
    if direction not in ("in", "out"):
        raise ValueError(f"bad direction: {direction!r}")
    num = try_normalize(number) or str(number or "").strip()
    cur = store.connect().execute(
        "INSERT INTO calls(direction, number, contact_id, goal, started_at) "
        "VALUES (?,?,?,?,?)",
        (direction, num, contact_id, goal or "", time.time() if ts is None else ts),
    )
    cid = int(cur.lastrowid or 0)
    audit(
        "call_start",
        {
            "call_id": cid,
            "direction": direction,
            "number": num,
            "contact_id": contact_id,
            "goal": goal or "",
        },
    )
    return cid


def add_turn(call_id: int, speaker: str, text: str, ts: float | None = None) -> int:
    if speaker not in SPEAKERS:
        raise ValueError(f"bad speaker: {speaker!r}")
    conn = store.connect()
    if not conn.execute("SELECT 1 FROM calls WHERE id=?", (call_id,)).fetchone():
        raise KeyError(f"no such call: {call_id}")
    cur = conn.execute(
        "INSERT INTO turns(call_id, speaker, text, ts) VALUES (?,?,?,?)",
        (call_id, speaker, str(text or ""), time.time() if ts is None else ts),
    )
    return int(cur.lastrowid or 0)


def end_call(
    call_id: int, outcome: str, summary: str = "", ts: float | None = None
) -> None:
    """Close a call once. A second end raises ValueError."""
    conn = store.connect()
    if not conn.execute("SELECT 1 FROM calls WHERE id=?", (call_id,)).fetchone():
        raise KeyError(f"no such call: {call_id}")
    try:
        conn.execute(
            "UPDATE calls SET ended_at=?, outcome=?, summary=? WHERE id=?",
            (time.time() if ts is None else ts, outcome, summary or "", call_id),
        )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"call {call_id} already ended") from e
    audit("call_end", {"call_id": call_id, "outcome": outcome})


def get_call(call_id: int) -> dict | None:
    conn = store.connect()
    r = conn.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
    if not r:
        return None
    d = _row(r)
    d["turns"] = [
        _row(t)
        for t in conn.execute(
            "SELECT speaker, text, ts FROM turns WHERE call_id=? ORDER BY id",
            (call_id,),
        )
    ]
    return d


def recent(n: int = 10, contact_id: int | None = None) -> list[dict]:
    """Newest first, without turns."""
    sql, args = "SELECT * FROM calls", []
    if contact_id is not None:
        sql += " WHERE contact_id=?"
        args.append(contact_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(0, int(n)))
    return [_row(r) for r in store.connect().execute(sql, args)]
