"""Durable, independent task, event and sourced-evidence foundation."""

import datetime as dt
import json
import sqlite3
import uuid
from pathlib import Path

_ALLOWED = {
    "queued": {"running", "cancelled"},
    "running": {"done", "failed", "paused", "cancelled"},
    "paused": {"running", "cancelled"},
    "done": set(),
    "failed": set(),
    "cancelled": set(),
}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


class FoundationStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, k TEXT UNIQUE, goal TEXT, steps TEXT, status TEXT, created TEXT, updated TEXT)"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, kind TEXT, subject TEXT)"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, content TEXT, source TEXT, observed TEXT)"
            )

    def _db(self):
        return sqlite3.connect(self.path, timeout=3)

    @staticmethod
    def _task(row):
        return (
            dict(
                zip(
                    (
                        "id",
                        "key",
                        "goal",
                        "steps",
                        "status",
                        "created_at",
                        "updated_at",
                    ),
                    (
                        row[0],
                        row[1],
                        row[2],
                        json.loads(row[3]),
                        row[4],
                        row[5],
                        row[6],
                    ),
                )
            )
            if row
            else None
        )

    def create_task(self, goal, steps, key=None):
        if not goal.strip() or not steps:
            raise ValueError("A goal and at least one step are required")
        task_id = uuid.uuid4().hex
        with self._db() as con:
            con.execute(
                "INSERT OR IGNORE INTO tasks VALUES(?,?,?,?,?,?,?)",
                (task_id, key, goal, json.dumps(steps), "queued", now(), now()),
            )
            row = con.execute(
                "SELECT * FROM tasks WHERE k=? "
                if key
                else "SELECT * FROM tasks WHERE id=?",
                (key or task_id,),
            ).fetchone()
        return self._task(row)

    def get_task(self, task_id):
        with self._db() as con:
            return self._task(
                con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            )

    def tasks(self, limit=20):
        with self._db() as con:
            return [
                self._task(row)
                for row in con.execute(
                    "SELECT * FROM tasks ORDER BY created DESC LIMIT ?", (limit,)
                )
            ]

    def transition(self, task_id, status):
        with self._db() as con:
            row = con.execute(
                "SELECT status FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if not row or status not in _ALLOWED[row[0]]:
                raise ValueError("Invalid task transition")
            con.execute(
                "UPDATE tasks SET status=?,updated=? WHERE id=?",
                (status, now(), task_id),
            )
        return self.get_task(task_id)

    def pause_interrupted(self):
        with self._db() as con:
            cur = con.execute(
                "UPDATE tasks SET status='paused',updated=? WHERE status='running'",
                (now(),),
            )
            return cur.rowcount

    def event(self, kind, subject):
        # Caller controls a fixed, non-sensitive subject, never payloads.
        with self._db() as con:
            con.execute(
                "INSERT INTO events(at,kind,subject) VALUES(?,?,?)",
                (now(), kind[:60], subject[:120]),
            )

    def events(self, limit=30):
        with self._db() as con:
            rows = con.execute(
                "SELECT at,kind,subject FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(zip(("at", "kind", "subject"), row)) for row in reversed(rows)]

    def remember(self, content, source):
        if not source or not source.strip() or not content.strip():
            raise ValueError("Evidence requires content and a source")
        row = {
            "id": uuid.uuid4().hex,
            "content": content,
            "source": source,
            "observed_at": now(),
        }
        with self._db() as con:
            con.execute(
                "INSERT INTO evidence VALUES(?,?,?,?)",
                (row["id"], content, source, row["observed_at"]),
            )
        return row

    def search_evidence(self, query, limit=10):
        with self._db() as con:
            rows = con.execute(
                "SELECT id,content,source,observed FROM evidence WHERE content LIKE ? ORDER BY observed DESC LIMIT ?",
                ("%" + query.replace("%", "") + "%", limit),
            ).fetchall()
        return [
            dict(zip(("id", "content", "source", "observed_at"), row)) for row in rows
        ]
