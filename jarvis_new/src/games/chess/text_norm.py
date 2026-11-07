"""Shared utterance normalisation (Appendix A.1 wrappers, wake phrase)."""

from __future__ import annotations

import re

_PREFIX_PHRASES = [
    r"would you be so kind as to",
    r"be so kind as to",
    r"would you mind",
    r"could you please",
    r"could you",
    r"can you",
    r"would you",
    r"will you",
    r"i'?d like to",
    r"i would like to",
    r"i want to",
    r"i'?ll",
    r"i will",
    r"let'?s",
    r"let me",
    r"i'?m going to",
    r"i'?m gonna",
    r"kindly",
]
_PREFIX_WORDS = {
    "chess",
    "please",
    "now",
    "okay",
    "ok",
    "alright",
    "so",
    "um",
    "uh",
    "er",
    "erm",
    "hmm",
    "well",
    "then",
    "next",
    "and",
    "right",
    "go",
    "yes",
    "yeah",
}
_SUFFIX_PHRASES = [
    r"if you would",
    r"thank you",
    r"for me",
    r"go on",
    r"thanks",
    r"please",
    r"cheers",
    r"quickly",
    r"jarvis",
    r"now",
    r"then",
    r"sir",
]

_WAKE_RE = re.compile(r"^(?:(?:hey|hi|ok|okay|a|ay|hay)\s+)?(?:jarvis|jarvas|jervis)\b")


def clean(text: str) -> str:
    """Lowercase, drop punctuation (keeping ' - and =), collapse spaces."""
    t = text.lower().replace("’", "'").replace("‘", "'")  # noqa: RUF001
    t = t.replace("=", " equals ")
    t = re.sub(r"[^a-z0-9' \-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _other_wake_re() -> re.Pattern[str] | None:
    """Wake names of every loaded non-Jarvis character (None if unavailable)."""
    try:
        try:
            from character_runtime import get_runtime
        except ImportError:
            from src.character_runtime import get_runtime
        names: set[str] = set()
        for cid, ch in get_runtime().chars.items():
            if cid != "jarvis":
                names.update(
                    re.escape(clean(n))
                    for n in (*ch.wake_names, ch.spoken_name)
                    if clean(n)
                )
        if not names:
            return None
        alt = "|".join(sorted(names))
        return re.compile(rf"^(?:(?:hey|hi|ok|okay)\s+)?(?:{alt})\b")
    except Exception:
        return None


def is_wake_phrase(text: str) -> bool:
    """True when the utterance starts with any character's wake words."""
    if is_jarvis_wake(text):
        return True
    t = clean(text)
    rx = _other_wake_re() if t else None
    return bool(rx and rx.match(t))


def is_jarvis_wake(text: str) -> bool:
    """True when the utterance starts with the main assistant's wake words."""
    t = clean(text)
    if not t:
        return False
    if t.startswith(("hey jarvis", "ok jarvis", "okay jarvis", "hi jarvis")):
        return True
    return bool(re.match(r"^jarvis\b", t))


def _strip_step(t: str) -> str | None:
    """Remove one A.1 wrapper from the front or back; None if nothing to strip."""
    for ph in _PREFIX_PHRASES:
        m = re.match(rf"^{ph}\b\s*", t)
        if m and m.end() < len(t):
            return t[m.end() :]
    words = t.split(" ")
    if (
        len(words) > 1
        and words[0] in _PREFIX_WORDS
        and not (words[0] == "go" and words[1] in {"to", "back", "on"})
        and not (
            words[0] == "right"
            and words[1] in {"knight", "rook", "bishop", "one", "side"}
        )
    ):
        return " ".join(words[1:])
    for ph in _SUFFIX_PHRASES:
        m = re.search(rf"\s+{ph}$", t)
        if m and len(t) > len(ph):
            return t[: m.start()]
    return None


def variants(text: str) -> list[str]:
    """The cleaned utterance, then each successively wrapper-stripped form.

    Politeness never changes meaning, but some wrapper words are also part of
    real phrases ("play for me", "not now"), so matchers try every state.
    """
    t = clean(text)
    out = [t] if t else []
    while t:
        nxt = _strip_step(t)
        if nxt is None or not nxt.strip():
            break
        t = nxt.strip()
        out.append(t)
    return out


def strip_wrappers(text: str) -> str:
    """Remove A.1 prefixes and suffixes; politeness never changes meaning."""
    v = variants(text)
    return v[-1] if v else ""
