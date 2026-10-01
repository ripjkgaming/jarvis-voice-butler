"""Intent-driven disambiguation: slang/fragment/sarcasm → concrete params.

Five layers, cheapest first (no inference burn unless needed):
1. Exact alias hit (user-teachable, persisted in shortcuts.json) -> 1.0.
2. Keyword routing over content words (local, deterministic): common
   voice commands map to real Jarvis actions even when difflib sees
   only character noise. One keyword -> ~0.6 (confirm), more -> up to
   0.85. This layer exists because pure edit distance misroutes across
   domains ("check my gmail inbox" -> dns_recon was the incident).
3. Fuzzy match over aliases + tool catalog (difflib, local) -> 0.5-0.9.
4. Needle 2 semantic routing (optional 14MB local tool-calling model,
   ``uv sync --extra needle`` + JARVIS_NEEDLE=1): paraphrases neither
   layer catches, confidence-gated into the same tiers. Abstains
   (None) on anything unsure — never misfires.
5. Otherwise low confidence → single clarifying question (LLM fallback
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
    "morning briefing": {"action": "morning_briefing", "params": {}},
    # Recipe entry phrases (skills/morning_routine.md): the skill's step 1
    # IS morning_briefing, so exact seeds are behavior-preserving.
    "morning routine": {"action": "morning_briefing", "params": {}},
    "start my morning": {"action": "morning_briefing", "params": {}},
    "good morning": {"action": "morning_briefing", "params": {}},
    "kick off the day": {"action": "morning_briefing", "params": {}},
    "daily startup": {"action": "morning_briefing", "params": {}},
    "begin my morning": {"action": "morning_briefing", "params": {}},
    "morning bundle": {"action": "morning_briefing", "params": {}},
    "start the day": {"action": "morning_briefing", "params": {}},
    # Helper-proposed paraphrases (reviewed 2026-09-18): exact seeds that
    # fix confirm-tier misroutes (what-is-on-today->clock,
    # catch-me-up->volume) and promote weak confirms to 1.0.
    "brief me this morning": {"action": "morning_briefing", "params": {}},
    "what is on today": {"action": "morning_briefing", "params": {}},
    "give me the morning rundown": {"action": "morning_briefing", "params": {}},
    "start of day summary": {"action": "morning_briefing", "params": {}},
    "morning status": {"action": "morning_briefing", "params": {}},
    "catch me up on today": {"action": "morning_briefing", "params": {}},
    "scan my box": {"action": "nmap_scan", "params": {}},
    # NOTE (Phase 5): focus/meeting recipe triggers are DELIBERATELY not
    # seeded — no single tool expresses their multi-step contracts (focus
    # restore, meeting notes file), and seeding to a half-action (e.g.
    # focus->mute) would be wrong behavior. They abstain to unknown so the
    # agent LLM performs skills/*.md. Known confirm-tier misroutes filed in
    # comms (heads-down-time->volume, time-to-focus->clock, prep-my-meeting
    # + get-ready-for-my-call->inbox, next-event->clock): fixing needs
    # catalog/keyword changes outside SEED lists — proposed, not taken.
    "recon my box": {"action": "dns_recon", "params": {}},
    "check my exposure": {"action": "pentest_report", "params": {}},
    "freeze testing": {"action": "freeze_testing", "params": {}},
    # Canonical voice phrasings: unambiguous, safe at 1.0.
    "what time is it": {"action": "tell_time", "params": {}},
    "what is the time": {"action": "tell_time", "params": {}},
    "tell me the time": {"action": "tell_time", "params": {}},
    "crank it up": {"action": "set_volume", "params": {"action": "up"}},
    "turn it up": {"action": "set_volume", "params": {"action": "up"}},
    "turn it down": {"action": "set_volume", "params": {"action": "down"}},
    "mute": {"action": "set_volume", "params": {"action": "mute"}},
    "unmute": {"action": "set_volume", "params": {"action": "unmute"}},
    "check my gmail": {"action": "gmail_inbox", "params": {}},
    "check my email": {"action": "gmail_inbox", "params": {}},
    "check my inbox": {"action": "gmail_inbox", "params": {}},
    "read my email": {"action": "gmail_inbox", "params": {}},
    "read my whatsapp": {"action": "whatsapp_read", "params": {}},
}

_TOOL_CATALOG = [
    "morning_briefing",
    "open_url",
    "take_screenshot",
    "set_volume",
    "nmap_scan",
    "dns_recon",
    "pentest_report",
    "freeze_testing",
    # Real Jarvis actions, so difflib can land on the right domain.
    "tell_time",
    "do_math",
    "media_control",
    "open_app",
    "gmail_inbox",
    "weather_now",
    "daily_briefing",
    "whatsapp_chats",
    "whatsapp_read",
    "whatsapp_draft",
    "manage_todo",
    "search_the_web",
    "transfer_to_system_control",
    "transfer_to_deep_research",
]

_APP_NAMES = frozenset(
    {
        "files",
        "terminal",
        "calculator",
        "browser",
        "whatsie",
        "code",
        "settings",
        "discord",
        "spotify",
    }
)

_SITE_NAMES = frozenset(
    {
        "google",
        "youtube",
        "amazon",
        "wikipedia",
        "github",
        "netflix",
        "spotify",
        "bbc",
        "reddit",
        "twitch",
        "ebay",
        # Mirror of tools.py KNOWN_SITES keys (normalized single tokens)
        # so "open <site>" routes without asking "which URL?".
        "chatgpt",
        "weather",
        "office",
        "stackoverflow",
        "linkedin",
        "outlook",
        "drive",
        "maps",
    }
)

# Verbs that open an app/site: voice says launch/start as often as open.
# "take" covers "take me to <site>" (screenshot has no app slot, so
# "take screenshot" never misfires here).
_OPEN_VERBS = frozenset({"open", "launch", "start", "take"})

# Destructive verbs: combined with another action verb in one breath
# ("open youtube and shut down the laptop") the request is multi-intent
# with irreversible parts — abstain so the agent asks, never half-acts.
_DESTRUCTIVE = frozenset({"shut down", "shutdown", "delete", "power off", "format"})

# Number words: quantities signal arithmetic ("fifteen times eleven").
_NUMBER_WORDS = frozenset(
    {
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
        "twenty",
        "thirty",
        "forty",
        "fifty",
        "sixty",
        "seventy",
        "eighty",
        "ninety",
        "hundred",
        "thousand",
    }
)

_TLDS = (
    "com",
    "org",
    "net",
    "io",
    "edu",
    "gov",
    "sg",
    "uk",
    "de",
    "co",
    "ai",
    "app",
    "dev",
)

_VOLUME_UP = frozenset({"up", "louder", "loud", "raise", "higher", "increase"})
_VOLUME_DOWN = frozenset({"down", "quieter", "quiet", "lower", "reduce", "decrease"})

# Content-word routes: (keywords, action). Slot words (app/site names,
# volume directions) are detected separately and count as extra hits.
_KEYWORD_ROUTES: tuple[tuple[frozenset[str], str], ...] = (
    (
        frozenset({"gmail", "inbox", "email", "teacher", "mail", "sent"}),
        "gmail_inbox",
    ),
    (frozenset({"time", "clock", "hour"}), "tell_time"),
    # Search rides above volume: direction words ("look UP", "scroll
    # down") otherwise steal search verbs at equal confidence. Volume's
    # exact seeds still win outright at 1.0.
    (frozenset({"search", "google", "lookup", "look"}), "search_the_web"),
    (
        frozenset(
            {
                "percent",
                "times",
                "plus",
                "minus",
                "divided",
                "multiply",
                "calculate",
                "calculator",
            }
        ),
        "do_math",
    ),
    (frozenset({"music", "pause", "resume"}), "media_control"),
    # Read vs write split: "read the chat" looks things up, "message
    # mum" composes. Without the split, every whatsapp noun misroutes.
    (
        frozenset({"read", "show", "list", "recent", "latest", "newest", "unread"}),
        "whatsapp_read",
    ),
    # Bare "send" excluded from this set: "send a birthday card" is
    # physical mail, not a chat draft. ("send a whatsapp" still fires
    # via "whatsapp".)
    (
        frozenset({"message", "draft", "whatsapp", "reply", "texted"}),
        "whatsapp_draft",
    ),
    (
        frozenset(
            {
                "volume",
                "louder",
                "quieter",
                "mute",
                "muted",
                "unmute",
                "sound",
                "loud",
                "quiet",
                "up",
                "down",
            }
        ),
        "set_volume",
    ),
    (
        frozenset(
            {
                "weather",
                "rain",
                "raining",
                "forecast",
                "sunny",
                "cloudy",
                "snow",
                "storm",
                "temperature",
                "humidity",
            }
        ),
        "weather_now",
    ),
    (frozenset({"briefing", "brief"}), "daily_briefing"),
    (frozenset({"whatsapp"}), "whatsapp_read"),
    (frozenset({"todo", "todos", "remind", "reminder"}), "manage_todo"),
    (
        frozenset({"screenshot", "picture", "capture"}),
        "take_screenshot",
    ),
)


def _route_params(action: str, words: set[str], clean: str) -> dict:
    """Extract slot params for a keyword-routed action. Pure."""
    if action == "set_volume":
        if words & _VOLUME_UP:
            return {"action": "up"}
        if words & _VOLUME_DOWN:
            return {"action": "down"}
        if "unmute" in words:
            return {"action": "unmute"}
        if words & {"mute", "muted", "silence"}:
            return {"action": "mute"}
        return {}
    if action == "transfer_to_system_control":
        for name in sorted(words & _APP_NAMES):
            return {"task": f"open {name}", "app": name}
        return {"task": clean}
    if action == "open_url":
        for name in sorted(words & _SITE_NAMES):
            return {"url": name}
        return {"url": clean}
    if action == "search_the_web":
        return {"query": clean}
    if action == "manage_todo":
        for verb in ("add", "list", "done", "clear"):
            if verb in words:
                return {"action": verb}
        return {}
    return {}


def keyword_route(clean: str) -> IntentResult | None:
    """Content-word routing with slot extraction. Pure.

    One keyword hit -> 0.6 (confirm tier), each extra hit or slot word
    +0.08, capped at 0.85: strong enough to outrank cross-domain
    difflib noise, never as sure as an exact alias.
    """
    words = set(clean.split())
    if not words:
        return None
    best: IntentResult | None = None
    query_len = len(words)
    for keywords, action in _KEYWORD_ROUTES:
        # Generic read-verbs only count inside a WhatsApp query, else
        # "list my todos" / "read the news" misroute to chat.
        if action == "whatsapp_read" and "whatsapp" not in words:
            continue
        hits = words & keywords
        if not hits:
            continue
        extra = 0
        if action == "set_volume":
            # A bare direction ("look UP", "scroll DOWN") is not a volume
            # command: the direction bonus needs a volume-context word or
            # a short imperative ("turn it up"). Exact seeds bypass this.
            direction = (words & _VOLUME_UP) or (words & _VOLUME_DOWN)
            context = words & {
                "volume",
                "louder",
                "quieter",
                "sound",
                "music",
                "speaker",
                "speakers",
                "mute",
                "muted",
                "unmute",
            }
            if (
                (direction and context)
                or (direction and query_len <= 4)
                or "mute" in words
            ):
                extra += 1
            # Explicit device + percent is volume, not arithmetic ("set
            # volume to thirty percent"). Scoped to percent so plain
            # "turn the volume up" keeps its confirm-tier score.
            if "volume" in words and "percent" in words:
                extra += 2
        if action == "do_math":
            # Quantities corroborate arithmetic ("fifteen times eleven").
            extra += len(words & _NUMBER_WORDS)
        score = round(min(0.85, 0.6 + 0.08 * (len(hits) - 1 + extra)), 3)
        if best is None or score > best.confidence:
            best = IntentResult(
                action=action,
                params=_route_params(action, words, clean),
                confidence=score,
                clarification=f"Taking that as '{action}', Sir — correct?",
            )
    # App opens and known sites carry their slot word as the signal.
    # Any open verb (open/launch/start) counts: "launch files",
    # "start whatsie" are the same handoff as "open files".
    if words & _OPEN_VERBS:
        slot = words & _APP_NAMES
        if slot:
            score = round(min(0.85, 0.6 + 0.08 * len(slot)), 3)
            candidate = IntentResult(
                action="transfer_to_system_control",
                params=_route_params("transfer_to_system_control", words, clean),
                confidence=score,
                clarification="Taking that as opening an app, Sir — correct?",
            )
            if best is None or score > best.confidence:
                best = candidate
        site = words & _SITE_NAMES
        if site:
            score = round(min(0.85, 0.6 + 0.08 * len(site)), 3)
            candidate = IntentResult(
                action="open_url",
                params=_route_params("open_url", words, clean),
                confidence=score,
                clarification="Taking that as opening a website, Sir — correct?",
            )
            if best is None or score > best.confidence:
                best = candidate
    return best


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
    text = re.sub(r"\b(jarvis|jeeves|jarves|jervis|hey|please|uh|um)\b", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _spoken_domain(text: str) -> str | None:
    """'open example dot com' -> 'example.com'. Pure, STT-shaped domains."""
    spoken = re.sub(r"\s+dot\s+", ".", (text or "").lower()).strip()
    match = re.search(r"\b([a-z0-9][a-z0-9-]*\.(" + "|".join(_TLDS) + r"))\b", spoken)
    return match.group(1) if match else None


_SEED_TABLE: dict[str, dict] | None = None


def _alias_table(aliases: dict[str, dict] | None) -> dict[str, dict]:
    """Normalized alias table; the seed-only table is built once, not per turn."""
    global _SEED_TABLE
    if aliases:
        return {normalize(k): v for k, v in {**SEED_ALIASES, **aliases}.items()}
    if _SEED_TABLE is None:
        _SEED_TABLE = {normalize(k): v for k, v in SEED_ALIASES.items()}
    return _SEED_TABLE


def resolve_intent(text: str, aliases: dict[str, dict] | None = None) -> IntentResult:
    """Resolve informal phrasing to action+params with confidence. Pure."""
    table = _alias_table(aliases)
    clean = normalize(text)
    if not clean:
        return IntentResult(
            action="unknown",
            clarification="I didn't quite catch that, Sir. Once more?",
        )
    # Spoken-dot domains route before anything fuzzy ("open example dot
    # com" must never become a search or a shrug).
    domain = _spoken_domain(text)
    if domain and any(
        verb in clean.split() for verb in ("open", "launch", "start", "take", "go")
    ):
        return IntentResult(action="open_url", params={"url": domain}, confidence=0.85)
    # Destructive + another action verb in one breath is multi-intent with
    # irreversible parts: abstain so the agent asks, never half-acts.
    if any(d in clean for d in _DESTRUCTIVE) and any(
        verb in clean.split()
        for verb in ("open", "launch", "start", "send", "turn", "play")
    ):
        return IntentResult(
            action="unknown",
            clarification="That sounds like two jobs, one of them destructive, Sir — which first?",
        )
    if clean in table:
        hit = table[clean]
        return IntentResult(
            action=hit["action"], params=dict(hit.get("params", {})), confidence=1.0
        )
    # Substring alias at 1.0 only when the alias covers a substantial
    # fraction of the query (alias words / query words >= 0.5) or the
    # query is short (<= 6 words). Without this, one alias word (e.g.
    # "mute") inside a long unrelated sentence auto-fires at 1.0.
    query_words = len(clean.split())
    for alias, hit in table.items():
        if alias and re.search(rf"\b{re.escape(alias)}\b", clean):
            if query_words > 6 and len(alias.split()) / query_words < 0.5:
                continue
            return IntentResult(
                action=hit["action"],
                params=dict(hit.get("params", {})),
                confidence=1.0,
            )
    candidates = list(table) + _TOOL_CATALOG
    best = difflib.get_close_matches(clean, candidates, n=1, cutoff=0.55)
    fuzzy: IntentResult | None = None
    if best:
        match = best[0]
        if match in table:
            hit = table[match]
            score = difflib.SequenceMatcher(None, clean, match).ratio()
            fuzzy = IntentResult(
                action=hit["action"],
                params=dict(hit.get("params", {})),
                confidence=round(min(0.9, max(0.5, score)), 3),
                clarification=f"Taking that as '{match}', Sir — correct?",
            )
        else:
            score = difflib.SequenceMatcher(None, clean, match).ratio()
            fuzzy = IntentResult(
                action=match,
                confidence=round(min(0.75, max(0.5, score)), 3),
                clarification=f"Taking that as '{match}', Sir — correct?",
            )
    # Keyword layer beats cross-domain edit-distance noise ("check my
    # gmail inbox" shares characters with "recon my box" but no content
    # words). Best of keyword/fuzzy at act threshold returns now.
    keyword = keyword_route(clean)
    for result in (keyword, fuzzy):
        if result is not None and result.confidence >= ACT_THRESHOLD:
            if keyword is not None and fuzzy is not None:
                return max((keyword, fuzzy), key=lambda r: r.confidence)
            return result
    # Semantic layer: Needle 2 paraphrase routing for what both missed.
    # Best of local wins; anything under CONFIRM_THRESHOLD asks once.
    contender = None
    for result in (keyword, fuzzy):
        if result is not None and (
            contender is None or result.confidence > contender.confidence
        ):
            contender = result
    try:
        from intent.needle_router import route_with_needle

        hit = route_with_needle(clean)
    except Exception:
        hit = None
    if hit is not None:
        action = str(hit.get("action") or "unknown")
        params = hit.get("params") if isinstance(hit.get("params"), dict) else {}
        try:
            confidence = round(float(hit.get("confidence") or 0.0), 3)
        except (TypeError, ValueError):
            confidence = 0.0
        needle_result = IntentResult(
            action=action,
            params=dict(params),
            confidence=max(0.0, min(1.0, confidence)),
            clarification=f"Taking that as '{action}', Sir — correct?",
        )
        if contender is None or needle_result.confidence > contender.confidence:
            contender = needle_result
    if contender is not None and contender.confidence >= CONFIRM_THRESHOLD:
        return contender
    return IntentResult(
        action="unknown",
        clarification=(
            "Forgive me, Sir — that one slipped past the valet. "
            "Might you phrase it once more?"
        ),
    )
