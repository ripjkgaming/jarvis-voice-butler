"""Away worker: while Sir is asleep, Jarvis handles his mail, calendar and WhatsApp.

Runs as a loop (`python -m src.away_worker run`, every 5 minutes) and only acts
while ~/.jarvis/sleep_state.json says {"state": "asleep"}. Every action, and
every item it refuses to decide, is written to away_log (see away_log.py).

Policy: full auto-send, with the log entry written every time. When the worker
is genuinely unsure (money, legal or health mail, a missing reply address, a
model failure, an unclear WhatsApp handoff) it logs a deferred_decision and
sends nothing.

Dry run is the default. Set JARVIS_AWAY_LIVE=1 to let it send; `--dry-run`
forces dry run even then. In dry run it still reads mail and drafts replies,
but logs them as "DRY RUN" entries instead of sending.

Reuse, not duplication:
- Gmail read/send and MIME: system.inbox (the same helpers the mail watcher uses).
- Reply drafting: draft_engine.draft_reply, with mail_watch_job's thread context.
- Calendar: event_extractor.suggest, the same auto-add path as the mail watcher.
- WhatsApp: wa_autoreply.handle_chat on mimic-list chats only, so the daemon's
  dedupe state and wa_mimic routing apply.
WhatsApp dates are already handled by the whatsapp_calendar worker, so the
calendar step covers mail only.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import away_log

POLL_S = 300.0
LIVE_ENV = "JARVIS_AWAY_LIVE"
MAX_MAIL_PER_CYCLE = 10
SEEN_KEEP = 2000
#: Mail whose text matches these is never answered automatically.
DEFER_RE = re.compile(
    r"\b(payment|pay(?:ing)?|invoice|refund|bank|transfer|salary|tax|loan|"
    r"contract|legal|lawyer|solicitor|court|police|subpoena|password|"
    r"verification code|one[- ]time code|diagnos\w*|hospital|medical|"
    r"funeral|passed away|urgent|terminate|resign\w*)\b",
    re.IGNORECASE,
)


def home() -> Path:
    return Path(os.environ.get("JARVIS_HOME") or Path.home() / ".jarvis")


def sleep_state_path() -> Path:
    return home() / "sleep_state.json"


def state_path() -> Path:
    return home() / "away_worker.state.json"


def live_enabled() -> bool:
    return os.environ.get(LIVE_ENV, "").strip() == "1"


def is_asleep() -> bool:
    """True while sleep_state.json says asleep. Missing or corrupt means awake."""
    try:
        data = json.loads(sleep_state_path().read_text())
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("state") == "asleep"


def _load_state() -> dict:
    try:
        data = json.loads(state_path().read_text())
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        data = {}
    data.setdefault("active", False)
    data.setdefault("seen", {})
    return data


def _save_state(state: dict) -> None:
    seen = state.get("seen") or {}
    if len(seen) > SEEN_KEEP:
        keep = sorted(seen.items(), key=lambda kv: float(kv[1]))[-SEEN_KEEP:]
        state["seen"] = dict(keep)
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, path)


def _defer(summary: str, detail: object = None) -> None:
    """Record something Sir must decide. Falls back to stderr if the log fails."""
    try:
        away_log.log_event("deferred_decision", summary, detail)
    except OSError as exc:
        print(f"away_log write failed ({exc}): {summary}", file=sys.stderr)


class Handler:
    """Pluggable backends for one away cycle. The defaults do nothing.

    A handler logs its own outcomes with away_log. The worker only logs a
    deferred_decision when a handler method raises.
    """

    def new_emails(self, since: float) -> list[dict]:
        """Unread human mail that arrived at or after `since`, parsed."""
        return []

    def process_email(self, msg: dict) -> None:
        """Reply to one mail if it can be handled safely, or defer it."""

    def process_calendar(self, msg: dict) -> None:
        """Add dates asked for in one mail to Google Calendar, or defer it."""

    def new_whatsapp(self) -> list[dict]:
        """Unread WhatsApp chats that are on the mimic list and ready."""
        return []

    def process_whatsapp(self, chat: dict) -> None:
        """Handle one WhatsApp chat through the existing mimic path, or defer it."""


class SystemHandler(Handler):
    """Real wiring into the existing Jarvis modules. Live only with JARVIS_AWAY_LIVE=1.

    Not exercised against live Gmail, Calendar or WhatsApp by the unit tests.
    Every test uses a fake Handler, so nothing real is ever sent.
    """

    def __init__(self, dry_run: bool = True) -> None:
        self.dry_run = bool(dry_run)
        self.owner = os.environ.get("JARVIS_OWNER_EMAIL", "").strip().lower()
        if not self.dry_run and not self.owner:
            raise RuntimeError("JARVIS_OWNER_EMAIL must be set for live away mode")

    # Gmail -----------------------------------------------------------------
    def new_emails(self, since: float) -> list[dict]:
        from system import inbox

        token = inbox._gmail_access_token()
        listing = inbox._gmail_api(
            "/messages",
            token,
            {"q": f"is:unread in:inbox after:{int(since)}", "maxResults": 20},
        )
        out: list[dict] = []
        for ref in (listing.get("messages") or [])[:MAX_MAIL_PER_CYCLE]:
            full = inbox._gmail_api(f"/messages/{ref['id']}", token, {"format": "full"})
            parsed = inbox.parse_gmail_message(full)
            verdict = inbox.classify_email(
                parsed["sender"], parsed["subject"], parsed["snippet"], full
            )
            if not verdict["human"] or verdict["level"] == "skip":
                continue
            to = inbox.automatic_reply_recipient(full)
            if self.owner and to == self.owner:
                continue
            parsed.update(
                source="mail",
                to=to,
                message_id=inbox._header(full, "Message-ID"),
                references=inbox._header(full, "References"),
                verdict=verdict,
            )
            out.append(parsed)
        return out

    def _owner_already_replied(self, thread_id: str, token: str) -> bool:
        if not thread_id or not self.owner:
            return False
        from system import inbox

        thread = inbox._gmail_api(
            f"/threads/{thread_id}",
            token,
            {"format": "metadata", "metadataHeaders": "From"},
        )
        for item in thread.get("messages") or []:
            sender = inbox._header(item, "From").lower()
            if self.owner in sender:
                return True
        return False

    def process_email(self, msg: dict) -> None:
        from system import inbox

        who = msg.get("sender") or "unknown"
        subject = msg.get("subject") or "(no subject)"
        if not msg.get("to"):
            _defer(
                f"Mail from {who} ({subject}) needs a reply, but I could not verify "
                "its reply address. I did not answer it.",
                {"id": msg.get("id")},
            )
            return
        text = f"{subject} {msg.get('snippet', '')} {msg.get('body', '')}"
        if DEFER_RE.search(text):
            _defer(
                f"Mail from {who} about {subject} touches money, legal or health "
                "matters. I left it for you.",
                {"id": msg.get("id")},
            )
            return
        import draft_engine
        import mail_watch_job

        context = mail_watch_job._draft_context(
            msg["id"], msg.get("thread_id", ""), msg["to"]
        )
        draft, warning = draft_engine.draft_reply(msg, context=context)
        if warning:
            _defer(
                f"I could not draft a reply to {who} about {subject}: {warning}",
                {"id": msg.get("id")},
            )
            return
        if draft is None:
            return  # the mail expects no answer
        token = inbox._gmail_access_token()
        if self._owner_already_replied(msg.get("thread_id", ""), token):
            return  # Sir is already handling this thread himself
        reply_subject = (
            subject if subject.lower().startswith("re:") else f"Re: {subject}"
        )
        body = draft["body"]
        if self.dry_run:
            away_log.log_event(
                "email_drafted",
                f"DRY RUN: would reply to {who} about {subject}",
                {
                    "to": msg["to"],
                    "subject": reply_subject,
                    "body": body,
                    "dry_run": True,
                },
            )
            return
        inbox._gmail_send_api(
            token,
            inbox.build_mime_b64(
                msg["to"],
                reply_subject,
                body,
                msg.get("message_id", ""),
                references=msg.get("references", ""),
            ),
            msg.get("thread_id") or None,
        )
        away_log.log_event(
            "email_replied",
            f"Replied to {who} about {subject}",
            {"to": msg["to"], "subject": reply_subject, "body": body},
        )

    # Calendar --------------------------------------------------------------
    def process_calendar(self, msg: dict) -> None:
        import event_extractor as ex

        if not ex.enabled() or msg.get("source") != "mail":
            return
        if self.dry_run:
            event, _warning = ex.extract(msg, time.time())
            if event:
                away_log.log_event(
                    "calendar_scheduled",
                    f"DRY RUN: would add {event.get('title', 'an event')}, "
                    f"{ex.spoken_when(event)}",
                    {"dry_run": True, "event": event},
                )
            return
        outcome = ex.suggest(msg, now=time.time())
        record = ex.get(str(msg.get("id") or "")) or {}
        event = record.get("event") or {}
        when = ex.spoken_when(event) if event.get("date") else "the date in the mail"
        title = event.get("title") or msg.get("subject") or "event"
        if outcome == "auto-added":
            away_log.log_event(
                "calendar_scheduled",
                f"Added to Google Calendar: {title}, {when}",
                {"id": msg.get("id"), "event": event},
            )
        elif (
            outcome == "suggested"
            or outcome.startswith("failed")
            or outcome == "retry-queued"
        ):
            _defer(
                f"Calendar event from {msg.get('sender') or 'mail'} needs your OK: "
                f"{title}, {when} ({outcome}).",
                {"id": msg.get("id"), "event": event},
            )

    # WhatsApp --------------------------------------------------------------
    def new_whatsapp(self) -> list[dict]:
        import wa_autoreply as war
        import wa_headless
        import wa_mimic
        from system import whatsapp as wa

        if not wa_headless.ensure():
            return []
        try:
            chats = asyncio.run(wa.list_chats(30))
        except Exception:
            return []
        state = war.load_state()
        since = state.setdefault("unread_since", {})
        now = time.time()
        ready: list[dict] = []
        for chat in chats:
            name = str(chat.get("name", ""))
            if int(chat.get("unread") or 0) <= 0 or wa_mimic.lookup(name) is None:
                continue
            first = float(since.setdefault(name, now))
            if now - first >= war.unread_wait_s():
                ready.append(chat)
        war.save_state(state)
        return ready

    def process_whatsapp(self, chat: dict) -> None:
        import wa_autoreply as war
        from system import whatsapp as wa

        name = str(chat.get("name", ""))
        state = war.load_state()
        outcome = asyncio.run(
            war.handle_chat(chat, wa, state, time.time(), not self.dry_run)
        )
        sent = state.pop("_sent_this_run", [])
        state.get("unread_since", {}).pop(name, None)
        war.save_state(state)
        if outcome in ("sent", "sent-rude") and sent:
            entry = sent[-1]
            away_log.log_event(
                "whatsapp_replied",
                f"Replied to {name} on WhatsApp",
                {"chat": name, "reply": entry.get("reply", "")},
            )
            if entry.get("pass_along") or entry.get("important"):
                _defer(
                    f"WhatsApp from {name}: {entry.get('for_owner') or 'please check it'}",
                    {"chat": name},
                )
        elif outcome == "dry-run":
            away_log.log_event(
                "other",
                f"DRY RUN: would reply on WhatsApp to {name}",
                {"dry_run": True},
            )
        elif outcome == "handoff":
            _defer(
                f"WhatsApp conversation with {name} needs you; I did not reply.",
                {"chat": name},
            )
        elif outcome.startswith(("skip-model", "send-failed", "error")):
            _defer(f"Could not reply on WhatsApp to {name}: {outcome}", {"chat": name})


def _step(label: str, fn, *args) -> None:
    """Run one handler call. A failure becomes a deferred decision. Never raises."""
    try:
        fn(*args)
    except Exception as exc:
        _defer(f"Could not finish {label}: {type(exc).__name__}: {exc}"[:300])


def cycle(handler: Handler, *, now: float | None = None) -> str:
    """One pass. Returns "awake", "asleep" or "error". Never raises."""
    now = time.time() if now is None else float(now)
    state = _load_state()
    try:
        asleep = is_asleep()
        if asleep and not state["active"]:
            away_log.start_away(now=now)
            state.update(active=True, asleep_since=now)
        elif not asleep and state["active"]:
            away_log.end_away(now=now)
            state["active"] = False
    except OSError as exc:
        print(f"away_log write failed: {exc}", file=sys.stderr)
        return "error"
    if not asleep:
        _save_state(state)
        return "awake"
    since = float(state.get("asleep_since") or now)
    seen = state["seen"]
    try:
        emails = handler.new_emails(since)
    except Exception as exc:
        _defer(f"Could not read Gmail: {type(exc).__name__}: {exc}"[:300])
        emails = []
    for msg in emails[:MAX_MAIL_PER_CYCLE]:
        key = f"mail:{msg.get('id')}"
        if key in seen:
            continue
        _step("mail reply", handler.process_email, msg)
        _step("mail calendar check", handler.process_calendar, msg)
        seen[key] = now
    try:
        chats = handler.new_whatsapp()
    except Exception as exc:
        _defer(f"Could not read WhatsApp: {type(exc).__name__}: {exc}"[:300])
        chats = []
    for chat in chats:
        _step("WhatsApp reply", handler.process_whatsapp, chat)
    _save_state(state)
    return "asleep"


def _lock_or_none():
    path = home() / "away_worker.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="away_worker")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser(
        "run", help="loop while asleep (dry run unless JARVIS_AWAY_LIVE=1)"
    )
    run.add_argument("--once", action="store_true", help="one cycle, then exit")
    run.add_argument("--interval", type=float, default=POLL_S)
    run.add_argument("--dry-run", action="store_true", help="never send, even if live")
    sub.add_parser("report", help="print the spoken summary of the last away session")
    args = parser.parse_args(argv)

    if args.cmd == "report":
        print(away_log.spoken_summary(away_log.away_report()))
        return 0

    dry = args.dry_run or not live_enabled()
    lock = _lock_or_none()
    if lock is None:
        print("away_worker is already running", file=sys.stderr)
        return 1
    try:
        handler = SystemHandler(dry_run=dry)
    except RuntimeError as exc:
        print(f"STUCK: {exc}", file=sys.stderr)
        return 2
    print(
        f"away_worker: {'DRY RUN' if dry else 'LIVE'}; poll {args.interval}s",
        flush=True,
    )
    try:
        while True:
            with contextlib.suppress(Exception):
                cycle(handler)
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
