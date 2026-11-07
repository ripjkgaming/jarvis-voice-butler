"""Laptop-owned bounded phone invitations. No urgency bypass, deferred rings or credentials.
Existing proactive sources offer signals; only a recently listening phone can receive one.
"""

import json
import math
import re
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Asia/Singapore")
TTL = 45


def quiet(now):
    local = datetime.fromtimestamp(now, ZONE)
    return local.weekday() < 5 and 480 <= local.hour * 60 + local.minute < 930


class CallQueue:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY, enabled INTEGER, calendar INTEGER, updates INTEGER, heartbeat REAL)"
            )
            c.execute("INSERT OR IGNORE INTO control VALUES (1,0,1,1,0)")
            c.execute(
                "CREATE TABLE IF NOT EXISTS monitor (id INTEGER PRIMARY KEY, data TEXT, at REAL)"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, key TEXT UNIQUE, kind TEXT, title TEXT, issued REAL, expires REAL, status TEXT, day TEXT)"
            )

            c.execute(
                "CREATE TABLE IF NOT EXISTS scheduled_call_receipts "
                "(key TEXT PRIMARY KEY, status TEXT, reason TEXT, call_id TEXT)"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS checkin_schedule (id INTEGER PRIMARY KEY, enabled INTEGER)"
            )
            c.execute("INSERT OR IGNORE INTO checkin_schedule VALUES (1,1)")

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=5)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    @staticmethod
    def expire(c, now):
        c.execute(
            "UPDATE calls SET status='expired' WHERE status='ringing' AND (expires<=? OR ?)",
            (now, quiet(now)),
        )

    def configure(self, enabled, calendar, updates, now, listener=True):
        with self.db() as c:
            old = c.execute("SELECT heartbeat FROM control WHERE id=1").fetchone()[0]
            heartbeat = (now if listener else 0) if listener is not None else old
            c.execute(
                "UPDATE control SET enabled=?,calendar=?,updates=?,heartbeat=? WHERE id=1",
                (enabled, calendar, updates, heartbeat if enabled else 0),
            )
            c.execute(
                "UPDATE calls SET status='cancelled' WHERE status='ringing' AND (?=0 OR (kind='calendar' AND ?=0) OR (kind='update' AND ?=0) OR ?=0)",
                (enabled, calendar, updates, listener is not False),
            )
        return self.pending(now)

    def monitor(self, enabled, calendar, updates, diagnostic, now):
        codes = {
            "off",
            "starting",
            "monitoring",
            "quiet_hours",
            "notifications_disabled",
            "channel_disabled",
            "notification_posted",
            "ringing",
            "posted_silent",
            "ringtone_failed",
            "notification_failed",
            "clock_skew",
            "future_invitation",
            "expired",
            "invalid_lifetime",
            "call_active",
            "handled",
            "network_error",
            "api_unavailable",
            "unauthorized",
            "monitor_start_failed",
            "guest_mode",
            "offline_mode",
            "not_configured",
        }
        d = {}
        if isinstance(diagnostic, dict):
            for key in ("notifications", "full_screen", "dnd"):
                if type(diagnostic.get(key)) is bool:
                    d[key] = diagnostic[key]
            for key, lo, hi in (
                ("client_time_ms", 0, 4_000_000_000_000),
                ("channel_importance", 0, 5),
                ("ringer_mode", 0, 2),
            ):
                value = diagnostic.get(key)
                if type(value) is int and lo <= value <= hi:
                    d[key] = value
            if diagnostic.get("status") in codes:
                d["status"] = diagnostic["status"]
        client = d.get("client_time_ms", 0)
        ready = (
            d.get("notifications") is True
            and d.get("channel_importance", 0) > 0
            and abs(now * 1000 - client) <= 15_000
            and d.get("status") != "call_active"
        )
        d["ready"] = ready
        d["clock_skew_ms"] = round(now * 1000 - client) if client else None
        with self.db() as c:
            c.execute(
                "INSERT OR REPLACE INTO monitor VALUES (1,?,?)", (json.dumps(d), now)
            )
        self.configure(enabled, calendar, updates, now, listener=ready)
        self.poll_schedule(now)
        return self.pending(now)

    def offer(self, kind, key, title, now, deadline=None):
        if (
            kind not in ("calendar", "update", "checkin")
            or not key
            or not title.strip()
        ):
            return False
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            self.expire(c, now)
            if c.execute("SELECT 1 FROM calls WHERE key=?", (key,)).fetchone():
                return False
            p = c.execute("SELECT * FROM control").fetchone()
            day = datetime.fromtimestamp(now, ZONE).date().isoformat()
            busy = c.execute(
                "SELECT 1 FROM calls WHERE status='ringing'",
            ).fetchone()
            count = c.execute(
                "SELECT count(*) FROM calls WHERE day=? AND status!='suppressed' AND kind!='scheduled'",
                (day,),
            ).fetchone()[0]
            allowed = (
                p["enabled"]
                and p["heartbeat"] <= now
                and now - p["heartbeat"] < 60
                and (
                    p["calendar"]
                    if kind == "calendar"
                    else p["updates"]
                    if kind == "update"
                    else c.execute(
                        "SELECT enabled FROM checkin_schedule WHERE id=1"
                    ).fetchone()[0]
                )
                and not quiet(now)
                and not busy
                and count < 3
            )
            c.execute(
                "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    key[:200],
                    kind,
                    title[:300],
                    now,
                    min(now + TTL, deadline) if deadline is not None else now + TTL,
                    "ringing" if allowed else "suppressed",
                    day,
                ),
            )
            # Bounded dedupe ledger; keep at least a week across restarts.
            c.execute("DELETE FROM calls WHERE issued<?", (now - 7 * 86400,))
            return bool(allowed)

    def offer_scheduled(self, key, title, now, deadline):
        """One requested occurrence, with a stable receipt and no late replay.

        Explicit requests use the incoming-call opt-in and live listener gates;
        proactive category switches and budgets do not consume this request.
        A blocked occurrence remains blocked after reconnect or restart.
        """
        if (
            not isinstance(key, str)
            or not 1 <= len(key) <= 200
            or not isinstance(title, str)
            or not title.strip()
            or type(now) not in (int, float)
            or not math.isfinite(now)
            or type(deadline) not in (int, float)
            or not math.isfinite(deadline)
        ):
            raise ValueError("Invalid scheduled invitation")
        import phone_session

        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            previous = c.execute(
                "SELECT status,reason,call_id FROM scheduled_call_receipts WHERE key=?",
                (key,),
            ).fetchone()
            if previous:
                return dict(previous)
            self.expire(c, now)
            control = c.execute("SELECT * FROM control WHERE id=1").fetchone()
            reason = (
                "expired"
                if deadline <= now
                else "guest_mode"
                if phone_session.guest_required()
                else "disabled"
                if not control["enabled"]
                else "offline"
                if not 0 <= now - control["heartbeat"] < 60
                else "quiet_hours"
                if quiet(now)
                else "busy"
                if c.execute("SELECT 1 FROM calls WHERE status='ringing'").fetchone()
                else ""
            )
            call_id = None
            if not reason:
                call_id = uuid.uuid4().hex
                day = datetime.fromtimestamp(now, ZONE).date().isoformat()
                c.execute(
                    "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?)",
                    (
                        call_id,
                        key,
                        "scheduled",
                        title.strip()[:300],
                        now,
                        min(now + TTL, deadline),
                        "ringing",
                        day,
                    ),
                )
            status = "blocked" if reason else "invited"
            c.execute(
                "INSERT INTO scheduled_call_receipts VALUES (?,?,?,?)",
                (key, status, reason, call_id),
            )
            return {"status": status, "reason": reason, "call_id": call_id}

    def pending(self, now):
        with self.db() as c:
            self.expire(c, now)
            p = c.execute("SELECT * FROM control").fetchone()
            record = c.execute("SELECT data,at FROM monitor WHERE id=1").fetchone()
            row = (
                c.execute(
                    "SELECT * FROM calls WHERE status='ringing' ORDER BY issued DESC LIMIT 1"
                ).fetchone()
                if p["enabled"]
                else None
            )
            invitation = (
                {k: row[k] for k in ("id", "kind", "title", "issued", "expires")}
                if row
                else None
            )
            return {
                "ok": True,
                "server_time": now,
                "quiet": quiet(now),
                "timezone": "Asia/Singapore",
                "enabled": bool(p["enabled"]),
                "calendar": bool(p["calendar"]),
                "updates": bool(p["updates"]),
                "invitation": invitation,
                "listener_age_s": max(0, now - p["heartbeat"])
                if p["heartbeat"]
                else None,
                "monitor": {**json.loads(record["data"]), "at": record["at"]}
                if record
                else None,
                "schedule": self.schedule(now),
            }

    def schedule(self, now):
        local = datetime.fromtimestamp(now, ZONE)
        first = datetime(2026, 10, 7, 19, 5, tzinfo=ZONE)
        days = (2 - local.weekday()) % 7
        upcoming = (local + timedelta(days=days)).replace(
            hour=19, minute=5, second=0, microsecond=0
        )
        if upcoming.timestamp() <= now:
            upcoming += timedelta(days=7)
        upcoming = max(first, upcoming)
        with self.db() as c:
            enabled = bool(
                c.execute("SELECT enabled FROM checkin_schedule WHERE id=1").fetchone()[
                    0
                ]
            )
        return {
            "enabled": enabled,
            "weekday": "Wednesday",
            "time": "19:05",
            "timezone": "Asia/Singapore",
            "start_date": "2026-10-07",
            "next_at": upcoming.isoformat(),
        }

    def set_schedule(self, enabled):
        with self.db() as c:
            c.execute("UPDATE checkin_schedule SET enabled=? WHERE id=1", (enabled,))
            if not enabled:
                c.execute(
                    "UPDATE calls SET status='cancelled' WHERE kind='checkin' AND status='ringing'"
                )

    def poll_schedule(self, now):
        local = datetime.fromtimestamp(now, ZONE)
        if local.date().isoformat() < "2026-10-07" or local.weekday() != 2:
            return False
        due = local.replace(hour=19, minute=5, second=0, microsecond=0).timestamp()
        if not 0 <= now - due < TTL:
            return False
        if not self.schedule(now)["enabled"]:
            return False
        return self.offer(
            "checkin",
            "weekly-checkin:" + local.date().isoformat(),
            "It's time for your Wednesday check-in. How are you feeling, and what would you like to focus on this week?",
            now,
            deadline=due + TTL,
        )

    def accepted(self, call_id, now, consume=False):
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT * FROM calls WHERE id=? AND status='accept' AND issued<=? AND expires>?",
                (call_id, now, now),
            ).fetchone()
            control = c.execute("SELECT * FROM control").fetchone()
            if (
                not row
                or quiet(now)
                or not control["enabled"]
                or not (
                    control["calendar"]
                    if row["kind"] == "calendar"
                    else control["updates"]
                    if row["kind"] == "update"
                    else True
                    if row["kind"] == "scheduled"
                    else c.execute(
                        "SELECT enabled FROM checkin_schedule WHERE id=1"
                    ).fetchone()[0]
                )
            ):
                return None
            if consume:
                c.execute("UPDATE calls SET status='announced' WHERE id=?", (call_id,))
            return row["title"]

    def claim(self, call_id, action, now):
        if action not in ("accept", "decline", "end"):
            return False
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            self.expire(c, now)
            p = c.execute("SELECT * FROM control").fetchone()
            if not p["enabled"] or quiet(now):
                return False
            r = c.execute(
                "UPDATE calls SET status=? WHERE id=? AND status='ringing' AND issued<=? AND expires>?",
                (action, call_id, now, now),
            )
            return r.rowcount == 1


def queue():
    return CallQueue(
        Path(os.environ.get("JARVIS_HOME") or Path.home() / ".jarvis")
        / "phone_calls.sqlite3"
    )


def offer_signal(signal, now):
    # Reuse existing integrations only: calendar and meaningful finished-job updates.
    if signal.kind not in ("calendar", "job") or signal.urgency == "low":
        return False
    key = (
        "calendar:" + str(signal.action["event_id"])
        if signal.kind == "calendar" and signal.action.get("event_id")
        else signal.key
    )
    if signal.ts and not 0 <= now - signal.ts <= TTL:
        return False
    return queue().offer(
        "calendar" if signal.kind == "calendar" else "update", key, signal.title, now
    )


def endpoint(body, mint=None):
    now = time.time()
    q = queue()
    if body is None:
        return 200, q.pending(now)
    if not isinstance(body, dict) or body.get("api_version") != 1:
        return 400, {"ok": False, "error": "Invalid calls request"}
    if body.get("action") == "schedule":
        if type(body.get("enabled")) is not bool:
            return 400, {"ok": False, "error": "Boolean enabled required"}
        q.set_schedule(body["enabled"])
        return 200, q.pending(now)
    if body.get("action") in ("configure", "poll"):
        if any(
            type(body.get(k)) is not bool for k in ("enabled", "calendar", "updates")
        ):
            return 400, {"ok": False, "error": "Boolean controls required"}
        if body["action"] == "poll":
            return 200, q.monitor(
                body["enabled"],
                body["calendar"],
                body["updates"],
                body.get("diagnostics", {}),
                now,
            )
        return 200, q.configure(
            body["enabled"],
            body["calendar"],
            body["updates"],
            now,
            listener=None if body.get("settings_only") is True else True,
        )
    if not q.claim(str(body.get("id", "")), body.get("action"), now):
        return 409, {
            "ok": False,
            "error": "Call expired, unavailable or already handled",
        }
    if body["action"] != "accept":
        return 200, {"ok": True}
    # Mint only after explicit accept and a fresh policy recheck. Failure never re-rings.
    if quiet(time.time()):
        return 409, {"ok": False, "error": "School quiet hours"}
    try:
        return 200, dict(ok=True, **mint(str(body["id"])))
    except Exception:  # noqa: BLE001 - fail closed without exposing credential errors
        return 503, {
            "ok": False,
            "error": "Voice service unavailable; start a call when ready",
        }


def consume_announcement(call_id, identity, now=None):
    """Signed identity plus authenticated eligible ledger, one opener, no caller title."""
    import phone_session

    # Private incoming tokens carry both immutable phone_owner and call attrs.
    # The isolated job has already verified room dispatch and pinned identity.
    if not isinstance(call_id, str) or not re.fullmatch(r"[0-9a-f]{32}", call_id):
        return ""
    if phone_session.private():
        if (
            not phone_session.identity()
            or identity != phone_session.identity()
            or call_id != phone_session.invitation_id()
        ):
            return ""
    elif identity != "jarvis-phone-" + call_id:
        return ""
    return (
        queue().accepted(call_id, time.time() if now is None else now, consume=True)
        or ""
    )


def mint_phone_token(call_id):
    if not queue().accepted(call_id, time.time()):
        raise RuntimeError("Invitation unavailable")
    from mint_token import mint_token_payload

    return mint_token_payload("", True, phone_call_id=call_id, private_phone=True)


def poll_schedule(now=None):
    # Called only by the existing proactive scheduler thread, never a phone API
    # poll. The bounded Calendar read cannot delay voice/session requests.
    now = time.time() if now is None else now
    calls = queue()
    weekly = calls.poll_schedule(now)
    try:
        import bedtime_calls

        bedtime_calls.poll_schedule(calls, now)
    except Exception:
        pass  # An isolated bedtime source failure cannot break weekly calls.
    return weekly
