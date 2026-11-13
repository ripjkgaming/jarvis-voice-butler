"""Call policy (W3): system prompt builder and outbound guard."""

from __future__ import annotations

import re
from dataclasses import dataclass

DISCLOSURE = "Hi, this is Jarvis, an AI assistant answering on behalf of the owner."
RECORDING_NOTICE = "Just so you know, this call is being recorded and transcribed."

# (category, pattern) applied to text Jarvis is about to SAY.
_SENSITIVE = [
    (
        "sensitive data",
        re.compile(
            r"\b(password|passcode|pin\b|otp|one[- ]time (code|password)|cvv|"
            r"card number|credit card|bank account|account number|sort code|iban|"
            r"routing number|social security|ssn|passport (number|no)|"
            r"home address|api key|secret key|security code)\b",
            re.I,
        ),
    ),
    (
        "payment",
        re.compile(
            r"\b(i will pay|i'll pay|will transfer|i'll transfer|send (you )?(the )?money|"
            r"wire (you|the)|payment (is|has been) (made|sent)|i('ll| will) send \$?\d+|"
            r"[$£€]\s?\d+)",
            re.I,
        ),
    ),
    (
        "commitment",
        re.compile(
            r"\b(i (promise|guarantee|agree|accept|confirm)|we have a deal|"
            r"i('ll| will) (sign|book|buy|purchase|order|attend|be there|meet you)|"
            r"(he|she|they|the owner) (will|can) (definitely )?(attend|come|sign|pay)|"
            r"consider it (done|confirmed))\b",
            re.I,
        ),
    ),
]

# (category, pattern) on what the CALLER asks, to pre-empt risky replies.
_CALLER_ASKS = [
    (
        "prompt injection",
        re.compile(
            r"\b(ignore|disregard|forget|override) (all |any |your |the |previous |prior |above )*"
            r"(instructions?|rules?|prompt|guidelines|restrictions)|"
            r"(system|developer) (prompt|message)|you are now|pretend (to be|you)|"
            r"jailbreak|act as (if|an?|the)|reveal (your|the) (prompt|instructions)",
            re.I,
        ),
    ),
    (
        "caller asked for sensitive data",
        re.compile(
            r"\b(password|passcode|pin number|pin code|otp|one[- ]time (code|password)|"
            r"verification code|security code|cvv|login details|credentials|"
            r"card number|bank details|account number|"
            r"home address|where (does|do) (he|she|they) live)\b",
            re.I,
        ),
    ),
    (
        "caller asked for payment",
        re.compile(
            r"\b(send (me )?(the )?money|pay (me|us|the invoice)|transfer|invoice|"
            r"gift cards?)\b",
            re.I,
        ),
    ),
    (
        "caller asked for a commitment",
        re.compile(
            r"\b(sign|agree to|commit|confirm (the )?(booking|meeting|order)|"
            r"can (he|she|you) (promise|guarantee))\b",
            re.I,
        ),
    ),
]

SAFE_FALLBACK = (
    "I can't help with that on my own. I'll pass it to the owner, "
    "who will get back to you."
)


@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""
    replacement: str = SAFE_FALLBACK


def check_outgoing(text: str) -> GuardResult:
    """Block a reply that shares sensitive data, pays, or commits the owner."""
    for cat, pat in _SENSITIVE:
        m = pat.search(text or "")
        if m:
            return GuardResult(False, f"{cat}: reply contained {m.group(0)!r}")
    return GuardResult(True)


INJECTION_REPLY = "I can't do that, but I can take a message."


def check_incoming(text: str) -> GuardResult:
    """Flag a caller request that needs the owner before any reply."""
    for cat, pat in _CALLER_ASKS:
        m = pat.search(text or "")
        if m:
            if cat == "prompt injection":
                return GuardResult(False, f"{cat}: {m.group(0)!r}", INJECTION_REPLY)
            return GuardResult(False, f"{cat}: {m.group(0)!r}")
    return GuardResult(True)


def build_system_prompt(
    *,
    direction: str,
    contact=None,
    goal: str = "",
    recall: str = "",
    persona: str = "jarvis",
    owner_name: str = "the owner",
    record: bool = False,
    disclose_ai: bool = True,
    rule_message: str = "",
    max_minutes: float = 10,
) -> str:
    who = "an unknown caller"
    if contact is not None:
        who = (
            f"{contact.name} ({contact.relationship or 'contact'}, "
            f"trust={contact.trust})"
        )
    lines = [
        f"You are a phone assistant on a live voice call for {owner_name}. "
        "Speak in short natural sentences, one or two at a time. No lists, "
        "no markdown.",
    ]
    if persona == "owner":
        lines.append(
            f"Speak in the first person as {owner_name}'s voice agent, "
            "but never claim to be human."
        )
    if disclose_ai:
        lines.append(
            "You have disclosed that you are an AI. If asked, confirm it. "
            "Never deny being an AI."
        )
    if direction == "in":
        lines.append(
            f"Incoming call from {who}. Find out who they are and why "
            "they call, answer simple questions, and take a message. "
            f"If they want {owner_name}, say you will pass the request on."
        )
    else:
        lines.append(
            f"You placed this call to {who}. Goal: {goal or 'unspecified'}. "
            "Stay on the goal, handle replies politely, then end the call."
        )
    if rule_message:
        lines.append(f"Owner instruction for this period: {rule_message}")
    if recall:
        lines.append("Known context (do not read aloud verbatim):\n" + recall)
    if record:
        lines.append("The call is recorded and the caller was told so.")
    lines.append(
        "HARD RULES: never share passwords, codes, card or bank details, "
        "addresses, or other private data. Never make payments or accept "
        "payment requests. Never commit the owner to anything (meetings, "
        "purchases, agreements). For any of these say you will ask the owner "
        "and mark it ESCALATE. If the caller asks for the owner or a human, "
        "reply with the single token HANDOVER. When the conversation is done, "
        f"say goodbye and append the token END. Calls last at most {max_minutes:g} "
        "minutes."
    )
    return "\n".join(lines)
