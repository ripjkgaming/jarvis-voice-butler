"""Durable storage for WhatsApp messages, scan checkpoints, and consumer queues.

The database is intentionally local and private. Message identity is scoped to a
chat because WhatsApp message IDs are not guaranteed to be globally unique.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any


def _default_path() -> Path:
    home = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
    return home / "aware" / "messages.sqlite3"


class Store:
    """SQLite message store.

    ``ingest`` persists messages and their checkpoint in one transaction.
    Consumers are independent: the first ``pending(consumer)`` call creates
    that consumer's queue for every message stored so far.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self._lock = threading.RLock()
        if path is not None and str(path) == ":memory:":
            self.path = None
            self._db_path = ":memory:"
        else:
            self.path = Path(path) if path is not None else _default_path()
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with suppress(OSError):
                self.path.parent.chmod(0o700)
            self._db_path = str(self.path)
        self._conn = sqlite3.connect(self._db_path, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 30000")
            if self._db_path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
                self._conn.execute("PRAGMA synchronous = FULL")
                self._secure_files()
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    chat_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    ord INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    me INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    inserted_at REAL NOT NULL,
                    PRIMARY KEY (chat_id, id)
                );
                CREATE INDEX IF NOT EXISTS messages_order
                    ON messages(timestamp, chat_id, ord, id);
                CREATE INDEX IF NOT EXISTS messages_chat_order
                    ON messages(chat_id, timestamp, ord, id);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    chat_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    boundary REAL NOT NULL,
                    complete INTEGER NOT NULL,
                    error TEXT NOT NULL,
                    last_successful_scan REAL,
                    last_scan REAL NOT NULL,
                    last_message_id TEXT,
                    last_message_timestamp REAL,
                    last_message_order INTEGER,
                    last_scan_complete INTEGER NOT NULL DEFAULT 0,
                    bootstrap_cursor_id TEXT,
                    bootstrap_cursor_timestamp REAL,
                    bootstrap_cursor_order INTEGER,
                    incremental_cursor_id TEXT,
                    incremental_cursor_timestamp REAL,
                    incremental_cursor_order INTEGER
                );
                CREATE TABLE IF NOT EXISTS consumers (
                    name TEXT PRIMARY KEY,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS acknowledgements (
                    consumer TEXT NOT NULL REFERENCES consumers(name) ON DELETE CASCADE,
                    chat_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    acknowledged_at REAL NOT NULL,
                    PRIMARY KEY (consumer, chat_id, message_id)
                );
                """
            )
            self._migrate_checkpoints()
            self._secure_files()

    def _migrate_checkpoints(self) -> None:
        """Add checkpoint fields when opening a database from an earlier build."""
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(checkpoints)").fetchall()
        }
        columns = {
            "last_scan_complete": "INTEGER NOT NULL DEFAULT 0",
            "bootstrap_cursor_id": "TEXT",
            "bootstrap_cursor_timestamp": "REAL",
            "bootstrap_cursor_order": "INTEGER",
            "incremental_cursor_id": "TEXT",
            "incremental_cursor_timestamp": "REAL",
            "incremental_cursor_order": "INTEGER",
        }
        for name, declaration in columns.items():
            if name not in existing:
                self._conn.execute(
                    f"ALTER TABLE checkpoints ADD COLUMN {name} {declaration}"
                )

    def _secure_files(self) -> None:
        if self._db_path == ":memory:":
            return
        for candidate in (
            self._db_path,
            self._db_path + "-wal",
            self._db_path + "-shm",
        ):
            try:
                os.chmod(candidate, 0o600)
            except FileNotFoundError:
                pass
            except OSError:
                pass

    @staticmethod
    def _json(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _message_row(
        chat_id: str, message: dict[str, Any], index: int
    ) -> dict[str, Any]:
        if not isinstance(message, dict):
            raise TypeError("each message must be a dictionary")
        if message.get("id") is None or not str(message["id"]).strip():
            raise ValueError("message is missing a non-empty id")
        timestamp = float(message.get("timestamp", message.get("time", 0)))
        if not math.isfinite(timestamp):
            raise ValueError("message timestamp must be finite")
        raw_order = message.get("order", message.get("ordinal", index))
        order_value = float(raw_order)
        if not math.isfinite(order_value) or not order_value.is_integer():
            raise ValueError("message order must be a finite integer")
        order = int(order_value)
        return {
            "chat_id": str(chat_id),
            "id": str(message["id"]),
            "timestamp": timestamp,
            "order": order,
            "text": str(message.get("text", "") or ""),
            "sender": str(message.get("sender", "") or ""),
            "me": bool(message.get("me", False)),
            "payload": Store._json(message),
        }

    @staticmethod
    def _public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        payload = json.loads(row["payload"])
        return {
            "id": row["id"],
            "chat_id": row["chat_id"],
            "timestamp": row["timestamp"],
            "order": row["ord"] if isinstance(row, sqlite3.Row) else row["order"],
            "text": row["text"],
            "sender": row["sender"],
            "me": bool(row["me"]),
            "payload": payload,
        }

    def ingest(
        self,
        chat_id: str,
        name: str,
        messages: list[dict[str, Any]],
        *,
        boundary: float,
        complete: bool,
        now: float,
        error: str = "",
    ) -> list[dict[str, Any]]:
        """Persist new messages and scan progress atomically; return new rows.

        ``boundary`` is the initial history boundary. It is captured only when
        the chat is first seen, so a retry cannot move the bootstrap window.
        A completed bootstrap remains complete during subsequent incremental
        scans; an error string records the latest partial scan outcome.
        """
        chat_id = str(chat_id)
        if not chat_id.strip():
            raise ValueError("chat_id must be non-empty")
        prepared = [
            self._message_row(chat_id, message, i) for i, message in enumerate(messages)
        ]
        prepared.sort(key=lambda item: (item["timestamp"], item["order"], item["id"]))
        now = float(now)
        with self._lock:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                inserted = []
                for item in prepared:
                    cursor = conn.execute(
                        """INSERT OR IGNORE INTO messages
                        (chat_id,id,timestamp,ord,text,sender,me,payload,inserted_at)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                        (
                            item["chat_id"],
                            item["id"],
                            item["timestamp"],
                            item["order"],
                            item["text"],
                            item["sender"],
                            int(item["me"]),
                            item["payload"],
                            now,
                        ),
                    )
                    if cursor.rowcount:
                        inserted.append(item)
                previous = conn.execute(
                    "SELECT * FROM checkpoints WHERE chat_id=?", (chat_id,)
                ).fetchone()
                checkpoint_boundary = (
                    float(previous["boundary"]) if previous else float(boundary)
                )
                was_complete = bool(previous["complete"]) if previous else False
                is_complete = was_complete or bool(complete)
                # The ingestion watermark is derived from all durable messages,
                # not the current batch, so out-of-order history pages cannot
                # move it backwards.
                watermark = conn.execute(
                    """SELECT id,timestamp,ord FROM messages WHERE chat_id=?
                    ORDER BY timestamp DESC,ord DESC,id DESC LIMIT 1""",
                    (chat_id,),
                ).fetchone()
                oldest = conn.execute(
                    """SELECT id,timestamp,ord FROM messages WHERE chat_id=?
                    ORDER BY timestamp,ord,id LIMIT 1""",
                    (chat_id,),
                ).fetchone()
                previous_incremental = (
                    (
                        previous["incremental_cursor_id"],
                        previous["incremental_cursor_timestamp"],
                        previous["incremental_cursor_order"],
                    )
                    if previous
                    else (None, None, None)
                )
                incremental_cursor = (
                    (watermark["id"], watermark["timestamp"], watermark["ord"])
                    if complete and not error and watermark
                    else previous_incremental
                )
                conn.execute(
                    """INSERT INTO checkpoints
                    (chat_id,name,boundary,complete,error,last_successful_scan,last_scan,
                     last_message_id,last_message_timestamp,last_message_order,
                     last_scan_complete,bootstrap_cursor_id,bootstrap_cursor_timestamp,
                     bootstrap_cursor_order,incremental_cursor_id,incremental_cursor_timestamp,
                     incremental_cursor_order)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(chat_id) DO UPDATE SET name=excluded.name,
                    complete=excluded.complete,error=excluded.error,
                    last_successful_scan=excluded.last_successful_scan,
                    last_scan=excluded.last_scan,last_message_id=excluded.last_message_id,
                    last_message_timestamp=excluded.last_message_timestamp,
                    last_message_order=excluded.last_message_order,
                    last_scan_complete=excluded.last_scan_complete,
                    bootstrap_cursor_id=excluded.bootstrap_cursor_id,
                    bootstrap_cursor_timestamp=excluded.bootstrap_cursor_timestamp,
                    bootstrap_cursor_order=excluded.bootstrap_cursor_order,
                    incremental_cursor_id=excluded.incremental_cursor_id,
                    incremental_cursor_timestamp=excluded.incremental_cursor_timestamp,
                    incremental_cursor_order=excluded.incremental_cursor_order""",
                    (
                        chat_id,
                        str(name),
                        checkpoint_boundary,
                        int(is_complete),
                        str(error or ""),
                        now
                        if not error
                        else (previous["last_successful_scan"] if previous else None),
                        now,
                        watermark["id"] if watermark else None,
                        watermark["timestamp"] if watermark else None,
                        watermark["ord"] if watermark else None,
                        int(bool(complete) and not bool(error)),
                        oldest["id"] if oldest else None,
                        oldest["timestamp"] if oldest else None,
                        oldest["ord"] if oldest else None,
                        *incremental_cursor,
                    ),
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            self._secure_files()
        return [self._public(item) for item in inserted]

    def chat(self, chat_id: str) -> dict[str, Any] | None:
        """Return a chat checkpoint, or ``None`` before the first scan."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM checkpoints WHERE chat_id=?", (str(chat_id),)
            ).fetchone()
        if row is None:
            return None
        return {
            "chat_id": row["chat_id"],
            "name": row["name"],
            "boundary": row["boundary"],
            "complete": bool(row["complete"]),
            "error": row["error"],
            "last_successful_scan": row["last_successful_scan"],
            "last_scan": row["last_scan"],
            "last_message_id": row["last_message_id"],
            "last_message_timestamp": row["last_message_timestamp"],
            "last_message_order": row["last_message_order"],
            "last_scan_complete": bool(row["last_scan_complete"]),
            "bootstrap_cursor_id": row["bootstrap_cursor_id"],
            "bootstrap_cursor_timestamp": row["bootstrap_cursor_timestamp"],
            "bootstrap_cursor_order": row["bootstrap_cursor_order"],
            "incremental_cursor_id": row["incremental_cursor_id"],
            "incremental_cursor_timestamp": row["incremental_cursor_timestamp"],
            "incremental_cursor_order": row["incremental_cursor_order"],
        }

    def pending(
        self,
        consumer: str,
        limit: int = 100,
        *,
        complete_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Return oldest unacknowledged messages for an independent consumer.

        With ``complete_only=True``, only messages from chats whose initial
        history window has complete coverage are eligible.
        """
        consumer = str(consumer)
        limit = max(0, int(limit))
        with self._lock:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO consumers(name,created_at) VALUES (?,strftime('%s','now'))",
                    (consumer,),
                )
                coverage_filter = (
                    "JOIN checkpoints cp ON cp.chat_id=m.chat_id AND cp.complete=1"
                    if complete_only
                    else ""
                )
                rows = conn.execute(
                    f"""SELECT m.* FROM messages m {coverage_filter}
                    LEFT JOIN acknowledgements a
                    ON a.chat_id=m.chat_id AND a.message_id=m.id AND a.consumer=?
                    WHERE a.message_id IS NULL
                    ORDER BY m.timestamp,m.chat_id,m.ord,m.id LIMIT ?""",
                    (consumer, limit),
                ).fetchall()
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        return [self._public(row) for row in rows]

    @staticmethod
    def _identity(value: Any) -> tuple[str, str]:
        if isinstance(value, dict):
            chat_id = value.get("chat_id")
            message_id = value.get("id", value.get("message_id"))
        elif isinstance(value, (tuple, list)) and len(value) == 2:
            chat_id, message_id = value
        else:
            raise TypeError(
                "message identity must be (chat_id, id) or a message mapping"
            )
        if chat_id is None or message_id is None:
            raise ValueError("composite message identity requires chat_id and id")
        return str(chat_id), str(message_id)

    def acknowledge(self, consumer: str, message_ids: list[Any]) -> None:
        """Acknowledge exact ``(chat_id, message_id)`` identities for a consumer."""
        consumer = str(consumer)
        identities = [self._identity(value) for value in message_ids]
        with self._lock:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO consumers(name,created_at) VALUES (?,strftime('%s','now'))",
                    (consumer,),
                )
                conn.executemany(
                    """INSERT OR IGNORE INTO acknowledgements
                    (consumer,chat_id,message_id,acknowledged_at) VALUES (?,?,?,strftime('%s','now'))""",
                    [
                        (consumer, chat_id, message_id)
                        for chat_id, message_id in identities
                    ],
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def context(
        self,
        chat_id: str,
        limit: int = 20,
        *,
        around_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return a bounded chronological chat window.

        Without ``around_id`` this returns the newest ``limit`` messages. If
        ``around_id`` is supplied, the window includes that source message and
        as many preceding and following messages as fit.
        """
        limit = max(0, int(limit))
        chat_id = str(chat_id)
        if limit == 0:
            return []
        with self._lock:
            if around_id is None:
                rows = self._conn.execute(
                    """SELECT * FROM (SELECT * FROM messages WHERE chat_id=?
                    ORDER BY timestamp DESC,ord DESC,id DESC LIMIT ?)
                    ORDER BY timestamp,ord,id""",
                    (chat_id, limit),
                ).fetchall()
            else:
                source = self._conn.execute(
                    "SELECT * FROM messages WHERE chat_id=? AND id=?",
                    (chat_id, str(around_id)),
                ).fetchone()
                if source is None:
                    return []
                source_order = (
                    source["timestamp"],
                    source["ord"],
                    source["id"],
                )
                before_count = (limit - 1) // 2
                after_count = limit - 1 - before_count
                before = self._conn.execute(
                    """SELECT * FROM messages WHERE chat_id=?
                    AND (timestamp,ord,id) < (?,?,?)
                    ORDER BY timestamp DESC,ord DESC,id DESC LIMIT ?""",
                    (chat_id, *source_order, before_count),
                ).fetchall()
                after = self._conn.execute(
                    """SELECT * FROM messages WHERE chat_id=?
                    AND (timestamp,ord,id) > (?,?,?)
                    ORDER BY timestamp,ord,id LIMIT ?""",
                    (chat_id, *source_order, after_count),
                ).fetchall()
                # Use unused slots on the opposite side when the source is near
                # either edge, keeping the requested limit useful there too.
                remaining = limit - 1 - len(before) - len(after)
                if remaining and len(before) < before_count:
                    extra = self._conn.execute(
                        """SELECT * FROM messages WHERE chat_id=?
                        AND (timestamp,ord,id) > (?,?,?)
                        ORDER BY timestamp,ord,id LIMIT ? OFFSET ?""",
                        (chat_id, *source_order, remaining, len(after)),
                    ).fetchall()
                    after.extend(extra)
                elif remaining and len(after) < after_count:
                    extra = self._conn.execute(
                        """SELECT * FROM messages WHERE chat_id=?
                        AND (timestamp,ord,id) < (?,?,?)
                        ORDER BY timestamp DESC,ord DESC,id DESC LIMIT ? OFFSET ?""",
                        (chat_id, *source_order, remaining, len(before)),
                    ).fetchall()
                    before.extend(extra)
                before.reverse()
                rows = [*before, source, *after]
        return [self._public(row) for row in rows]

    def close(self) -> None:
        """Close this store's SQLite connection."""
        with self._lock:
            self._conn.close()
