"""Ask Jarvis what it did on Sir's behalf: WhatsApp replies, email, a recap.

Reads the logs the background jobs already write, so every question works
from the Jarvis UI or by voice without opening WhatsApp or Gmail:
- ~/.jarvis/wa_digest.jsonl (wa_autoreply: every reply, dry run, handoff)
- ~/.jarvis/mail_digest.jsonl (mail_log: flagged, acked, drafted, sent)

"What did you say to Aarav", "who did you reply to on WhatsApp", "what
emails did you reply to", "what happened while I was away". Read-only.
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, require_local


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _when(ts: float, now: float) -> str:
    """'at 14:05' today, else 'yesterday 14:05' / 'Mon 14:05'. Pure."""
    t, n = datetime.fromtimestamp(ts), datetime.fromtimestamp(now)
    clock = t.strftime("%H:%M")
    days = (n.date() - t.date()).days
    if days == 0:
        return f"at {clock}"
    if days == 1:
        return f"yesterday {clock}"
    return f"{t.strftime('%a %d %b')} {clock}"


def wa_entries(
    since: float = 0.0, chat: str = "", limit: int = 50, path=None
) -> list[dict]:
    """WhatsApp digest entries, newest first, optionally one chat. Never raises."""
    import wa_autoreply

    chat = " ".join(chat.split()).lower()
    try:
        lines = (path or wa_autoreply.digest_path()).read_text().splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or float(e.get("ts") or 0) < since:
            continue
        if chat and chat not in str(e.get("chat", "")).lower():
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


def describe_wa(entries: list[dict], now: float, n: int = 5) -> str:
    """Spoken lines about WhatsApp activity. Pure."""
    sent = [e for e in entries if e.get("sent")]
    handoffs = [e for e in entries if e.get("why") == "handoff"]
    dry = [e for e in entries if e.get("why") == "dry-run"]
    lines: list[str] = []
    for e in sent[:n]:
        voice = " as you" if e.get("as_owner") else ""
        they = _clip("; ".join(e.get("incoming") or []), 90)
        lines.append(
            f'{e.get("chat")} {_when(float(e["ts"]), now)}: they said "{they}", '
            f'I replied{voice} "{_clip(e.get("reply", ""), 140)}"'
        )
    for e in handoffs[:3]:
        lines.append(
            f"{e.get('chat')} {_when(float(e['ts']), now)}: left for you, "
            f"{_clip(e.get('for_owner') or 'needs you personally', 100)}"
        )
    if not sent and dry:
        lines.append(
            f"{len(dry)} replies were drafted in dry-run mode but not sent "
            "(JARVIS_WA_AUTOREPLY is not live)"
        )
    return ". ".join(lines)


def describe_mail(entries: list[dict], now: float, n: int = 5) -> str:
    """Spoken lines about email activity. Pure."""
    verbs = {
        "acked": "auto-acknowledged",
        "sent": "sent",
        "drafted": "drafted a reply to",
        "flagged": "flagged as urgent",
        "discarded": "discarded the draft for",
        "received": "saw mail from",
    }
    lines = []
    for e in entries[:n]:
        who = e.get("to") or e.get("sender") or "someone"
        what = _clip(e.get("subject", ""), 80)
        extra = ""
        if e.get("kind") in ("sent", "acked", "drafted") and e.get("body"):
            extra = f', saying "{_clip(e["body"], 110)}"'
        lines.append(
            f"{_when(float(e['ts']), now)} I {verbs.get(e.get('kind'), e.get('kind'))} "
            f"{_clip(who, 50)}: {what}{extra}"
        )
    return ". ".join(lines)


_MAIL_KINDS = {
    "all": None,
    "replied": ("acked", "sent"),
    "replies": ("acked", "sent"),
    "sent": ("sent",),
    "acked": ("acked",),
    "flagged": ("flagged",),
    "urgent": ("flagged",),
    "drafted": ("drafted",),
    "drafts": ("drafted",),
}


def recap(hours: float, now: float | None = None) -> str:
    """Everything Jarvis handled in the last `hours`, one short paragraph."""
    import mail_log

    now = time.time() if now is None else now
    since = now - hours * 3600
    parts: list[str] = []
    wa = wa_entries(since)
    sent = [e for e in wa if e.get("sent")]
    handoffs = [e for e in wa if e.get("why") == "handoff"]
    if sent:
        chats = sorted({str(e.get("chat")) for e in sent})
        parts.append(
            f"WhatsApp: I replied {len(sent)} time{'s' if len(sent) != 1 else ''} "
            f"to {', '.join(chats[:6])}"
        )
    if handoffs:
        parts.append(
            "Waiting for you on WhatsApp: "
            + "; ".join(
                f"{e.get('chat')} ({_clip(e.get('for_owner', ''), 70)})"
                for e in handoffs[:3]
            )
        )
    passed = [e for e in sent if e.get("pass_along")]
    if passed:
        parts.append(
            "Messages to pass on: "
            + "; ".join(
                f"{e.get('chat')}: {_clip(e.get('for_owner', ''), 80)}"
                for e in passed[:4]
            )
        )
    mail = mail_log.recent(since=since, limit=100)
    flagged = [e for e in mail if e.get("kind") == "flagged"]
    replied = [e for e in mail if e.get("kind") in ("acked", "sent")]
    drafted = [e for e in mail if e.get("kind") == "drafted"]
    if flagged:
        parts.append(
            f"Urgent mail: {len(flagged)}, "
            + "; ".join(
                f"{_clip(e.get('sender', ''), 30)}: {_clip(e.get('subject', ''), 50)}"
                for e in flagged[:3]
            )
        )
    if replied:
        parts.append(
            f"I answered {len(replied)} email{'s' if len(replied) != 1 else ''}"
        )
    try:
        import draft_engine

        waiting = [
            d for s in ("pending", "announced") for d in draft_engine.list_drafts(s)
        ]
    except Exception:
        waiting = []
    if waiting or drafted:
        parts.append(
            f"{len(waiting)} reply draft{'s' if len(waiting) != 1 else ''} waiting for you"
        )
    try:
        import exams

        nxt = exams.next_exam()
        if nxt:
            parts.append("Next exam: " + exams.describe(nxt))
    except Exception:
        pass
    if not parts:
        return f"Nothing needed you in the last {int(hours)} hours, Sir."
    return ". ".join(parts) + "."


class RecallTools:
    """Read-only recall of what Jarvis did. Register via .tools."""

    @property
    def tools(self) -> list:
        return [self.whatsapp_replies, self.email_activity, self.catch_me_up]

    @function_tool()
    async def whatsapp_replies(
        self, context: RunContext, chat: str = "", hours: float = 48, n: int = 5
    ) -> dict[str, str]:
        """What Jarvis auto-replied on WhatsApp: "what did you say to Aarav",
        "who did you reply to", "did anyone message me". Also lists chats
        Jarvis left for Sir (handoffs).

        Args:
            chat: Part of a chat name to filter to; empty = everyone.
            hours: How far back to look (1-336).
            n: How many replies to describe (1-10).
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        now = time.time()
        hours = max(1.0, min(336.0, float(hours or 48)))
        entries = wa_entries(now - hours * 3600, chat or "")
        say = describe_wa(entries, now, max(1, min(10, int(n or 5))))
        if not say:
            who = f" to {chat.strip()}" if (chat or "").strip() else ""
            return {
                "say": f"I haven't replied{who} on WhatsApp in the last {int(hours)} hours, Sir."
            }
        return {"say": say[:1500]}

    @function_tool()
    async def email_activity(
        self,
        context: RunContext,
        kind: str = "all",
        who: str = "",
        hours: float = 72,
        n: int = 5,
    ) -> dict[str, str]:
        """What Jarvis did with Sir's email: "what emails did you reply to",
        "what did you send to my teacher", "any urgent mail", "what did you draft".

        Args:
            kind: all, replied (auto-acks + sent), sent, flagged, or drafted.
            who: Part of a name or address to filter to.
            hours: How far back to look (1-336).
            n: How many to describe (1-10).
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        import mail_log

        key = (kind or "all").strip().lower()
        if key not in _MAIL_KINDS:
            raise ToolError("kind must be all, replied, sent, flagged or drafted.")
        now = time.time()
        hours = max(1.0, min(336.0, float(hours or 72)))
        entries = mail_log.recent(
            kinds=_MAIL_KINDS[key], since=now - hours * 3600, who=who or ""
        )
        if not entries:
            return {
                "say": f"No email activity like that in the last {int(hours)} hours, Sir."
            }
        return {"say": describe_mail(entries, now, max(1, min(10, int(n or 5))))[:1500]}

    @function_tool()
    async def catch_me_up(
        self, context: RunContext, hours: float = 12
    ) -> dict[str, str]:
        """Recap of everything Jarvis handled while Sir was away: WhatsApp
        replies and handoffs, messages to pass on, urgent and answered email,
        drafts waiting, and the next exam. "What did I miss", "catch me up".

        Args:
            hours: How far back (1-72).
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        hours = max(1.0, min(72.0, float(hours or 12)))
        return {"say": recap(hours)[:1500]}
