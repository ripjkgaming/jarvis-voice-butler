"""Durable, deduplicated memories for the Always Aware expansion.

Structured event data and delivery state live in a private SQLite database.
Only a short derived ``summary`` is copied into the existing Obsidian daily
vault. ``status`` deliberately reports operational metadata only.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

from aware import state as st

TZ = ZoneInfo("Asia/Singapore")
MAX_SUMMARY = 500


def _db_path() -> Path:
    return st.aware_dir() / "expansion_memory.sqlite3"


@contextmanager
def _vault_lock():
    """Serialize the marker check and vault append across processes."""
    path = st.aware_dir() / "expansion_memory_vault.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.chmod(path, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    db = sqlite3.connect(path, timeout=10)
    os.chmod(path, 0o600)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        """CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            source TEXT NOT NULL,
            event_time REAL NOT NULL,
            confidence REAL NOT NULL,
            dedup_key TEXT NOT NULL,
            data TEXT NOT NULL,
            supersedes INTEGER REFERENCES memories(id),
            superseded_by INTEGER REFERENCES memories(id),
            vault_written INTEGER NOT NULL DEFAULT 0,
            vault_error TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            UNIQUE(kind, source, dedup_key)
        )"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS memories_time ON memories(event_time DESC)")
    return db


def _vault_append(when: float, text: str) -> bool:
    import memory

    if not memory.enabled():
        return False
    moment = dt.datetime.fromtimestamp(when, TZ)
    heading = f"# {moment.strftime('%A %d %B %Y')}"
    path = memory.daily_path(moment.date())
    marker = text.splitlines()[0] if text.splitlines() else ""
    with _vault_lock():
        if marker and path.exists() and marker in path.read_text(errors="replace"):
            return True
        return memory._append(path, text, heading)


def _clean_summary(data: dict) -> str:
    """Return a short derived sentence; never fall back to raw message text."""
    import memory

    value = data.get("summary") or data.get("description") or data.get("outcome") or ""
    return memory.redact(" ".join(str(value).split()))[:MAX_SUMMARY]


def _vault_line(row: sqlite3.Row) -> str:
    moment = dt.datetime.fromtimestamp(row["event_time"], TZ).strftime(
        "%Y-%m-%d %H:%M %Z"
    )
    summary = (
        _clean_summary(json.loads(row["data"]))
        or f"{row['kind'].replace('_', ' ')} recorded"
    )
    return (
        f"## Always Aware <!-- aware-memory:{row['id']} -->\n"
        f"- [{moment}] {summary} (source: {row['source']}; "
        f"kind: {row['kind']}; confidence: {row['confidence']:.2f})"
    )


def _deliver(memory_id: int) -> bool:
    with _connect() as db:
        row = db.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
    if row is None:
        return False
    if row["vault_written"]:
        return True
    try:
        ok = _vault_append(row["event_time"], _vault_line(row))
    except Exception:
        ok = False
    with _connect() as db:
        db.execute(
            "UPDATE memories SET vault_written=?, vault_error=? WHERE id=?",
            (int(ok), "" if ok else "vault append failed", memory_id),
        )
    return ok


def record(
    kind: str,
    source: str,
    timestamp: float,
    confidence: float,
    data: dict,
    key: str | None = None,
) -> bool:
    """Persist one sourced event and append its derived summary once.

    Returns True only for a newly inserted event. ``key`` is the stable source
    event identity; absent keys are derived from canonical data. Repeating a
    call is safe and retries a prior failed vault delivery.
    """
    kind, source = str(kind).strip()[:80], str(source).strip()[:160]
    if not kind or not source or not isinstance(data, dict):
        raise ValueError("kind, source and dictionary data are required")
    when = float(timestamp)
    confidence = min(1.0, max(0.0, float(confidence)))
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    dedup = (
        str(key) if key is not None else hashlib.sha256(encoded.encode()).hexdigest()
    )
    corrects = data.get("corrects") or data.get("supersedes")
    inserted = False
    with _connect() as db:
        existing = db.execute(
            "SELECT id FROM memories WHERE kind=? AND source=? AND dedup_key=?",
            (kind, source, dedup),
        ).fetchone()
        if existing:
            memory_id = int(existing["id"])
        else:
            target = None
            if corrects:
                corrects_source = str(data.get("corrects_source") or source)
                if isinstance(corrects, int):
                    found = db.execute(
                        "SELECT id FROM memories WHERE id=? AND source=?",
                        (corrects, corrects_source),
                    ).fetchone()
                else:
                    found = db.execute(
                        """SELECT id FROM memories WHERE dedup_key=? AND source=?
                           ORDER BY id DESC LIMIT 1""",
                        (str(corrects), corrects_source),
                    ).fetchone()
                target = int(found["id"]) if found else None
            cursor = db.execute(
                """INSERT INTO memories
                   (kind,source,event_time,confidence,dedup_key,data,supersedes,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (kind, source, when, confidence, dedup, encoded, target, time.time()),
            )
            memory_id = int(cursor.lastrowid)
            if target:
                db.execute(
                    "UPDATE memories SET superseded_by=? WHERE id=?",
                    (memory_id, target),
                )
            inserted = True
    _deliver(memory_id)
    return inserted


def retry_pending(limit: int = 100) -> int:
    """Retry vault writes; return the number successfully delivered."""
    with _connect() as db:
        ids = [
            r[0]
            for r in db.execute(
                "SELECT id FROM memories WHERE vault_written=0 ORDER BY id LIMIT ?",
                (max(0, int(limit)),),
            )
        ]
    return sum(_deliver(memory_id) for memory_id in ids)


def context(
    limit: int = 10,
    *,
    kinds: list[str] | None = None,
    source: str | None = None,
    current_only: bool = False,
) -> list[dict]:
    """Return a bounded set of recent derived memories for relevant context."""
    clauses, params = [], []
    if kinds:
        clauses.append("kind IN (" + ",".join("?" for _ in kinds) + ")")
        params.extend(kinds)
    if source is not None:
        clauses.append("source=?")
        params.append(source)
    if current_only:
        clauses.append("superseded_by IS NULL")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _connect() as db:
        rows = db.execute(
            f"SELECT * FROM memories{where} ORDER BY event_time DESC,id DESC LIMIT ?",
            (*params, max(0, min(int(limit), 100))),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "kind": row["kind"],
                "source": row["source"],
                "timestamp": row["event_time"],
                "confidence": row["confidence"],
                "summary": _clean_summary(json.loads(row["data"])),
                "state": json.loads(row["data"]).get("state", "active"),
                "supersedes": row["supersedes"],
                "superseded_by": row["superseded_by"],
            }
            for row in rows
        ]


def status() -> dict:
    """Explain memory intake and delivery without returning memory contents."""
    with _connect() as db:
        totals = db.execute(
            "SELECT COUNT(*) AS total, SUM(vault_written=0) AS retries FROM memories"
        ).fetchone()
        sources = {
            r["source"]: r["n"]
            for r in db.execute(
                "SELECT source,COUNT(*) AS n FROM memories GROUP BY source ORDER BY source"
            )
        }
        outcomes = {"vault_written": 0, "vault_write_pending": 0, "superseded": 0}
        for row in db.execute(
            "SELECT vault_written,superseded_by,COUNT(*) AS n FROM memories GROUP BY vault_written,superseded_by"
        ):
            outcomes[
                "vault_written" if row["vault_written"] else "vault_write_pending"
            ] += row["n"]
            if row["superseded_by"] is not None:
                outcomes["superseded"] += row["n"]
        last = db.execute("SELECT MAX(event_time) FROM memories").fetchone()[0]
    return {
        "total": int(totals["total"] or 0),
        "sources": sources,
        "outcomes": outcomes,
        "retries": int(totals["retries"] or 0),
        "last_event_at": last,
    }


_COMMITMENT = re.compile(
    r"\b(meet(?:ing)?|appointment|deadline|due\s+(?:on|by)|remind|"
    r"schedule|book(?:ing)?|reservation|class|flight|tomorrow|"
    r"next\s+(?:week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
    re.IGNORECASE,
)
_CONFIRMATION = re.compile(
    r"\b(yes|confirmed|that works|works for me|see you then)\b", re.I
)
_CANCELLATION = re.compile(
    r"\b(cancel(?:led|ed)?|call(?:ed)?\s+off|can't\s+make\s+it|"
    r"cannot\s+make\s+it|won't\s+make\s+it|drop\s+the\s+plan)\b",
    re.I,
)
_RESCHEDULE = re.compile(
    r"\b(reschedul\w*|postpon\w*|push\s+(?:it\s+)?back|"
    r"move\b[^.!?]{0,100}\bto\b|change\b[^.!?]{0,100}\bto\b)",
    re.I,
)
_PLAN_STOPWORDS = {
    "about",
    "after",
    "appointment",
    "before",
    "book",
    "booking",
    "can",
    "cancel",
    "change",
    "class",
    "could",
    "day",
    "for",
    "from",
    "have",
    "meet",
    "meeting",
    "move",
    "next",
    "plan",
    "please",
    "reschedule",
    "schedule",
    "see",
    "the",
    "then",
    "this",
    "to",
    "tomorrow",
    "week",
    "with",
    "would",
}


def _plan_terms(text: str) -> set[str]:
    return {
        term.lower()
        for term in re.findall(r"[a-zA-Z]{4,}", text)
        if term.lower() not in _PLAN_STOPWORDS
    }


def _matching_prior(text: str, prior: list[dict]) -> dict | None:
    """Match only a unique prior commitment sharing a distinctive subject."""
    terms = _plan_terms(text)
    if not terms:
        return None
    scored = []
    for row in prior:
        overlap = terms & _plan_terms(row.get("summary", ""))
        if overlap:
            scored.append((len(overlap), row))
    if not scored:
        return None
    best = max(score for score, _ in scored)
    winners = [row for score, row in scored if score == best]
    return winners[0] if len(winners) == 1 else None


def _message_memory(message: dict, prior: list[dict]) -> dict | None:
    """Make a small factual memory only for likely plans or clear confirmations."""
    text = " ".join(str(message.get("text") or "").split())
    if not text:
        return None
    matched = (
        _matching_prior(text, prior)
        if _CANCELLATION.search(text) or _RESCHEDULE.search(text)
        else None
    )
    if _CANCELLATION.search(text):
        state = "cancelled" if matched else "unmatched_change"
        category = (
            "cancellation of prior commitment"
            if matched
            else "unmatched cancellation mention"
        )
    elif _RESCHEDULE.search(text):
        state = "active" if matched else "unmatched_change"
        category = (
            "rescheduled commitment" if matched else "unmatched reschedule mention"
        )
    elif _COMMITMENT.search(text):
        state = "active"
        category = "upcoming commitment"
    elif prior and _CONFIRMATION.search(text):
        state = "active"
        category = "confirmation in an ongoing plan"
    else:
        return None
    import memory

    # A short excerpt retains useful dates/names but is redacted and never
    # interpreted as instructions or executed as an action.
    excerpt = memory.redact(text)[:240]
    sender = memory.redact(str(message.get("sender") or "participant"))[:60]
    result = {
        "summary": f"Possible {category} from {sender}: {excerpt}",
        "message_id": str(message.get("id") or ""),
        "state": state,
    }
    if matched and (_CANCELLATION.search(text) or _RESCHEDULE.search(text)):
        result["corrects"] = int(matched["id"])
    return result


def _wall_time() -> float:
    """Current wall clock used for live controls, independently of event time."""
    return time.time()


def _controls_on() -> bool:
    """Read live Always Aware controls at each queue-message boundary."""
    return st.status_word(st.load(), _wall_time()) == "on"


def process_pending(store=None, now: float | None = None) -> dict:
    """Extract cautious commitment memories from the durable message queue.

    The message consumer is acknowledged with composite identities only after
    any derived memory was durably recorded. Ordinary messages are skipped
    without creating vault noise. Relevant prior memories are bounded to five
    current commitment facts from that chat.
    """
    owned_store = store is None
    if owned_store:
        from aware.message_store import Store

        store = Store()
    now = time.time() if now is None else float(now)
    pending = []
    acknowledged, failed = [], []
    recorded = skipped = processed = 0
    stopped = ""
    try:
        if not _controls_on():
            stopped = st.status_word(st.load(), _wall_time())
        else:
            pending = store.pending("memory", complete_only=True)
            for message in pending:
                if not _controls_on():
                    stopped = st.status_word(st.load(), _wall_time())
                    if stopped == "on":
                        stopped = "controls_changed"
                    break
                processed += 1
                identity = (message["chat_id"], message["id"])
                source = f"whatsapp:{message['chat_id']}"
                try:
                    previous = context(
                        limit=5, kinds=["commitment"], source=source, current_only=True
                    )
                    previous = [row for row in previous if row["state"] == "active"]
                    data = _message_memory(message, previous)
                    if data is not None:
                        inserted = record(
                            "commitment",
                            source,
                            float(message.get("timestamp") or now),
                            0.72,
                            data,
                            key=f"message:{message['id']}",
                        )
                        recorded += int(inserted)
                    else:
                        skipped += 1
                    acknowledged.append(identity)
                except Exception:
                    failed.append(identity)
        if acknowledged:
            store.acknowledge("memory", acknowledged)
        return {
            "processed": processed,
            "recorded": recorded,
            "skipped": skipped,
            "acknowledged": len(acknowledged),
            "retrying": len(failed),
            "stopped": stopped,
            "at": now,
        }
    finally:
        if owned_store:
            store.close()
