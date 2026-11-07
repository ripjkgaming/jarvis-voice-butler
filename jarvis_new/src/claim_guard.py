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

import ast
import json
import os
import re
import time
from dataclasses import dataclass, field

from action_outcome import parse_outcome

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


# "I cannot access your emails", "I don't have access to that", "I'm unable
# to check": a refusal. Fine after a tool failed; a giveaway when Jarvis
# never tried (the tools exist, Gemini just answered from nothing).
_REFUSAL = re.compile(
    r"\b(?:i(?:'m| am)? (?:afraid i )?(?:can't|cannot|can not|am unable to|'m unable to|"
    r"unable to|don't have (?:access|the ability|a way)|do not have (?:access|the ability|a way)|"
    r"have no (?:access|way)|don't know|do not know|couldn't|could not)|"
    r"i'm afraid i (?:can't|cannot|don't|do not)|"
    r"(?:that's|that is|it's|it is) (?:not possible|beyond me|unavailable))\b"
)
# Sir's turn carried a goal: a command, or a question about his own world.
_GOAL = re.compile(
    r"\b(?:my|me|what|what's|whats|when|where|who|which|how|any|check|show|tell|"
    r"read|find|get|list|look|see|search|is there|are there|do i|have i)\b"
)


def refusal(sentence: str) -> bool:
    """Does this sentence refuse or plead ignorance? Pure."""
    return bool(_REFUSAL.search(_norm(sentence)))


@dataclass
class Verdict:
    category: str  # send / media / window / delete / create / done
    claim: str  # the sentence that claimed it
    reason: str  # "no-tool" | "tool-failed"
    detail: str = ""  # failed tool names
    uncertain: bool = False


@dataclass
class _Turn:
    text: str = ""
    at: float = 0.0
    tools_ok: list[str] = field(default_factory=list)
    tools_failed: list[str] = field(default_factory=list)
    outcomes: list = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    corrections: int = 0
    calendar_read_titles: list[str] = field(default_factory=list)


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


_MIGRATED = {
    "open_app",
    "open_url",
    "open_visible_url",
    "open_google_app",
    "browse_url",
    "write_file",
    "set_volume",
    "volume_set",
    "volume_up",
    "volume_down",
    "volume_mute",
    "volume_unmute",
    "click",
    "type_text",
}


def sdk_tool_result(output):
    """Installed SDK stringifies dict outputs; accept bounded literal/JSON data."""
    value = getattr(output, "output", None)
    if not isinstance(value, str) or len(value) > 65536:
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        try:
            tree = ast.parse(value, mode="eval")
            if sum(1 for _ in ast.walk(tree)) > 512:
                return None
            result = ast.literal_eval(tree)
            return result if isinstance(result, dict) else None
        except (ValueError, TypeError, SyntaxError, RecursionError, MemoryError):
            return None


class ClaimGuard:
    """Per-user-turn ledger of what ran vs. what was claimed."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._turn = _Turn()
        self.calendar_context = False

    def note_user(self, text: str) -> None:
        """A new final user transcript starts a new turn."""
        cleaned = _norm(text)
        self.calendar_context = bool(re.search(r"\bcalendar\b", cleaned)) or (
            self.calendar_context
            and (
                not _REQUEST.search(cleaned)
                or bool(
                    re.search(
                        r"\b(?:confirm|yes|added|scheduled|january|february|march|april|may|june|july|august|september|october|november|december|20\d{2})\b",
                        cleaned,
                    )
                )
            )
        )
        self._turn = _Turn(text=text or "", at=self._clock())

    def note_tool(self, name: str, ok: bool) -> None:
        name = str(name or "")
        if name in _MIGRATED:
            self._turn.unverified.append(name)
            if not ok:
                self._turn.tools_failed.append(name)
        else:
            (self._turn.tools_ok if ok else self._turn.tools_failed).append(name)

    def note_result(self, name: str, result, *, is_error=False) -> None:
        outcome = parse_outcome(result)
        if outcome is not None and not is_error:
            self._turn.outcomes.append((name, outcome))
            if (
                outcome.verified
                and outcome.effect == "calendar.read"
                and isinstance(result, dict)
            ):
                self._turn.calendar_read_titles.extend(
                    _norm(str(item.get("title") or ""))
                    for item in result.get("events", [])[:250]
                    if isinstance(item, dict) and item.get("title")
                )
        else:
            self.note_tool(name, not is_error)

    def _backed(self, category: str, sentence: str = "") -> bool:
        outcomes = self._turn.outcomes
        # A local exam-list write or a Calendar read is not a provider mutation.
        # Calendar claims require a structured verified readback receipt.
        calendar_context = self.calendar_context or bool(
            re.search(r"\bcalendar\b", _norm(sentence) + " " + _norm(self._turn.text))
        )
        if calendar_context and category in {"create", "delete", "done"}:
            # A fresh read may verify an existing state, never a first-person
            # mutation claim such as 'I added/updated it'.
            if (
                category == "create"
                and re.search(
                    r"\b(?:is|was|has been)\s+(?:now\s+)?(?:scheduled|added|booked|saved)\b",
                    sentence,
                    re.I,
                )
                and not _FIRST_PERSON.search(_norm(sentence))
                and any(
                    title and title in _norm(sentence)
                    for title in self._turn.calendar_read_titles
                )
            ):
                return True
            required = (
                "calendar.delete"
                if category == "delete"
                else "calendar.update"
                if re.search(
                    r"\b(?:updated|changed|moved|rescheduled)\b", sentence, re.I
                )
                else "calendar.create"
            )
            return any(
                outcome.verified and outcome.effect == required
                for _, outcome in outcomes
            )
        if category == "done":
            if self._turn.unverified or any(not o.verified for _, o in outcomes):
                return False
            return bool(self._turn.tools_ok) or any(o.verified for _, o in outcomes)
        for _name, outcome in outcomes:
            if not outcome.verified:
                continue
            # A saved file is not a scheduled appointment or generic update.
            if (
                outcome.effect == "file.write"
                and category == "create"
                and re.search(r"\b(saved|wrote|written)\b", _norm(sentence))
            ):
                return True
            if (
                outcome.effect == "url.navigate"
                and category == "window"
                and "backend" in sentence.lower()
                and not re.search(r"visible|desktop", sentence, re.I)
            ):
                return True
            if outcome.effect in {"setting.volume", "setting.mute"} and category in {
                "media",
                "create",
            }:
                if category == "create" and "volume" not in sentence.lower():
                    continue
                if category == "media" and not re.search(
                    r"\b(?:volume|muted|unmuted|silenced|lowered|raised)\b|turned .*(?:down|up)",
                    sentence,
                    re.I,
                ):
                    continue
                before = outcome.evidence.get("before_pct")
                actual = outcome.evidence.get("actual_pct")
                if re.search(r"lowered|turned .*down", sentence, re.I) and (
                    before is None or actual is None or actual >= before
                ):
                    continue
                if re.search(r"raised|turned .*up", sentence, re.I) and (
                    before is None or actual is None or actual <= before
                ):
                    continue
                nums = [int(n) for n in re.findall(r"\b(\d{1,3})\b", sentence)]
                actual = outcome.evidence.get("actual_pct")
                if nums and any(n != actual for n in nums):
                    continue
                if (
                    re.search(r"\bunmuted\b", sentence, re.I)
                    and outcome.evidence.get("actual_muted") is not False
                ):
                    continue
                if (
                    re.search(r"\bmuted\b", sentence, re.I)
                    and outcome.evidence.get("actual_muted") is not True
                ):
                    continue
                if outcome.effect == "setting.mute" and not re.search(
                    r"\b(?:muted|unmuted)\b", sentence, re.I
                ):
                    continue
                return True

        keys = _TOOL_KEYS.get(category, ())
        return any(any(k in n.lower() for k in keys) for n in self._turn.tools_ok)

    def check(self, assistant_text: str) -> Verdict | None:
        """A Verdict when Jarvis claimed an action nothing backs up."""
        turn = self._turn
        if not turn.text or (
            self._clock() - turn.at > USER_WINDOW_S and not self.calendar_context
        ):
            return None  # proactive speech / no request to answer
        if (
            not turn.tools_ok
            and not turn.tools_failed
            and _GOAL.search(_norm(turn.text))
            and refusal(assistant_text)
        ):
            # "I cannot access your emails" without even trying.
            return Verdict(
                category="refusal",
                claim=(assistant_text or "")[:160],
                reason="no-tool",
            )
        if not _REQUEST.search(_norm(turn.text)) and not self.calendar_context:
            return None  # chit-chat, not a command
        found = claims(assistant_text)
        if (
            any(o.effect.startswith("setting.") for _, o in turn.outcomes)
            and re.search(
                r"\bvolume\b[^.!?]*\b\d{1,3}\s*(?:percent|%)", assistant_text, re.I
            )
            and not _NEGATED.search(_norm(assistant_text))
        ):
            found.append(("media", assistant_text))
        if (
            (turn.outcomes or turn.unverified)
            and re.search(
                r"\b(?:page|tab|website|link)\b[^.!?]*\b(?:visible|loaded|displayed)\b",
                assistant_text,
                re.I,
            )
            and not _NEGATED.search(_norm(assistant_text))
        ):
            found.append(("window", assistant_text))
        if (
            self.calendar_context
            and re.search(
                r"\b(?:is|was|has been)\s+(?:now\s+)?(?:scheduled|added|booked|updated|saved)\b",
                assistant_text,
                re.I,
            )
            and not _NEGATED.search(_norm(assistant_text))
        ):
            found.append(("create", assistant_text))
        for category, sentence in found:
            if self._backed(category, sentence):
                continue
            failed = list(turn.tools_failed)
            return Verdict(
                category=category,
                claim=sentence[:160],
                reason="unverified"
                if turn.outcomes or turn.unverified
                else "tool-failed"
                if failed
                else "no-tool",
                detail=", ".join(failed[:3]),
                uncertain=bool(turn.outcomes or turn.unverified),
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
    if verdict.category == "refusal":
        return (
            f'Sir asked: "{user_text[:200]}". You said "{verdict.claim}" without '
            "calling any tool. You DO have tools for his mail, calendar, "
            "weather, news, exams, files, apps, the web and more. Pick the "
            "tool that serves his goal and call it now, then report only its "
            "real result. Only if no tool exists for it, say so in one short "
            "sentence."
        )
    if verdict.uncertain:
        return (
            "The requested result is not backed by matching read-back evidence. "
            "Do not repeat any mutation or claim success. Report only the recorded "
            "outcome; a launch request is not visibility and a click is not delivery."
        )
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
    """Last resort: never leave a false 'done' standing ('' = stay quiet)."""
    if verdict.category == "refusal":
        return ""  # an honest "can't" after the retry needs no correction
    if verdict.uncertain:
        return "Correction, Sir: that result is not verified. The command may have been dispatched."
    return (
        "Correction, Sir: I said that was done, but it wasn't. "
        "Nothing actually happened. Please ask me again."
    )


def calendar_safe_text(text: str) -> str:
    """Calendar mutation claims need proof even if final input was lost.

    This boundary does not rely on a timely user transcript: native audio can
    precede transcription, and speculative speech can predate request context.
    """
    if not enabled():
        return text
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        normalized = _norm(sentence)
        domain = GUARD.calendar_context or bool(
            re.search(r"\b(?:calendar|event|appointment)\b", normalized)
        )
        if not domain or _NEGATED.search(normalized) or "?" in sentence:
            continue
        categories = [category for category, _ in claims(sentence)]
        if re.search(
            r"\b(?:scheduled|booked|added|saved|created|updated|changed)\b", normalized
        ):
            categories.append("create")
        if re.search(r"\b(?:deleted|removed|cancelled|canceled)\b", normalized):
            categories.append("delete")
        for category in categories:
            if category in {"create", "delete", "done"} and not GUARD._backed(
                category, sentence
            ):
                return "I haven't verified that Google Calendar change yet, Sir."
    return text


_CALENDAR_DOMAIN = re.compile(r"\b(?:calendar|event|events|appointment|appointments)\b")


def calendar_risk(text: str) -> bool:
    """True when a reply in progress could carry a calendar claim.

    Streaming speech checks this on the partial transcript. A True result
    holds the rest of the reply until the full text passes
    calendar_safe_text. Pure apart from the module GUARD state.
    """
    if not enabled():
        return False
    if GUARD.calendar_context:
        return True
    normalized = _norm(text or "")
    if _CALENDAR_DOMAIN.search(normalized):
        return True
    return calendar_safe_text(text) != text
