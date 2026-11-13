"""Durable scheduled outbound call jobs.

Reuses the scheduled_tasks machinery (goodnight admission fence, timezone, time
parsing, grace window). A job is claimed (``dispatching``) before the dial
callback runs. A crash without a receipt leaves it ``uncertain``; it is never
replayed. Jobs overdue beyond the grace window are ``missed``.

The dial callback is injectable: ``dial(job: dict) -> receipt`` where receipt is
``True`` or ``{"status": "queued|started|blocked|failed", "reason": str}``.
Rule-window expiry is handled by ``RuleExpiry``.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path

from goodnight_fence import admission
from scheduled_tasks import GRACE_S, HORIZON_S, ScheduleError, enabled, timezone

log = logging.getLogger("calls.scheduler")

_FINAL_OK = {"queued", "started", "blocked", "failed"}


def calls_dir() -> Path:
    env = os.environ.get("JARVIS_CALLS_DIR", "").strip()
    if env:
        return Path(env)
    home = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(home) if home else Path.home() / ".jarvis") / "calls"


def store_path() -> Path:
    return calls_dir() / "scheduled_calls.sqlite"


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else store_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ScheduleError("The call schedule store is unavailable.")
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS scheduled_calls (id TEXT PRIMARY KEY,"
                " request_key TEXT UNIQUE, contact_id INTEGER, contact_name TEXT,"
                " number TEXT, goal TEXT, due REAL, timezone TEXT, created REAL,"
                " status TEXT, reason TEXT, claimed REAL)"
            )
        with contextlib.suppress(OSError):
            self.path.chmod(0o600)

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @admission
    def create(
        self,
        contact_name,
        number,
        goal,
        due,
        *,
        contact_id=None,
        now=None,
        request_id=None,
    ):
        now = time.time() if now is None else now
        if not enabled():
            raise ScheduleError(
                "Scheduled calls are disabled. Enable the scheduling worker first."
            )
        contact_name = " ".join(str(contact_name or "").split())
        number = str(number or "").strip()
        goal = " ".join(str(goal or "").split())
        if not contact_name or not number:
            raise ScheduleError("A scheduled call needs a contact with a number.")
        if len(goal) > 300:
            raise ScheduleError("Give a call goal up to 300 characters.")
        if not math.isfinite(due) or not now < due <= now + HORIZON_S:
            raise ScheduleError("Choose a future time within the next year.")
        request_key = f"{request_id}|{number}|{goal}" if request_id else None
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if request_key:
                row = db.execute(
                    "SELECT * FROM scheduled_calls WHERE request_key=?", (request_key,)
                ).fetchone()
                if row:
                    return dict(row)
            job_id = "call-" + secrets.token_hex(8)
            db.execute(
                "INSERT INTO scheduled_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    request_key,
                    contact_id,
                    contact_name,
                    number,
                    goal,
                    due,
                    str(timezone()),
                    now,
                    "pending",
                    "",
                    None,
                ),
            )
            return dict(
                db.execute(
                    "SELECT * FROM scheduled_calls WHERE id=?", (job_id,)
                ).fetchone()
            )

    def get(self, job_id):
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM scheduled_calls WHERE id=?", (job_id,)
            ).fetchone()
            return dict(row) if row else None

    def list(self, limit=20, pending_only=False):
        where = "WHERE status IN ('pending','dispatching')" if pending_only else ""
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM scheduled_calls "
                    + where
                    + " ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, due LIMIT ?",
                    (max(1, min(100, limit)),),
                )
            ]

    def cancel(self, job_id):
        with self.db() as db:
            return bool(
                db.execute(
                    "UPDATE scheduled_calls SET status='cancelled',reason='' "
                    "WHERE id=? AND status IN ('pending','dispatching')",
                    (job_id,),
                ).rowcount
            )

    def cancel_for_contact(self, contact_name):
        with self.db() as db:
            return db.execute(
                "UPDATE scheduled_calls SET status='cancelled',reason='' "
                "WHERE lower(contact_name)=lower(?) AND status IN ('pending','dispatching')",
                (contact_name,),
            ).rowcount

    def tick(self, *, now=None, dial: Callable | None = None, clock=None):
        if not enabled():
            return 0
        clock = clock or (time.time if now is None else lambda: now)
        now = clock() if now is None else now
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE scheduled_calls SET status='uncertain',"
                "reason='Dispatch ended without a receipt; not replayed' "
                "WHERE status='dispatching' AND claimed<?",
                (now - GRACE_S,),
            )
            db.execute(
                "UPDATE scheduled_calls SET status='missed',"
                "reason='Scheduled time passed while unavailable' "
                "WHERE status='pending' AND due<?",
                (now - GRACE_S,),
            )
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM scheduled_calls WHERE status='pending' AND due<=? "
                    "ORDER BY due LIMIT 20",
                    (now,),
                )
            ]
            for row in rows:
                db.execute(
                    "UPDATE scheduled_calls SET status='dispatching',claimed=? WHERE id=?",
                    (now, row["id"]),
                )
        count = 0
        for row in rows:
            # Serialize cancellation against the short dial handoff.
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                cur = db.execute(
                    "SELECT status FROM scheduled_calls WHERE id=?", (row["id"],)
                ).fetchone()
                if cur[0] != "dispatching":
                    continue
                dispatch_now = clock()
                if dispatch_now > row["due"] + GRACE_S:
                    db.execute(
                        "UPDATE scheduled_calls SET status='missed',"
                        "reason='Scheduled time passed before dial' WHERE id=?",
                        (row["id"],),
                    )
                    continue
                status, reason = "failed", "Dialer unavailable"
                try:
                    receipt = (dial or _no_dialer)(row)
                    if receipt is True:
                        receipt = {"status": "queued", "reason": ""}
                    if isinstance(receipt, dict) and receipt.get("status") in _FINAL_OK:
                        status = receipt["status"]
                        reason = str(receipt.get("reason", ""))[:160]
                except Exception:
                    log.warning("scheduled call dial failed", exc_info=True)
                db.execute(
                    "UPDATE scheduled_calls SET status=?,reason=? WHERE id=?",
                    (status, reason, row["id"]),
                )
                count += 1
        return count


def _no_dialer(row):
    return {"status": "failed", "reason": "No dialer configured"}


class RuleExpiry:
    """Notices rule windows that ended and tidies them (audit + cancel)."""

    def __init__(self, rules=None, calllog=None):
        self._rules, self._calllog = rules, calllog
        self._seen: set[int] = set()

    def _mods(self):
        rules = self._rules
        if rules is None:
            from calls import rules as rules
        log_mod = self._calllog
        if log_mod is None:
            with contextlib.suppress(ImportError):
                from calls import calllog as log_mod
        return rules, log_mod

    def check(self, now=None) -> list[int]:
        now = time.time() if now is None else now
        rules, calllog = self._mods()
        current = {r.id for r in rules.active_rules(now)}
        expired = sorted(self._seen - current)
        self._seen = current
        done = []
        for rid in expired:
            # cancel_rule returns 0 if it was already cancelled by the user.
            if rules.cancel_rule(rid):
                done.append(rid)
                if calllog is not None:
                    with contextlib.suppress(Exception):
                        calllog.audit("rule_expired", {"rule_id": rid, "at": now})
        return done


def start_thread(dial, interval=1.0, store=None, expiry: RuleExpiry | None = None):
    """Background loop: tick scheduled calls and expire rule windows."""
    stop = threading.Event()

    def loop():
        st = store
        while not stop.is_set():
            with contextlib.suppress(Exception):
                if enabled():
                    st = st or Store()
                    st.tick(dial=dial)
                if expiry is not None:
                    expiry.check()
            stop.wait(max(0.1, interval))

    thread = threading.Thread(target=loop, name="scheduled-calls", daemon=True)
    thread.start()
    return thread, stop
