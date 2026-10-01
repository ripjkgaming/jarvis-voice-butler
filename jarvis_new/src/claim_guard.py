"""Catch "done" claims that no tool call backs up.

Gemini Live sometimes narrates success it never produced: "turned it
down", "paused", "all approved" with no tool call, or with a tool that
errored. Each tool is now verified (audio_ctl, draft_approve_all, ...),
but the model can still speak first. This is the general backstop: it
watches one user turn at a time and compares what Jarvis *said* he did
against what actually ran.

    note_user(text)      a final user transcript (starts a new turn)
    note_tool(name, ok)  a tool finished (agent events + the fast path)
    check(assistant)     -> Verdict | None for each assistant utterance

A verdict means "claimed <category> with no successful matching tool".
The agent then makes Jarvis really do it (one re-prompt with tools on),
and if he still claims without acting, speaks a fixed honest line.
Pure logic, injectable clock, no I/O. JARVIS_CLAIM_GUARD=0 disables it.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

USER_WINDOW_S = 90.0  # a claim this long after Sir's request still answers it
MAX_CORRECTIONS = 2  # re-prompt once, then one fixed honest line, then silence

# claim category -> verbs that claim it
_CLAIM_VERBS: dict[str, str] = {
    "send": r"approved|sent|replied|forwarded|delivered|emailed|messaged|posted",
    "media": r"paused|resumed|unpaused|skipped|muted|unmuted|lowered|raised|silenced",
    "window": r"opened|closed|launched|dismissed|minimi[sz]ed|maximi[sz]ed",
    "delete": r"deleted|discarded|removed|cancel+ed|archived|trashed|cleared",
    "create": r"scheduled|booked|added|saved|created|updated|changed|noted|remembered|set",
}
# a tool name containing any of these can back that category
_TOOL_KEYS: dict[str, tuple[str, ...]] = {
    "send": (
        "send",
        "reply",
        "approve",
        "mail",
        "whatsapp",
        "message",
        "muse",
        "forward",
        "post",
    ),
    "media": ("media", "volume", "play", "mute", "pause", "music", "audio", "spotify"),
    "window": (
        "open",
        "close",
        "app",
        "window",
        "launch",
        "url",
        "ui",
        "project",
        "page",
        "browser",
        "navigate",
    ),
    "delete": (
        "delete",
        "discard",
        "remove",
        "cancel",
        "archive",
        "dismiss",
        "trash",
        "clear",
    ),
    "create": (
        "add",
        "create",
        "schedule",
        "save",
        "set",
        "update",
        "calendar",
        "remember",
        "todo",
        "reminder",
        "note",
        "event",
        "book",
        "import",
        "exam",
        "memory",
    ),
}
# "turned it down/up" is media; "turned X on/off" is not covered here
_TURNED = re.compile(r"\bturned (?:it |the \w+ )?(?:down|up)\b")
_ALL = "|".join(_CLAIM_VERBS.values())

# "I've approved", "I just sent", "it's paused", "they're all sent"
_FIRST_PERSON = re.compile(
    rf"\b(?:i(?:'ve| have|'m| am)?|we(?:'ve| have)?|jarvis)\s+(?:just\s+|now\s+|already\s+|all\s+)*(?:{_ALL})\b"
)
_STATE = re.compile(
    rf"\b(?:it(?:'s| is| has been)|they(?:'re| are| have been)|that(?:'s| is| has been)|"
    rf"drafts? (?:is|are|were|have been)|all)\s+(?:now\s+|all\s+|been\s+)*(?:{_ALL})\b"
)
# a short standalone confirmation: "Done, Sir.", "Approved.", "Paused."
_BARE = re.compile(rf"^(?:all\s+)?(?:done|{_ALL})(?:\s+(?:sir|all|now))?$")
_NEGATED = re.compile(
    r"\b(?:not|n't|never|couldn't|can't|cannot|unable|failed|didn't|haven't|"
    r"won't|wasn't|unless|if|once|before|would|should|will|shall|going to|"
    r"want me|shall i|do you|did you|let me)\b"
)
# Sir's last turn was an instruction to act, not chit-chat
_REQUEST = re.compile(
    r"\b(?:approve|send|reply|respond|forward|pause|resume|play|skip|mute|unmute|"
    r"turn|lower|raise|louder|quieter|volume|open|close|launch|start|stop|delete|"
    r"discard|remove|cancel|archive|schedule|book|add|save|set|remind|remember|"
    r"create|update|change|dismiss|clear|do it|go ahead|yes|yeah|confirm)\b"
)


@dataclass
class Verdict:
    category: str  # send / media / window / delete / create / done
    claim: str  # the sentence that claimed it
    reason: str  # "no-tool" | "tool-failed"
    detail: str = ""  # failed tool names


@dataclass
class _Turn:
    text: str = ""
    at: float = 0.0
    tools_ok: list[str] = field(default_factory=list)
    tools_failed: list[str] = field(default_factory=list)
    corrections: int = 0


def enabled() -> bool:
    return os.environ.get("JARVIS_CLAIM_GUARD", "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ,]+", " ", (text or "").lower()).split())


def claim_category(sentence: str) -> str | None:
    """The category of action this sentence claims to have done, or None. Pure."""
    s = _norm(sentence)
    if not s or "?" in sentence or _NEGATED.search(s):
        return None
    bare = re.sub(r",", "", s)
    hit = (
        _FIRST_PERSON.search(s)
        or _STATE.search(s)
        or _BARE.match(bare)
        or _TURNED.search(s)
    )
    if not hit:
        return None
    if _TURNED.search(s):
        return "media"
    verb = re.search(rf"\b({_ALL})\b", s)
    if verb is None:  # bare "done"
        return "done"
    for cat, verbs in _CLAIM_VERBS.items():
        if re.fullmatch(verbs, verb.group(1)):
            return cat
    return "done"


def claims(text: str) -> list[tuple[str, str]]:
    """[(category, sentence)] for every action claim in an utterance. Pure."""
    out = []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        cat = claim_category(sent)
        if cat:
            out.append((cat, sent.strip()))
    return out


class ClaimGuard:
    """Per-user-turn ledger of what ran vs. what was claimed."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._turn = _Turn()

    def note_user(self, text: str) -> None:
        """A new final user transcript starts a new turn."""
        self._turn = _Turn(text=text or "", at=self._clock())

    def note_tool(self, name: str, ok: bool) -> None:
        (self._turn.tools_ok if ok else self._turn.tools_failed).append(str(name or ""))

    def _backed(self, category: str) -> bool:
        if category == "done":
            return bool(self._turn.tools_ok)
        keys = _TOOL_KEYS.get(category, ())
        return any(any(k in n.lower() for k in keys) for n in self._turn.tools_ok)

    def check(self, assistant_text: str) -> Verdict | None:
        """A Verdict when Jarvis claimed an action nothing backs up."""
        turn = self._turn
        if not turn.text or self._clock() - turn.at > USER_WINDOW_S:
            return None  # proactive speech / no request to answer
        if not _REQUEST.search(_norm(turn.text)):
            return None  # chit-chat, not a command
        for category, sentence in claims(assistant_text):
            if self._backed(category):
                continue
            failed = list(turn.tools_failed)
            return Verdict(
                category=category,
                claim=sentence[:160],
                reason="tool-failed" if failed else "no-tool",
                detail=", ".join(failed[:3]),
            )
        return None

    def next_correction(self) -> int:
        """1 = re-prompt with tools, 2 = fixed honest line, 0 = stop. Counts up."""
        if self._turn.corrections >= MAX_CORRECTIONS:
            return 0
        self._turn.corrections += 1
        return self._turn.corrections

    @property
    def user_text(self) -> str:
        return self._turn.text


GUARD = ClaimGuard()


def reprompt(verdict: Verdict, user_text: str) -> str:
    """Instructions that make Jarvis actually do it, or say he can't."""
    why = (
        f"the tool failed ({verdict.detail})"
        if verdict.reason == "tool-failed"
        else "no tool was called"
    )
    return (
        f'Sir asked: "{user_text[:200]}". You just said "{verdict.claim}", but '
        f"{why}, so it did NOT happen. Do not repeat that claim. If a tool can "
        "do it, call that tool now and report only its real result. If you "
        "cannot, tell Sir plainly in one short sentence what did not happen "
        "and why."
    )


def fixed_line(verdict: Verdict) -> str:
    """Last resort: never leave a false 'done' standing."""
    return (
        "Correction, Sir: I said that was done, but it wasn't. "
        "Nothing actually happened. Please ask me again."
    )
