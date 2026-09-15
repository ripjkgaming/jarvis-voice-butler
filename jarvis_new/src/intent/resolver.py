"""Intent-driven disambiguation: slang/fragment/sarcasm → concrete params.

Three layers, cheapest first (no inference burn unless needed):
1. Exact alias hit (user-teachable, persisted in shortcuts.json) -> 1.0.
2. Fuzzy match over aliases + tool catalog (difflib, local) -> 0.5-0.9.
3. Otherwise low confidence → single clarifying question (LLM fallback
   drafts it at call time; this module only decides the tier).

Router rule: >=0.8 act silently; 0.5-0.8 act + one-line confirm; <0.5 ask.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

ACT_THRESHOLD = 0.8
CONFIRM_THRESHOLD = 0.5

SEED_ALIASES: dict[str, dict] = {
    "hot rod red": {"action": "set_color", "params": {"color": "crimson"}},
    "punch it": {"action": "max_throttle", "params": {}},
    "take the wheel": {"action": "autopilot", "params": {"mode": "on"}},
    "go dark": {"action": "stealth_mode", "params": {"mode": "on"}},
    "brief me": {"action": "morning_briefing", "params": {}},
}

_TOOL_CATALOG = [
    "set_color",
    "max_throttle",
    "autopilot",
    "stealth_mode",
    "morning_briefing",
    "open_url",
    "take_screenshot",
    "set_volume",
]


@dataclass
class IntentResult:
    action: str
    params: dict = field(default_factory=dict)
    confidence: float = 0.0
    clarification: str | None = None

    @property
    def should_act(self) -> bool:
        return self.confidence >= ACT_THRESHOLD

    @property
    def should_confirm(self) -> bool:
        return CONFIRM_THRESHOLD <= self.confidence < ACT_THRESHOLD


def normalize(text: str) -> str:
    """Lowercase, strip punctuation/wakewords. Pure."""
    text = (text or "").lower()
    text = re.sub(r"\b(jarvis|hey|please|uh|um)\b", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def resolve_intent(text: str, aliases: dict[str, dict] | None = None) -> IntentResult:
    """Resolve informal phrasing to action+params with confidence. Pure."""
    table = {normalize(k): v for k, v in {**SEED_ALIASES, **(aliases or {})}.items()}
    clean = normalize(text)
    if not clean:
        return IntentResult(
            action="unknown",
            clarification="I didn't quite catch that, Sir. Once more?",
        )
    if clean in table:
        hit = table[clean]
        return IntentResult(
            action=hit["action"], params=dict(hit.get("params", {})), confidence=1.0
        )
    for alias, hit in table.items():
        if alias and re.search(rf"\b{re.escape(alias)}\b", clean):
            return IntentResult(
                action=hit["action"],
                params=dict(hit.get("params", {})),
                confidence=1.0,
            )
    candidates = list(table) + _TOOL_CATALOG
    best = difflib.get_close_matches(clean, candidates, n=1, cutoff=0.55)
    if best:
        match = best[0]
        if match in table:
            hit = table[match]
            score = difflib.SequenceMatcher(None, clean, match).ratio()
            return IntentResult(
                action=hit["action"],
                params=dict(hit.get("params", {})),
                confidence=round(min(0.9, max(0.5, score)), 3),
                clarification=f"Taking that as '{match}', Sir — correct?",
            )
        score = difflib.SequenceMatcher(None, clean, match).ratio()
        return IntentResult(
            action=match,
            confidence=round(min(0.75, max(0.5, score)), 3),
            clarification=f"Taking that as '{match}', Sir — correct?",
        )
    return IntentResult(
        action="unknown",
        clarification=(
            "Forgive me, Sir — that one slipped past the valet. "
            "Might you phrase it once more?"
        ),
    )
