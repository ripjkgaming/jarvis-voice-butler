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
    (crontab -l 2>/dev/null; echo "*/5 * * * * cd /home/ripjk/jarvis-voice-butler/jarvis_new && JARVIS_LOCAL=1 JARVIS_OWNER_EMAIL=ripjkgaming@gmail.com JARVIS_WA_NOTIFY_CHAT='Message yourself' .venv/bin/python src/mail_watch_job.py") | crontab -
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

STATE_PATH = Path.home() / ".jarvis" / "mail_watch.state.json"
MAX_PER_RUN = 20


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


def _notify_desktop(title: str, text: str) -> None:
    with contextlib.suppress(Exception):
        subprocess.run(["notify-send", title, text[:500]], timeout=10, check=False)


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
    level = str(verdict["level"])
    tag = f"{level} [{'|'.join(str(r) for r in verdict['reasons'])}]"
    log_action("mailwatch", f"{tag} from={sender[:60]} subj={subject[:80]}")

    if level == "skip":
        return "skip"
    if level == "normal":
        return "normal-logged"

    # High: ping + desktop notification always; reply only human senders.
    ping = (
        f"High-priority mail from {sender[:60]}: {subject[:100]}. "
        f"Why: {', '.join(str(r) for r in verdict['reasons'][:3])}."
    )
    pinged = await _ping_whatsapp(ping)
    _notify_desktop("Jarvis — priority mail", f"{sender}: {subject}")
    outcome = f"high pinged={pinged}"
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
    return outcome


async def main() -> None:
    from system import log_action

    os.environ["JARVIS_LOCAL"] = "1"
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


if __name__ == "__main__":
    asyncio.run(main())
