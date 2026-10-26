"""Durable public-evidence monitoring. No session, audio or external-send imports."""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import math
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class Event:
    key: str
    ts: float
    facts: dict


@dataclass(frozen=True)
class Session:
    owner: str
    scope: str
    identity: str
    authorized: bool
    call_active: bool
    watch_active: bool


def validate(source, event, filters):
    if not isinstance(filters, dict):
        raise ValueError("Explicit filter object required")
    if source == "jolpica" and event in {"schedule", "results"}:
        if set(filters) != {"season", "round"}:
            raise ValueError("Exact numeric season and round required")
        if not all(type(filters[k]) is int for k in filters):
            raise ValueError("Numeric filters required")
        if not 1950 <= filters["season"] <= 2100 or not 1 <= filters["round"] <= 30:
            raise ValueError("Season/round out of range")
    elif source == "openf1" and event == "race_control":
        if set(filters) != {"session_key", "start", "end"}:
            raise ValueError(
                "Historical numeric session and bounded UTC start/end required"
            )
        if type(filters["session_key"]) is not int or filters["session_key"] <= 0:
            raise ValueError("No latest/live session")
        for k in ("start", "end"):
            if type(filters[k]) not in (int, float) or not math.isfinite(filters[k]):
                raise ValueError("Finite timestamps required")
        if not 0 < filters["end"] - filters["start"] <= 3600:
            raise ValueError("Replay window must be at most one hour")
    else:
        raise ValueError("Unsupported explicit source/event")


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError("Symlink store refused")
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS monitors (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, scope TEXT NOT NULL,
                    source TEXT NOT NULL, event TEXT NOT NULL, filters TEXT NOT NULL,
                    outputs TEXT NOT NULL, expires REAL NOT NULL, created REAL NOT NULL,
                    ttl REAL NOT NULL, budget INTEGER NOT NULL, used INTEGER DEFAULT 0,
                    state TEXT DEFAULT 'active', cursor REAL DEFAULT 0,
                    next_poll REAL DEFAULT 0, errors INTEGER DEFAULT 0,
                    last_error TEXT DEFAULT '', registry_id TEXT DEFAULT '');
                CREATE TABLE IF NOT EXISTS events (
                    monitor TEXT, id TEXT, ts REAL, collected REAL, facts TEXT,
                    PRIMARY KEY(monitor,id));
                CREATE TABLE IF NOT EXISTS outbox (
                    monitor TEXT, event_id TEXT, state TEXT DEFAULT 'pending',
                    PRIMARY KEY(monitor,event_id));
                CREATE TABLE IF NOT EXISTS model_usage (ts REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS model_state (id INTEGER PRIMARY KEY, retry REAL);
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, monitor TEXT, ids TEXT, state TEXT);
                CREATE TABLE IF NOT EXISTS activations (
                    id TEXT PRIMARY KEY, owner TEXT, scope TEXT, state TEXT,
                    created REAL, expires REAL, monitor TEXT);
            """)
        self.path.chmod(0o600)

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=15000")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create(
        self,
        *,
        owner,
        session_scope,
        source,
        event,
        filters,
        expires,
        outputs,
        now=None,
        budget=3,
        ttl=300,
        request_id=None,
    ):
        now = time.time() if now is None else now
        if not isinstance(owner, str) or not re.fullmatch(
            r"[a-z][a-z0-9-]{0,39}", owner
        ):
            raise ValueError("Trusted owner identity required")
        if session_scope not in {"desktop", "private-owner"}:
            raise ValueError("Explicit supported session scope required")
        validate(source, event, filters)
        if (
            not isinstance(outputs, list)
            or not outputs
            or len(set(outputs)) != len(outputs)
            or set(outputs) - {"status", "session", "activate", "desktop-alert"}
        ):
            raise ValueError(
                "Explicit outputs: status, session, activate, desktop-alert; no calls/sends"
            )
        if "desktop-alert" in outputs and (
            "session" not in outputs or session_scope != "desktop"
        ):
            raise ValueError("Desktop alerts require explicit desktop session output")
        if "activate" in outputs and "session" not in outputs:
            raise ValueError("Activation requires explicit session output too")
        if (
            type(expires) not in (int, float)
            or not math.isfinite(expires)
            or not now < expires <= now + 7 * 86400
        ):
            raise ValueError("Explicit finite expiry within seven days required")
        if (
            type(budget) is not int
            or not 1 <= budget <= 50
            or type(ttl) not in (int, float)
            or not 1 <= ttl <= 3600
        ):
            raise ValueError("Bounded alert budget and freshness TTL required")
        if request_id is not None and (
            not isinstance(request_id, str) or not 1 <= len(request_id) <= 128
        ):
            raise ValueError("Bounded request ID required")
        definition = [
            owner,
            session_scope,
            source,
            event,
            filters,
            outputs,
            expires,
            budget,
            ttl,
        ]
        mid = "monitor-" + digest([owner, request_id or definition])[:32]
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM monitors WHERE id=?", (mid,)).fetchone()
            if old:
                if [
                    old["owner"],
                    old["scope"],
                    old["source"],
                    old["event"],
                    json.loads(old["filters"]),
                    json.loads(old["outputs"]),
                    old["expires"],
                    old["budget"],
                    old["ttl"],
                ] != definition:
                    raise ValueError("Request ID already used for another definition")
                return mid
            if (
                db.execute(
                    "SELECT COUNT(*) FROM monitors WHERE state='active'"
                ).fetchone()[0]
                >= 100
            ):
                raise ValueError("Active monitor capacity reached")
            db.execute(
                "INSERT INTO monitors(id,owner,scope,source,event,filters,outputs,expires,created,ttl,budget) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    mid,
                    owner,
                    session_scope,
                    source,
                    event,
                    canonical(filters),
                    canonical(outputs),
                    expires,
                    now,
                    ttl,
                    budget,
                ),
            )
        return mid

    def list(self, owner, *, authorized=True):
        if not authorized:
            raise PermissionError("Owner authorization required")
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM monitors WHERE owner=? ORDER BY created DESC LIMIT 100",
                    (owner,),
                )
            ]

    def status(self, mid, owner):
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM monitors WHERE id=? AND owner=?", (mid, owner)
            ).fetchone()
            if not row:
                raise ValueError("No monitor in owner scope")
            result = dict(row)
            result["counts"] = dict(
                db.execute(
                    "SELECT state, COUNT(*) FROM outbox WHERE monitor=? GROUP BY state",
                    (mid,),
                ).fetchall()
            )
            result["activations"] = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM activations WHERE monitor=? ORDER BY created DESC LIMIT 20",
                    (mid,),
                )
            ]
            return result

    def stop(self, mid, owner, now=None):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "UPDATE monitors SET state='cancelled' WHERE id=? AND owner=?",
                (mid, owner),
            ).rowcount:
                raise ValueError("No monitor in owner scope")
            db.execute(
                "UPDATE outbox SET state='cancelled' WHERE monitor=? AND state='pending'",
                (mid,),
            )
            db.execute(
                "UPDATE activations SET state='cancelled' WHERE monitor=? AND state='pending'",
                (mid,),
            )

    def expire(self, now):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE monitors SET state='expired' WHERE state='active' AND expires<=?",
                (now,),
            )
            db.execute(
                "UPDATE outbox SET state='expired' WHERE state='pending' AND (monitor IN (SELECT id FROM monitors WHERE state!='active') OR EXISTS(SELECT 1 FROM events e JOIN monitors m ON m.id=e.monitor WHERE e.monitor=outbox.monitor AND e.id=outbox.event_id AND e.collected+m.ttl<=?))",
                (now,),
            )
            db.execute(
                "UPDATE activations SET state='expired' WHERE state='pending' AND expires<=?",
                (now,),
            )

    def pending_count(self):
        with self.db() as db:
            return db.execute(
                "SELECT COUNT(*) FROM outbox WHERE state='pending'"
            ).fetchone()[0]

    def reserve_model(self, now, daily=50, minute=20):
        day = int(now // 86400) * 86400
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM model_usage WHERE ts<?", (day,))
            retry = db.execute("SELECT retry FROM model_state WHERE id=1").fetchone()
            if retry and retry[0] > now:
                return False
            if (
                db.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] >= daily
                or db.execute(
                    "SELECT COUNT(*) FROM model_usage WHERE ts>?", (now - 60,)
                ).fetchone()[0]
                >= minute
            ):
                return False
            db.execute("INSERT INTO model_usage VALUES(?)", (now,))
            return True

    def model_backoff(self, now):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO model_state VALUES(1,?)", (now + 60,))

    def prepare(self, now):
        """Persist pending delivery/activation demand even without a session host."""
        # Commit batch membership BEFORE side effects. A crash cannot change a
        # request key by coalescing later arrivals into a retried delivery.
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            monitors = db.execute(
                "SELECT * FROM monitors WHERE state='active' AND expires>? AND used<budget",
                (now,),
            ).fetchall()
            for m in monitors:
                if "session" not in json.loads(m["outputs"]):
                    continue
                if db.execute(
                    "SELECT 1 FROM batches WHERE monitor=? AND state='pending'",
                    (m["id"],),
                ).fetchone():
                    continue
                rows = db.execute(
                    "SELECT e.* FROM events e JOIN outbox o ON o.monitor=e.monitor AND o.event_id=e.id WHERE e.monitor=? AND o.state='pending' AND e.collected+?>? ORDER BY e.ts,e.id LIMIT 20",
                    (m["id"], m["ttl"], now),
                ).fetchall()
                if rows:
                    ids = [r["id"] for r in rows]
                    key = "monitor-delivery-" + digest([m["id"], ids])
                    db.execute(
                        "INSERT OR IGNORE INTO batches VALUES(?,?,?,?)",
                        (key, m["id"], canonical(ids), "pending"),
                    )
                    if "activate" in json.loads(m["outputs"]):
                        db.execute(
                            "INSERT OR IGNORE INTO activations VALUES(?,?,?,?,?,?,?)",
                            (
                                key,
                                m["owner"],
                                m["scope"],
                                "pending",
                                now,
                                min(
                                    m["expires"],
                                    min(r["collected"] for r in rows) + m["ttl"],
                                ),
                                m["id"],
                            ),
                        )

    def dispatch(
        self,
        now,
        session_provider,
        sink,
        *,
        quiet=None,
        start_session=None,
        live_effects=False,
        activation_guard=lambda owner, scope: False,
    ):
        """Sink/start must be bounded and durably idempotent by request key.

        Provider returns exactly one authenticated existing owner session or None.
        Guard/start are coordinator-owned; default denies any actual activation.
        SQLite serializes cancel/dispatch. Sink must not reenter this store.
        """
        from proactive.engine import decide, live_context, load_state
        from proactive.policy import in_quiet_hours, quiet_hours
        from proactive.signals import Signal

        self.expire(now)
        if quiet is None:
            quiet = in_quiet_hours(dt.datetime.fromtimestamp(now), quiet_hours())
        if quiet:
            return 0
        self.prepare(now)
        sent = 0
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            batches = db.execute(
                "SELECT * FROM batches WHERE state='pending' ORDER BY id"
            ).fetchall()
            for batch in batches:
                m = db.execute(
                    "SELECT * FROM monitors WHERE id=?", (batch["monitor"],)
                ).fetchone()
                if (
                    m["state"] != "active"
                    or m["expires"] <= now
                    or m["used"] >= m["budget"]
                ):
                    db.execute(
                        "UPDATE batches SET state='discarded' WHERE id=?",
                        (batch["id"],),
                    )
                    continue
                ids = json.loads(batch["ids"])
                rows = [
                    db.execute(
                        "SELECT e.* FROM events e JOIN outbox o ON o.monitor=e.monitor AND o.event_id=e.id WHERE e.monitor=? AND e.id=? AND o.state='pending' AND e.collected+?>?",
                        (m["id"], eid, m["ttl"], now),
                    ).fetchone()
                    for eid in ids
                ]
                if any(r is None for r in rows):
                    db.execute(
                        "UPDATE batches SET state='discarded' WHERE id=?",
                        (batch["id"],),
                    )
                    db.executemany(
                        "UPDATE outbox SET state='expired' WHERE monitor=? AND event_id=? AND state='pending'",
                        [(m["id"], eid) for eid in ids],
                    )
                    continue
                key = batch["id"]
                outputs = json.loads(m["outputs"])
                title = f"{m['source']} {m['event']}: {len(rows)} new public event(s)."
                detail = canonical(
                    [{"id": r["id"], "facts": json.loads(r["facts"])} for r in rows]
                )
                signal = Signal(
                    kind="event-monitor", key=key, title=title, detail=detail, ts=now
                )
                if (
                    decide(signal, live_context(load_state(), signal.kind, now)).action
                    != "say"
                ):
                    continue
                session = session_provider()
                if session is None and "activate" in outputs:
                    # Durable pending activation survives reconnect/restart; no phone offers.
                    db.execute(
                        "INSERT OR IGNORE INTO activations VALUES(?,?,?,?,?,?,?)",
                        (
                            key,
                            m["owner"],
                            m["scope"],
                            "pending",
                            now,
                            min(
                                m["expires"],
                                min(r["collected"] for r in rows) + m["ttl"],
                            ),
                            m["id"],
                        ),
                    )
                    if (
                        live_effects
                        and start_session
                        and activation_guard(m["owner"], m["scope"])
                    ):
                        session = start_session(key, m["owner"], m["scope"])
                        if session:
                            db.execute(
                                "UPDATE activations SET state='started' WHERE id=?",
                                (key,),
                            )
                if (
                    not session
                    or not session.authorized
                    or session.owner != m["owner"]
                    or session.scope != m["scope"]
                    or session.call_active
                    or session.watch_active
                ):
                    continue
                # Evidence only, no model-generated speech or model dispatch authority.
                current = session_provider()
                # A just-started session must also be published by the provider.
                if current != session:
                    continue
                try:
                    ok = sink(key, session, signal)
                except Exception:
                    ok = False
                if ok:
                    db.executemany(
                        "UPDATE outbox SET state='sent' WHERE monitor=? AND event_id=?",
                        [(m["id"], r["id"]) for r in rows],
                    )
                    db.execute("UPDATE monitors SET used=used+1 WHERE id=?", (m["id"],))
                    db.execute("UPDATE batches SET state='sent' WHERE id=?", (key,))
                    db.execute(
                        "UPDATE activations SET state='fulfilled' WHERE id=?", (key,)
                    )
                    sent += 1
        return sent


class Worker:
    def __init__(self, store, adapters, *, triage=None, registry=None):
        self.store, self.adapters, self.triage, self.registry = (
            store,
            adapters,
            triage,
            registry,
        )

    def sync_registry(self, now):
        if self.registry is None:
            return
        with self.store.db() as db:
            rows = db.execute("SELECT * FROM monitors").fetchall()
        for m in rows:
            row = self.registry.by_native("event-monitor", m["id"])
            if row is None:
                row = self.registry.register(
                    "event-monitor",
                    m["id"],
                    "monitor_observation",
                    f"{m['source']} {m['event']}",
                    {"monitor_id": m["id"]},
                )
            if row["lifecycle"] == "cancellation_requested":
                self.store.stop(m["id"], m["owner"], now)
                m = self.store.status(m["id"], m["owner"])
            if m["state"] != "active" and row["lifecycle"] != "settled":
                self.registry.settle(
                    row["id"],
                    "event-monitor",
                    "monitoring_stopped",
                    row["payload"],
                    row["revision"],
                )
            with self.store.db() as db:
                db.execute(
                    "UPDATE monitors SET registry_id=? WHERE id=?", (row["id"], m["id"])
                )

    def collect(self, now=None):
        now = time.time() if now is None else now
        self.store.expire(now)
        self.sync_registry(now)
        with self.store.db() as db:
            rows = db.execute(
                "SELECT * FROM monitors WHERE state='active' AND next_poll<=? ORDER BY next_poll,id LIMIT 10",
                (now,),
            ).fetchall()
        for raw in rows:
            m = dict(raw)
            m["filters"] = json.loads(m["filters"])
            try:
                events = self.adapters[m["source"]](m, m["cursor"], now)
                if len(events) > 200:
                    raise ValueError("Batch exceeded 200 events")
                fresh = []
                for e in events:
                    if (
                        not isinstance(e, Event)
                        or not isinstance(e.key, str)
                        or not 0 < len(e.key) <= 256
                        or type(e.ts) not in (int, float)
                        or not math.isfinite(e.ts)
                    ):
                        raise ValueError("Invalid evidence")
                    if (
                        not isinstance(e.facts, dict)
                        or len(canonical(e.facts).encode()) > 4096
                    ):
                        raise ValueError("Evidence bounds")
                    replay = m["source"] == "openf1"
                    if replay:
                        if not m["filters"]["start"] <= e.ts <= m["filters"]["end"]:
                            continue
                    elif not now - m["ttl"] <= e.ts <= now + 30:
                        continue
                    fresh.append(e)
                with self.store.db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    current = db.execute(
                        "SELECT * FROM monitors WHERE id=?", (m["id"],)
                    ).fetchone()
                    if current["state"] != "active" or current["expires"] <= now:
                        continue
                    new = []
                    for e in sorted(fresh, key=lambda e: (e.ts, e.key)):
                        if db.execute(
                            "INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)",
                            (m["id"], e.key, e.ts, now, canonical(e.facts)),
                        ).rowcount:
                            new.append(e)
                    # Deterministic enqueue happens atomically with cursor. Optional triage
                    # may only suppress pending evidence after commit, never invent events.
                    db.executemany(
                        "INSERT INTO outbox(monitor,event_id) VALUES(?,?)",
                        [(m["id"], e.key) for e in new],
                    )
                    db.execute(
                        "UPDATE monitors SET cursor=MAX(cursor,?),next_poll=?,errors=0,last_error='' WHERE id=?",
                        (max([m["cursor"]] + [e.ts for e in fresh]), now + 60, m["id"]),
                    )
                if new and self.triage:
                    try:
                        decision = self.triage(new)
                        if isinstance(decision, str):
                            if len(decision) > 8192:
                                raise ValueError("Model response too large")
                            decision = json.loads(decision)
                        if (
                            not isinstance(decision, dict)
                            or set(decision) != {"ids"}
                            or not isinstance(decision["ids"], list)
                            or any(not isinstance(k, str) for k in decision["ids"])
                            or len(set(decision["ids"])) != len(decision["ids"])
                            or set(decision["ids"]) - {e.key for e in new}
                        ):
                            raise ValueError("Evidence-bound schema required")
                        with self.store.db() as db:
                            db.executemany(
                                "UPDATE outbox SET state='suppressed' WHERE monitor=? AND event_id=? AND state='pending'",
                                [
                                    (m["id"], e.key)
                                    for e in new
                                    if e.key not in decision["ids"]
                                ],
                            )
                    except Exception:
                        pass  # unavailable/quota/invalid response -> deterministic collection
            except Exception as exc:
                with self.store.db() as db:
                    db.execute(
                        "UPDATE monitors SET errors=errors+1,next_poll=?,last_error=? WHERE id=?",
                        (
                            now + min(3600, 60 * 2 ** min(m["errors"], 6)),
                            type(exc).__name__,
                            m["id"],
                        ),
                    )

        self.store.prepare(now)
