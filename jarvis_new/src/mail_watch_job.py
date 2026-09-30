"""Headless mail watcher: poll unread, ping WhatsApp, auto-reply high-priority.

Runs from cron every 5 minutes (install below). For each unseen unread
message it triages via system.inbox.classify_email:

- skip (own mail, noise): logged only.
- normal: logged; human senders get a relaxed "Will pass it on" reply (once
  per thread); surfaces through gmail tools + morning briefing.
- high: WhatsApp ping to JARVIS_WA_NOTIFY_CHAT + desktop notification.
  Human senders ALSO get an automatic acknowledgment reply — this is the
  user-preauthorized autoconfirm path (kill with JARVIS_AUTOREPLY=0).
  Bulk/noreply senders are pinged about, never replied to.

State (seen ids) lives in ~/.jarvis/mail_watch.state.json so restarts
never double-ping or double-reply. Every outcome is reason-coded to
actions.log. Failures are swallowed per-message; the job always exits 0.

Install:
    (crontab -l 2>/dev/null; echo "*/5 * * * * cd /mnt/data/jarvis-voice-butler/jarvis_new && JARVIS_LOCAL=1 JARVIS_OWNER_EMAIL=ripjkgaming@gmail.com JARVIS_WA_NOTIFY_CHAT='Message yourself' .venv/bin/python src/mail_watch_job.py") | crontab -
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

MAX_PER_RUN = 20
MAX_DRAFTS_PER_RUN = 3
#: One automatic acknowledgement per thread, and per sender per day, at most.
ACK_SENDER_COOLDOWN_S = 24 * 3600
ACK_KEEP = 300
MAX_EVENT_CHECKS_PER_RUN = 3
_run = {"drafts": 0, "events": 0}


async def _pass_on_reply(
    full: dict, parsed: dict, token: str, acks: dict, *, scam: bool = False
) -> str:
    """Send the relaxed "Will pass it on" reply (or the scam reply) to a human sender.

    Shares the urgent path's ack memory: one per thread, one per sender per day.
    Returns a reason-coded suffix. Never raises. JARVIS_AUTOREPLY=0 disables.
    """
    try:
        import mail_log
        from system import log_action
        from system.inbox import (
            _extract_email,
            _gmail_send_api,
            _header,
            build_mime_b64,
            passon_body,
            scam_body,
        )

        if os.environ.get("JARVIS_AUTOREPLY", "1") == "0":
            return " replied=no(disabled)"
        thread_id = str(full.get("threadId", "") or "")
        addr = _extract_email(_header(full, "From") or parsed["sender"])
        if not addr:
            return " replied=no(no-address)"
        own = os.environ.get("JARVIS_OWNER_EMAIL", "").lower()
        if own and addr.lower() == own:
            return " replied=no(own-mail)"
        now = time.time()
        if not ack_allowed(acks, thread_id, addr, now):
            return " replied=no(already-acked)"
        subject = parsed["subject"]
        reply_subject = (
            subject if subject.lower().startswith("re:") else f"Re: {subject}"
        )
        body = (scam_body if scam else passon_body)(subject)
        await asyncio.to_thread(
            _gmail_send_api,
            token,
            build_mime_b64(
                addr,
                reply_subject,
                body,
                _header(full, "Message-ID"),
                auto=True,
                references=_header(full, "References"),
            ),
            thread_id or None,
        )
        note_ack(acks, thread_id, addr, now)
        with contextlib.suppress(Exception):
            mail_log.record(
                "acked", id=parsed["id"], to=addr, subject=reply_subject, body=body
            )
        log_action("mailwatch", f"passon-reply to={addr} subj={reply_subject[:60]}")
        return " replied=scam" if scam else " replied=passon"
    except Exception as exc:
        with contextlib.suppress(Exception):
            from system import log_action

            log_action("mailwatch", f"passon-reply error: {exc}")
        return " replied=error"


def state_path() -> Path:
    h = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(h) if h else Path.home() / ".jarvis") / "mail_watch.state.json"


def ack_allowed(acks: dict, thread_id: str, addr: str, now: float) -> bool:
    """No second auto-ack in a thread, none to a sender acked in the last day. Pure."""
    if thread_id and f"t:{thread_id}" in acks:
        return False
    last = float(acks.get(f"a:{addr}") or 0)
    return not (addr and now - last < ACK_SENDER_COOLDOWN_S)


def note_ack(acks: dict, thread_id: str, addr: str, now: float) -> None:
    if thread_id:
        acks[f"t:{thread_id}"] = now
    if addr:
        acks[f"a:{addr}"] = now
    for key in sorted(acks, key=lambda k: float(acks[k]))[
        : max(0, len(acks) - ACK_KEEP)
    ]:
        acks.pop(key, None)


def _load_state() -> dict:
    try:
        data = json.loads(state_path().read_text())
        if isinstance(data, dict):
            data.setdefault("seen", [])
            return data
    except Exception:
        pass
    return {"seen": []}


def _save_state(state: dict) -> None:
    try:
        state_path().parent.mkdir(parents=True, exist_ok=True)
        state_path().write_text(json.dumps(state))
    except Exception:
        pass


def _sender_name(sender: str) -> str:
    """Display name only: 'Teacher <t@..>' -> 'Teacher', 'a@b.c' -> 'a'."""
    name = str(sender or "").split("<")[0].strip().strip("'\"")
    if "@" in name:
        name = name.split("@")[0].strip()
    return name or str(sender or "")[:60]


def _notify_send(**kwargs) -> dict:
    """notify.send, fail-soft: a notify error must never break the watcher."""
    try:
        import notify

        return notify.send(**kwargs)
    except Exception:
        return {"route": "error"}


async def _ping_whatsapp(text: str) -> bool:
    chat = os.environ.get("JARVIS_WA_NOTIFY_CHAT", "").strip()
    if not chat:
        return False
    try:
        from system import whatsapp as wa

        if not await asyncio.to_thread(wa.alive):
            return False
        result = await wa.send_chat(chat, text[:500])
        return bool(result.get("ok"))
    except Exception:
        return False


async def _maybe_suggest_event(parsed: dict, full: dict) -> None:
    """Human mail that mentions a date: ask Sir out loud whether to book it. Never raises.

    Stores the extracted events (event_suggest) and announces the question;
    the silent-call announce keeps the call open, so his "yes" reaches
    confirm_calendar_import. Nothing is booked here.
    """
    try:
        import backend_model
        import event_suggest
        from system import log_action

        text = f"{parsed.get('subject', '')}\n{parsed.get('body') or parsed.get('snippet', '')}"
        if not event_suggest.enabled() or not event_suggest.has_date_cue(text):
            return
        if _run["events"] >= MAX_EVENT_CHECKS_PER_RUN:
            return
        _run["events"] += 1
        events, warning = await asyncio.to_thread(
            backend_model.extract_events_from_email, text
        )
        events = event_suggest.future_only(events)
        if warning or not events:
            log_action("mailwatch", f"event none id={parsed['id'][:16]} {warning or ''}"[:200])
            return
        try:
            import google_api
            from system.workspace_tools import _event_key

            days = sorted(e["date"] for e in events)
            existing = await asyncio.to_thread(
                google_api.calendar_list, days[0] + "T00:00:00Z", days[-1] + "T23:59:59Z"
            )
            have = {
                _event_key(
                    str(i.get("summary", "")),
                    str((i.get("start") or {}).get("date") or (i.get("start") or {}).get("dateTime") or "")[:10],
                )
                for i in existing
            }
            events = [e for e in events if _event_key(e["title"], e["date"]) not in have]
        except Exception:
            pass  # calendar unreachable: still worth asking
        if not events:
            log_action("mailwatch", f"event already-booked id={parsed['id'][:16]}")
            return
        event_suggest.save(parsed["id"], parsed["sender"], parsed["subject"], events)
        _notify_send(
            kind="calendar-suggest",
            source="mail",
            title="Jarvis - book this?",
            text=f"{parsed['sender']}: {parsed['subject']}",
            speak_text=event_suggest.speak_line(parsed["sender"], events),
            fingerprint=f"cal:{parsed['id']}",
        )
        log_action("mailwatch", f"event suggested n={len(events)} id={parsed['id'][:16]}")
    except Exception as exc:
        with contextlib.suppress(Exception):
            from system import log_action

            log_action("mailwatch", f"event error: {exc}")


async def _maybe_draft(parsed: dict, full: dict, verdict: dict) -> None:
    """Draft a reply for human normal/high mail (max 3 per run). Never raises.

    The draft is stored locally and announced by the agent's DraftWatcher;
    nothing is sent here. Outcome is logged via log_action.
    """
    try:
        import draft_engine
        from system import log_action
        from system.inbox import _extract_email, _header

        if verdict["level"] not in ("normal", "high") or not verdict["human"]:
            return
        if not draft_engine.drafts_enabled():
            return
        if draft_engine.draft_path(parsed["id"]).exists():
            return
        if _run["drafts"] >= MAX_DRAFTS_PER_RUN:
            log_action("mailwatch", f"draft cap-reached id={parsed['id'][:16]}")
            return
        to = _extract_email(_header(full, "From") or parsed["sender"])
        _run["drafts"] += 1
        thread_id = str(full.get("threadId", "") or "")
        context = await asyncio.to_thread(_draft_context, parsed["id"], thread_id, to)
        _, outcome = await asyncio.to_thread(
            lambda: draft_engine.create_draft(
                parsed,
                to=to,
                thread_id=thread_id,
                priority=str(verdict["level"]),
                context=context,
            )
        )
        log_action("mailwatch", f"draft {outcome} id={parsed['id'][:16]}")
        if outcome == "created":
            with contextlib.suppress(Exception):
                import mail_log

                rec = draft_engine.read_draft(parsed["id"]) or {}
                mail_log.record(
                    "drafted",
                    id=parsed["id"],
                    sender=parsed["sender"],
                    subject=parsed["subject"],
                    summary=rec.get("summary", ""),
                    body=rec.get("body", ""),
                )
            try:
                from draft_notify import announce_line

                record = draft_engine.read_draft(parsed["id"])
                if record is not None:
                    _notify_send(
                        kind="email-draft",
                        source="mail",
                        title="Jarvis - reply draft ready",
                        text=f"Reply draft for {parsed['sender']}: {parsed['subject']}",
                        speak_text=announce_line(record),
                        fingerprint=f"draft:{parsed['id']}",
                    )
            except Exception:
                pass
    except Exception as exc:
        with contextlib.suppress(Exception):
            from system import log_action

            log_action("mailwatch", f"draft error: {exc}")


def _draft_context(msg_id: str, thread_id: str, to: str) -> dict:
    """Earlier messages in the thread + Sir's past mail to this person.

    Gives the drafter the conversation so far and his own voice. Every
    lookup is best-effort: any failure just means less context.
    """
    from system.inbox import (
        _gmail_access_token,
        _gmail_api,
        parse_gmail_message,
    )

    out: dict = {"thread": [], "style": []}
    try:
        token = _gmail_access_token()
    except Exception:
        return out
    with contextlib.suppress(Exception):
        if thread_id:
            thread = _gmail_api(f"/threads/{thread_id}", token, {"format": "full"})
            for full in (thread.get("messages") or [])[-6:]:
                if full.get("id") == msg_id:
                    continue
                p = parse_gmail_message(full)
                out["thread"].append(
                    {
                        "sender": p["sender"],
                        "date": p["date"],
                        "body": (p["body"] or p["snippet"])[:600],
                    }
                )
    with contextlib.suppress(Exception):
        if to:
            listed = _gmail_api(
                "/messages", token, {"q": f"in:sent to:{to}", "maxResults": 3}
            )
            for m in listed.get("messages") or []:
                p = parse_gmail_message(
                    _gmail_api(f"/messages/{m['id']}", token, {"format": "full"})
                )
                if p["body"]:
                    out["style"].append(p["body"][:500])
    return out


async def _claude_urgency(parsed: dict, verdict: dict) -> dict:
    """Let Claude make the urgent call on human mail; keywords are the fallback.

    Never raises: any failure returns the keyword verdict unchanged.
    """
    try:
        import urgency_judge
        from system import log_action

        if not urgency_judge.enabled():
            return verdict
        ai, warning = await asyncio.to_thread(urgency_judge.judge, parsed)
        if ai is None:
            log_action("mailwatch", f"urgency fallback: {warning}")
            return verdict
        final = urgency_judge.apply(verdict, ai)
        if final["level"] != verdict["level"]:
            log_action(
                "mailwatch",
                f"urgency claude changed {verdict['level']}->{final['level']} id={parsed['id'][:16]}",
            )
        return final
    except Exception:
        return verdict


async def _handle_one(msg_id: str, acks: dict | None = None) -> str:
    """Triage + act on one message. Returns a reason-coded outcome.

    acks is the persisted auto-acknowledgement memory (thread + sender);
    None means no memory (every high human mail may be acknowledged).
    """
    import mail_log

    acks = {} if acks is None else acks
    from system import log_action
    from system.inbox import (
        _gmail_access_token,
        _gmail_api,
        _gmail_send_api,
        _header,
        autoreply_body,
        build_mime_b64,
        classify_email,
        parse_gmail_message,
    )

    token = await asyncio.to_thread(_gmail_access_token)
    full = await asyncio.to_thread(
        _gmail_api, f"/messages/{msg_id}", token, {"format": "full"}
    )
    parsed = parse_gmail_message(full)
    sender, subject = parsed["sender"], parsed["subject"]
    verdict = classify_email(sender, subject, parsed.get("snippet", ""), full)
    if verdict["level"] != "skip" and verdict["human"]:
        verdict = await _claude_urgency(parsed, verdict)
    level = str(verdict["level"])
    tag = f"{level} [{'|'.join(str(r) for r in verdict['reasons'])}]"
    log_action("mailwatch", f"{tag} from={sender[:60]} subj={subject[:80]}")

    if level == "skip":
        return "skip"
    if level == "normal":
        suffix = ""
        scam = bool(verdict.get("scam"))
        if verdict["human"]:
            suffix = await _pass_on_reply(full, parsed, token, acks, scam=scam)
        if not scam:
            if verdict["human"]:
                await _maybe_suggest_event(parsed, full)
            await _maybe_draft(parsed, full, verdict)
        return "normal-logged" + suffix

    mail_log.record(
        "flagged",
        id=msg_id,
        sender=sender,
        subject=subject,
        level=level,
        summary=", ".join(str(r) for r in verdict["reasons"][:3]),
    )
    # High: ping + desktop notification always; reply only human senders.
    ping = (
        f"High-priority mail from {sender[:60]}: {subject[:100]}. "
        f"Why: {', '.join(str(r) for r in verdict['reasons'][:3])}."
    )
    pinged = await _ping_whatsapp(ping)
    outcome = f"high pinged={pinged}"
    acked = False
    try:
        from system.inbox import _extract_email

        addr = _extract_email(_header(full, "From") or sender)
        thread_id = str(full.get("threadId", "") or "")
        now = time.time()
        if not ack_allowed(acks, thread_id, addr, now):
            outcome += " replied=no(already-acked)"
        elif verdict["human"] and os.environ.get("JARVIS_AUTOREPLY", "1") != "0":
            orig_from = addr or sender
            reply_subject = (
                subject if subject.lower().startswith("re:") else f"Re: {subject}"
            )
            body = autoreply_body(subject)
            message_id = _header(full, "Message-ID")
            await asyncio.to_thread(
                _gmail_send_api,
                token,
                build_mime_b64(
                    orig_from,
                    reply_subject,
                    body,
                    message_id,
                    auto=True,
                    references=_header(full, "References"),
                ),
                thread_id or None,
            )
            acked = True
            note_ack(acks, thread_id, addr, now)
            mail_log.record(
                "acked", id=msg_id, to=orig_from, subject=reply_subject, body=body
            )
            try:
                from system import log_action as _log

                _log(
                    "mailwatch",
                    f"autoconfirm-reply to={orig_from} subj={reply_subject[:60]}",
                )
            except Exception:
                pass
            outcome += " replied=autoconfirm"
        else:
            outcome += " replied=no(nonhuman-or-disabled)"
    finally:
        # Tell Sir even if the acknowledgement failed: what arrived, and
        # whether Jarvis already answered it for him.
        who = _sender_name(sender)
        told = " I sent them an automatic acknowledgement." if acked else ""
        _notify_send(
            kind="email-urgent",
            source="mail",
            urgency="urgent",
            title="Jarvis - priority mail",
            text=f"{sender}: {subject}" + (" (acknowledged)" if acked else ""),
            speak_text=f"Sir, urgent mail from {who}: {subject}.{told}",
        )
    if verdict["human"]:
        await _maybe_suggest_event(parsed, full)
    await _maybe_draft(parsed, full, verdict)
    return outcome


async def main() -> None:
    from system import log_action

    os.environ["JARVIS_LOCAL"] = "1"
    _run["drafts"] = 0
    _run["events"] = 0
    state = _load_state()
    seen = set(state.get("seen", []))
    try:
        from system.inbox import _gmail_access_token, _gmail_api

        token = await asyncio.to_thread(_gmail_access_token)
        listed = await asyncio.to_thread(
            _gmail_api,
            "/messages",
            token,
            {"q": "is:unread newer_than:2d", "maxResults": MAX_PER_RUN},
        )
    except Exception as exc:
        log_action("mailwatch", f"poll failed: {exc}")
        return
    seen_map = dict(state.get("seen_map", {}))
    for entry in seen:
        # Back-compat with the firstdraft "id:outcome" list format.
        seen_map.setdefault(str(entry).split(":")[0], "seen")
    fresh = [
        str(m.get("id", ""))
        for m in (listed.get("messages", []) or [])
        if m.get("id") and m.get("id") not in seen_map
    ]
    if not fresh:
        return
    acks = dict(state.get("acks", {}))
    for msg_id in fresh:
        try:
            outcome = await _handle_one(msg_id, acks)
        except Exception as exc:
            from system import log_action as _log

            with __import__("contextlib").suppress(Exception):
                _log("mailwatch", f"{msg_id[:16]} error: {exc}")
            outcome = "error"
        seen_map[msg_id] = outcome
        trimmed = dict(list(seen_map.items())[-500:])
        _save_state({**state, "seen": [], "seen_map": trimmed, "acks": acks})
    with contextlib.suppress(Exception):
        log_action("mailwatch", f"run done new={len(fresh)}")


def start_thread(interval: float = 60.0, first_delay: float = 20.0):
    """Run the mail check inside the Jarvis service (bridge sidecar).

    Every `interval` seconds (1 minute) after a short warm-up. Returns
    (thread, stop event). JARVIS_MAIL_WATCH=0 switches it off. The owner
    address and auto-acknowledgement default match the old cron line.
    """
    import threading

    os.environ.setdefault("JARVIS_OWNER_EMAIL", "ripjkgaming@gmail.com")
    os.environ.setdefault("JARVIS_AUTOREPLY", "1")
    stop = threading.Event()

    def _loop() -> None:
        if stop.wait(first_delay):
            return
        while not stop.is_set():
            with contextlib.suppress(Exception):
                asyncio.run(main())
            if stop.wait(interval):
                return

    thread = threading.Thread(target=_loop, name="mail-watch", daemon=True)
    thread.start()
    return thread, stop


if __name__ == "__main__":
    asyncio.run(main())
