"""Headless mail watcher: poll unread, ping WhatsApp, auto-reply high-priority.

Runs from cron every 5 minutes (install below). For each unseen unread
message it triages via system.inbox.classify_email:

- skip (own mail, noise): logged only.
- normal: logged; surfaces through gmail tools + morning briefing.
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

STATE_PATH = Path.home() / ".jarvis" / "mail_watch.state.json"
MAX_PER_RUN = 20
MAX_DRAFTS_PER_RUN = 3
_run = {"drafts": 0}


def _load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text())
        if isinstance(data, dict):
            data.setdefault("seen", [])
            return data
    except Exception:
        pass
    return {"seen": []}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state))
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
        _, outcome = await asyncio.to_thread(
            lambda: draft_engine.create_draft(
                parsed,
                to=to,
                thread_id=str(full.get("threadId", "") or ""),
                priority=str(verdict["level"]),
            )
        )
        log_action("mailwatch", f"draft {outcome} id={parsed['id'][:16]}")
        if outcome == "created":
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


async def _handle_one(msg_id: str) -> str:
    """Triage + act on one message. Returns a reason-coded outcome."""
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
        await _maybe_draft(parsed, full, verdict)
        return "normal-logged"

    # High: ping + desktop notification always; reply only human senders.
    ping = (
        f"High-priority mail from {sender[:60]}: {subject[:100]}. "
        f"Why: {', '.join(str(r) for r in verdict['reasons'][:3])}."
    )
    pinged = await _ping_whatsapp(ping)
    outcome = f"high pinged={pinged}"
    acked = False
    try:
        if verdict["human"] and os.environ.get("JARVIS_AUTOREPLY", "1") != "0":
            orig_from = sender
            try:
                from system.inbox import _extract_email

                addr = _extract_email(_header(full, "From") or sender)
                if addr:
                    orig_from = addr
            except Exception:
                pass
            reply_subject = (
                subject if subject.lower().startswith("re:") else f"Re: {subject}"
            )
            body = autoreply_body(subject)
            message_id = _header(full, "Message-ID")
            thread_id = str(full.get("threadId", "") or "") or None
            await asyncio.to_thread(
                _gmail_send_api,
                token,
                build_mime_b64(orig_from, reply_subject, body, message_id),
                thread_id,
            )
            acked = True
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
    await _maybe_draft(parsed, full, verdict)
    return outcome


async def main() -> None:
    from system import log_action

    os.environ["JARVIS_LOCAL"] = "1"
    _run["drafts"] = 0
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
    for msg_id in fresh:
        try:
            outcome = await _handle_one(msg_id)
        except Exception as exc:
            from system import log_action as _log

            with __import__("contextlib").suppress(Exception):
                _log("mailwatch", f"{msg_id[:16]} error: {exc}")
            outcome = "error"
        seen_map[msg_id] = outcome
        trimmed = dict(list(seen_map.items())[-500:])
        _save_state({**state, "seen": [], "seen_map": trimmed})
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
