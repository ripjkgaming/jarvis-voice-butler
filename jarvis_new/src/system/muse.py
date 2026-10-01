"""Delegate a task to Meta's Muse agent, by email or WhatsApp.

Muse (Meta's agent, launched 2026) has its own email address and lives in
WhatsApp, so Jarvis can hand it a job the way Sir would hand it to a
person: write the brief, read it back, and — only once Sir confirms —
send it. Nothing goes out without that confirmation.

The Muse address is configurable (JARVIS_MUSE_EMAIL or
~/.jarvis/muse_email.txt) because Meta assigns it per-account; the
WhatsApp chat name defaults to "Muse" (JARVIS_MUSE_CHAT). The auto-reply
watcher blocklists the Muse chat so Jarvis never mimics Sir back at Muse.

Pure helpers build the brief; the senders are injected so the tool layer
and the tests share one code path.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

CHAT_DEFAULT = "Muse"
_MUSE_EMAIL_FILE = Path.home() / ".jarvis" / "muse_email.txt"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def enabled() -> bool:
    """Is Muse set up? Opt-in: nothing Muse-related exists until Sir
    configures it (an address, a chat name, or JARVIS_MUSE=1)."""
    if os.environ.get("JARVIS_MUSE", "").strip().lower() in ("1", "true", "on", "yes"):
        return True
    if (os.environ.get("JARVIS_MUSE_CHAT") or "").strip():
        return True
    return bool(muse_email())


def muse_email() -> str:
    """Muse's email address, or "" if Sir hasn't set one yet."""
    val = (os.environ.get("JARVIS_MUSE_EMAIL") or "").strip()
    if not val:
        try:
            val = _MUSE_EMAIL_FILE.read_text().strip().splitlines()[0].strip()
        except Exception:
            val = ""
    return val if _EMAIL_RE.match(val) else ""


def muse_chat() -> str:
    """WhatsApp chat name for Muse (default 'Muse')."""
    return (os.environ.get("JARVIS_MUSE_CHAT") or CHAT_DEFAULT).strip() or CHAT_DEFAULT


def pick_channel(via: str) -> str:
    """Normalise the requested channel: email / whatsapp / auto. Pure.

    'auto' prefers email when an address is configured, else WhatsApp.
    """
    v = (via or "auto").strip().lower()
    if v in ("mail", "e-mail", "gmail"):
        v = "email"
    if v in ("wa", "chat", "text", "message"):
        v = "whatsapp"
    if v not in ("email", "whatsapp", "auto"):
        v = "auto"
    if v == "auto":
        return "email" if muse_email() else "whatsapp"
    return v


def clean_task(task: str) -> str:
    """Trim and collapse the task text. Pure."""
    return " ".join((task or "").split()).strip()


def email_brief(task: str) -> tuple[str, str]:
    """(subject, body) for a Muse delegation email. Pure."""
    task = clean_task(task)
    words = task.rstrip(".?!").split()
    subject = "Task for Muse: " + (
        " ".join(words[:8]) + ("…" if len(words) > 8 else "")
    )
    body = (
        f"Hi Muse,\n\n{task}\n\n"
        "Please take this one and send the result back to this address "
        "when it's ready.\n\nThanks,\nJarvis (on behalf of Sir)"
    )
    return subject, body


def wa_brief(task: str) -> str:
    """WhatsApp one-liner for a Muse delegation. Pure."""
    return f"Hey Muse — a task for you: {clean_task(task)}. Send the result back here when done. (Jarvis, for Sir)"


def preview(task: str, channel: str) -> str:
    """Spoken read-back of what will be sent, for confirmation. Pure."""
    task = clean_task(task)
    if channel == "email":
        where = f"email ({muse_email() or 'no address set'})"
    else:
        where = f"WhatsApp ({muse_chat()})"
    return f"I'll delegate this to Muse over {where}: \"{task}\". Say confirm and I'll send it."
