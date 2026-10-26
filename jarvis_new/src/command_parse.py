"""Parse spoken/typed character-switch commands. Pure functions, no I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

MAX_WORDS = 9

_FILLERS = (
    "can you",
    "could you",
    "would you",
    "please",
    "hey",
    "okay",
    "ok",
    "lets",
    "now",
    "just",
)
_TRAILING = frozenset(("please", "thanks", "now", "sir", "boss", "mate"))
_BLOCKED_FIRST = frozenset(
    (
        "tell",
        "what",
        "who",
        "why",
        "how",
        "explain",
        "describe",
        "about",
        "is",
        "are",
        "does",
        "do",
        "did",
        "when",
        "where",
    )
)
_SWITCH_VERBS = ("switch", "change", "swap")
_MOVE_VERBS = ("go", "take")
_NOT_NAMES = frozenset(
    (
        "the",
        "a",
        "an",
        "my",
        "some",
        "this",
        "that",
        "these",
        "those",
        "it",
        "next",
        "previous",
        "last",
        "another",
        "other",
        "back",
        "off",
        "on",
        "up",
        "down",
        "mute",
        "silent",
        "sleep",
        "bed",
    )
)


@dataclass(frozen=True)
class SwitchCmd:
    kind: Literal["switch", "unknown"]
    target: str | None
    raw_name: str | None


def _dist(a: str, b: str) -> int:
    """Optimal string alignment distance (Levenshtein + adjacent transposition)."""
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


def _normalize(text: str) -> str:
    t = text.lower().replace("'", "").replace(chr(0x2019), "")
    t = re.sub(r"[^\w\s/]", " ", t)
    t = re.sub(r"(?<!^)/", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _is_verb(word: str, verbs: tuple[str, ...]) -> bool:
    for v in verbs:
        if word == v or (len(v) >= 4 and len(word) >= 3 and _dist(word, v) <= 1):
            return True
    return False


def _resolve(phrase: str, names: dict[str, tuple[str, ...]]) -> str | None:
    """Map a name phrase to a character id (exact, else unique fuzzy best)."""
    if not phrase:
        return None
    best: tuple[int, str] | None = None
    tied = False
    for cid, aliases in names.items():
        for alias in aliases:
            a = alias.lower().strip()
            if not a:
                continue
            if a == phrase:
                return cid
            limit = 1 if len(a) <= 5 else 2
            if len(phrase) < 3:
                continue
            dd = _dist(a, phrase)
            if dd <= limit:
                if best is None or dd < best[0]:
                    best, tied = (dd, cid), False
                elif dd == best[0] and cid != best[1]:
                    tied = True
    if best is None or tied:
        return None
    return best[1]


def _strip_fillers(tokens: list[str]) -> list[str]:
    changed = True
    while changed and tokens:
        changed = False
        for f in _FILLERS:
            parts = f.split()
            if tokens[: len(parts)] == parts and len(tokens) > len(parts):
                tokens = tokens[len(parts) :]
                changed = True
                break
    return tokens


def _parse_tokens(
    tokens: list[str], names: dict[str, tuple[str, ...]]
) -> SwitchCmd | None:
    if not tokens:
        return None
    n = len(tokens)
    w0 = tokens[0]

    # let NAME take over
    if (
        w0 == "let"
        and n >= 3
        and _is_verb(tokens[-2], ("take",))
        and tokens[-1] == "over"
    ):
        cid = _resolve(" ".join(tokens[1:-2]), names)
        return SwitchCmd("switch", cid, None) if cid else None
    # put NAME on
    if w0 == "put" and n >= 3 and tokens[-1] == "on":
        cid = _resolve(" ".join(tokens[1:-1]), names)
        return SwitchCmd("switch", cid, None) if cid else None
    # bring back NAME
    if _is_verb(w0, ("bring",)) and n >= 3 and tokens[1] == "back":
        cid = _resolve(" ".join(tokens[2:]), names)
        return SwitchCmd("switch", cid, None) if cid else None
    # NAME take over
    if n >= 3 and tokens[-1] == "over" and _is_verb(tokens[-2], ("take",)):
        cid = _resolve(" ".join(tokens[:-2]), names)
        if cid:
            return SwitchCmd("switch", cid, None)

    is_switch = _is_verb(w0, _SWITCH_VERBS)
    is_move = _is_verb(w0, _MOVE_VERBS)
    if not (is_switch or is_move):
        return None
    rest = tokens[1:]
    if rest and rest[0] in ("me", "us"):
        rest = rest[1:]
    if rest and rest[0] == "over":
        rest = rest[1:]
    if rest and rest[0] == "back":
        rest = rest[1:]
    has_to = bool(rest) and rest[0] == "to"
    if has_to:
        rest = rest[1:]
    if not rest:
        return None
    if is_move and not has_to:
        return None
    phrase = " ".join(rest)
    cid = _resolve(phrase, names)
    if cid:
        return SwitchCmd("switch", cid, None)
    if is_switch and has_to and len(rest) <= 2 and not (set(rest) & _NOT_NAMES):
        return SwitchCmd("unknown", None, phrase)
    return None


def parse_switch(text: str, names: dict[str, tuple[str, ...]]) -> SwitchCmd | None:
    """Return a SwitchCmd if text is clearly a character-switch command, else None."""
    if not text or "?" in text:
        return None
    norm = _normalize(text)
    if not norm:
        return None
    if norm.startswith("/"):
        cid = _resolve(norm[1:].strip(), dict(names)) if " " not in norm else None
        if cid is None:
            return None
        # slash form requires an exact alias
        alias = norm[1:]
        if any(alias == a.lower() for a in names.get(cid, ())):
            return SwitchCmd("switch", cid, None)
        return None
    tokens = norm.split()
    if len(tokens) > MAX_WORDS:
        return None
    tokens = _strip_fillers(tokens)
    while len(tokens) > 2 and tokens[-1] in _TRAILING:
        tokens = tokens[:-1]
    if not tokens or tokens[0] in _BLOCKED_FIRST:
        return None
    res = _parse_tokens(tokens, names)
    if res is not None:
        return res
    # vocative: leading character name addressed before the command
    if len(tokens) > 2 and _resolve(tokens[0], names):
        rest = _strip_fillers(tokens[1:])
        if rest and rest[0] not in _BLOCKED_FIRST:
            return _parse_tokens(rest, names)
    return None
