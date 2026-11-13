"""SQLite store shared by the calls modules.

One connection per (thread, db path), WAL mode, forward-only migrations
recorded in `schema_migrations`. The data dir is `JARVIS_CALLS_DIR`
(default `~/.jarvis/calls`); it is resolved on every call so tests can
point it at a tmp dir.
"""

from __future__ import annotations

import contextlib
import os
import re
import sqlite3
import threading
from pathlib import Path

DB_NAME = "calls.db"
DEFAULT_REGION = "65"  # Singapore

_local = threading.local()
_migrate_lock = threading.Lock()
_migrated: set[str] = set()

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            number TEXT NOT NULL UNIQUE,
            relationship TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            trust TEXT NOT NULL DEFAULT 'unknown'
                CHECK (trust IN ('family','work','unknown','blocked')),
            policy TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX idx_contacts_name ON contacts(name COLLATE NOCASE);
        CREATE TABLE rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL
                CHECK (kind IN ('answer_all','answer_unknown','reject','message_only')),
            start REAL,
            end REAL,
            daily_from TEXT,
            daily_to TEXT,
            message TEXT NOT NULL DEFAULT '',
            allow TEXT NOT NULL DEFAULT '[]',
            deny TEXT NOT NULL DEFAULT '[]',
            created_by TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            direction TEXT NOT NULL CHECK (direction IN ('in','out')),
            number TEXT NOT NULL,
            contact_id INTEGER,
            goal TEXT NOT NULL DEFAULT '',
            started_at REAL NOT NULL,
            ended_at REAL,
            outcome TEXT,
            summary TEXT
        );
        CREATE INDEX idx_calls_contact ON calls(contact_id);
        CREATE TABLE turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            call_id INTEGER NOT NULL,
            speaker TEXT NOT NULL CHECK (speaker IN ('caller','jarvis','owner')),
            text TEXT NOT NULL,
            ts REAL NOT NULL
        );
        CREATE INDEX idx_turns_call ON turns(call_id);
        CREATE TABLE audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            kind TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE contact_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER NOT NULL,
            fact TEXT NOT NULL,
            ts REAL NOT NULL
        );
        CREATE INDEX idx_facts_contact ON contact_facts(contact_id);
        -- append-only guarantees
        CREATE TRIGGER turns_no_update BEFORE UPDATE ON turns
            BEGIN SELECT RAISE(ABORT, 'turns is append-only'); END;
        CREATE TRIGGER turns_no_delete BEFORE DELETE ON turns
            BEGIN SELECT RAISE(ABORT, 'turns is append-only'); END;
        CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit
            BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
        CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit
            BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
        CREATE TRIGGER calls_no_delete BEFORE DELETE ON calls
            BEGIN SELECT RAISE(ABORT, 'calls is append-only'); END;
        CREATE TRIGGER calls_closed BEFORE UPDATE ON calls
            WHEN OLD.ended_at IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'call already ended'); END;
        """,
    ),
]


def calls_dir() -> Path:
    d = os.environ.get("JARVIS_CALLS_DIR", "").strip()
    return Path(d).expanduser() if d else Path.home() / ".jarvis" / "calls"


def db_path() -> Path:
    return calls_dir() / DB_NAME


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
    )
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    import time

    for version, sql in MIGRATIONS:
        if version in done:
            continue
        try:
            conn.execute("BEGIN IMMEDIATE")
            # re-check inside the write lock (another process may have won)
            if conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?", (version,)
            ).fetchone():
                conn.execute("COMMIT")
                continue
            for stmt in _split(sql):
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?,?)",
                (version, time.time()),
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise


def _split(script: str) -> list[str]:
    """Split a script into statements, keeping CREATE TRIGGER bodies whole."""
    out: list[str] = []
    buf: list[str] = []
    for line in script.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        stmt = "\n".join(buf).strip()
        if stmt.endswith(";") and sqlite3.complete_statement(stmt):
            out.append(stmt)
            buf = []
    return out


def connect() -> sqlite3.Connection:
    """Thread-local connection for the current db path."""
    path = db_path()
    key = str(path)
    conns: dict[str, sqlite3.Connection] = getattr(_local, "conns", None) or {}
    _local.conns = conns
    conn = conns.get(key)
    if conn is not None and path.exists():
        return conn
    if conn is not None:  # db file vanished (tmp dir wiped)
        conn.close()
        conns.pop(key, None)
        with _migrate_lock:
            _migrated.discard(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(key, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    with _migrate_lock:
        if key not in _migrated:
            _migrate(conn)
            _migrated.add(key)
    conns[key] = conn
    return conn


def close_all() -> None:
    """Close this thread's connections (tests / shutdown)."""
    for c in (getattr(_local, "conns", None) or {}).values():
        with contextlib.suppress(sqlite3.Error):
            c.close()
    _local.conns = {}
    with _migrate_lock:
        _migrated.clear()


def schema_version() -> int:
    row = connect().execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0] or 0)


def get_setting(key: str, default: str | None = None) -> str | None:
    row = connect().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    connect().execute(
        "INSERT INTO settings(key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def get_bool_setting(key: str, default: bool) -> bool:
    v = get_setting(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# --- number normalisation (E.164, default region SG) ---

_SG_MOBILE_LANDLINE = re.compile(r"^[3689]\d{7}$")


def normalize_number(raw: str, region: str = DEFAULT_REGION) -> str:
    """Return E.164 (`+<digits>`). Raise ValueError when not plausible.

    `+` or `00` prefix: international. 8 digits starting 3/6/8/9 (and `65`
    + those 8 digits): Singapore. Other bare digit strings of 11-15 digits
    are treated as already carrying a country code. Local numbers with a
    leading `0` are not guessed for non-SG regions.
    """
    s = str(raw or "").strip()
    if not s:
        raise ValueError("empty number")
    s = re.sub(r"(?i)\b(?:ext|x)\.?\s*\d+$", "", s)  # drop extension
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        raise ValueError(f"no digits in number: {raw!r}")
    if not plus and digits.startswith("00"):
        plus, digits = True, digits[2:]
    if plus:
        pass
    elif region == "65" and _SG_MOBILE_LANDLINE.match(digits):
        digits = "65" + digits
    elif len(digits) >= 11:
        pass  # assume country code present
    elif region == "65" and digits.startswith("65") and len(digits) == 10:
        pass
    else:
        raise ValueError(f"cannot normalise number: {raw!r}")
    if digits.startswith("0") or not 8 <= len(digits) <= 15:
        raise ValueError(f"invalid number: {raw!r}")
    if digits.startswith("65") and len(digits) != 10:
        raise ValueError(f"invalid Singapore number: {raw!r}")
    return "+" + digits


def try_normalize(raw: str, region: str = DEFAULT_REGION) -> str | None:
    try:
        return normalize_number(raw, region)
    except ValueError:
        return None
