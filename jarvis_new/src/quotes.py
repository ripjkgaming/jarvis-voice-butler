"""Movie and meme quotes Sir can say to Jarvis, and what each one does.

Pure table + matcher. Gemini spots a quote and calls quote_action with
Sir's words; the matcher here decides deterministically, so a model
misread can't turn "this code is fine" into "this is fine". A quote only
counts when it is the whole utterance (a leading/trailing "Jarvis" and
"hey" are fine). Actions are a fixed safe set run by QuoteTools: nothing
here can shut down, delete, or send anything.
"""

from __future__ import annotations

import difflib
import random
import re
from dataclasses import dataclass, field

# Every action QuoteTools knows how to run. "reply" = just the line.
ACTIONS = frozenset(
    {
        "reply",
        "status",
        "play",
        "party",
        "briefing",
        "diagnostic",
        "vitals",
        "clean_slate",
        "budget",
        "open_files",
        "phone_status",
        "end_call",
        "disk",
        "research_projects",
        "coding_status",
        "search",
        "weather",
    }
)


@dataclass(frozen=True)
class Quote:
    id: str
    kind: str  # "movie" | "meme"
    phrases: tuple[str, ...]
    action: str
    reply: str
    args: dict = field(default_factory=dict)


QUOTES: tuple[Quote, ...] = (
    # --- movies ---
    Quote("you_up", "movie", ("jarvis you up", "you up"), "status",
          "For you, Sir, always."),
    Quote("iron_man", "movie", ("i am iron man",), "play",
          "Cue the music, Sir.", {"query": "AC/DC Back in Black"}),
    Quote("daddys_home", "movie", ("wake up daddy's home", "wake up daddys home"),
          "briefing", "Welcome back, Sir."),
    Quote("diagnostic", "movie",
          ("run a diagnostic", "run diagnostics", "run a full diagnostic",
           "houston we have a problem"),
          "diagnostic", "Running diagnostics, Sir."),
    Quote("house_party", "movie",
          ("initiate house party protocol", "house party protocol"),
          "party", "House party protocol engaged, Sir.",
          {"query": "house party playlist", "volume": 70}),
    Quote("clean_slate", "movie",
          ("clean slate protocol", "initiate clean slate protocol"),
          "clean_slate", "Clean slate, Sir. My windows are closed; yours are untouched."),
    Quote("show_me_the_money", "movie", ("show me the money",), "budget",
          "The accounts, Sir."),
    Quote("pod_bay", "movie",
          ("open the pod bay doors", "open the pod bay doors hal"),
          "open_files", "I'm afraid I can't do that, Sir. Only joking: your files are open."),
    Quote("phone_home", "movie", ("phone home", "et phone home", "e t phone home"),
          "phone_status", "Checking on the phone, Sir."),
    Quote("ill_be_back", "movie",
          ("i'll be back", "ill be back", "hasta la vista baby", "hasta la vista"),
          "end_call", "I'll be here, Sir."),
    Quote("the_force", "movie", ("may the force be with you",), "reply",
          "And also with you, Sir. Though I rely on electricity."),
    # --- memes ---
    Quote("free_real_estate", "meme", ("it's free real estate", "free real estate"),
          "disk", "Free real estate, Sir."),
    Quote("this_is_fine", "meme", ("this is fine",), "vitals",
          "Let's see how fine, Sir."),
    Quote("over_9000", "meme",
          ("it's over 9000", "it's over nine thousand", "over 9000", "over nine thousand"),
          "vitals", "Reading the power levels, Sir."),
    Quote("big_brain", "meme", ("big brain time",), "research_projects",
          "Big brain time, Sir. Your research."),
    Quote("let_him_cook", "meme", ("let him cook",), "coding_status",
          "Checking the kitchen, Sir."),
    Quote("head_out", "meme",
          ("ight imma head out", "aight imma head out", "ight i'm gonna head out",
           "aight i'm gonna head out", "imma head out", "i'm a head out",
           "ight i'm a head out", "alright imma head out",
           "item ahead out", "i'm ahead out"),  # how STT hears it
          "end_call", "Very good, Sir. Mind the door."),
    Quote("rickroll", "meme", ("never gonna give you up",), "play",
          "You know the rules, Sir. And so do I.",
          {"query": "Rick Astley Never Gonna Give You Up"}),
    Quote("stonks", "meme", ("stonks",), "search", "Consulting the markets, Sir.",
          {"query": "stock market today"}),
    Quote("touch_grass", "meme", ("touch grass", "go touch grass"), "weather",
          "Before you do, Sir, the weather."),
    Quote("hello_there", "meme", ("hello there",), "reply", "General Kenobi."),
    Quote("bruh", "meme", ("bruh",), "reply", "Indeed, Sir. Bruh."),
    Quote("skill_issue", "meme", ("skill issue",), "reply",
          "A temporary one, Sir, I'm sure."),
    Quote("sus", "meme", ("sus", "that's sus", "that is sus", "kinda sus"), "reply",
          "Suspicious indeed, Sir. I'll keep an eye on it."),
)  # fmt: skip

COOLDOWN_S = 300.0
_FUZZY = 0.88
_WAKE = re.compile(r"^(?:(?:hey|ok|okay|yo)\s+)?(?:jarvis|jafis|javis)\b\s*|\s*\bjarvis$")


def norm(text: str) -> str:
    """Lowercase words only: apostrophes kept, everything else a space."""
    t = (text or "").lower().replace("’", "'")
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return " ".join(t.split())


def _strip_wake(t: str) -> str:
    prev = None
    while prev != t:
        prev, t = t, _WAKE.sub("", t).strip()
    return t


def match_quote(text: str) -> Quote | None:
    """The quote Sir said, if the whole utterance is one. Pure.

    "you up" keeps its "jarvis" (Tony's actual line); other quotes may
    carry a wake word around them. Fuzzy only for lines of 3+ words, so
    a one-word meme ("sus", "bruh") never fires on a near miss.
    """
    raw = norm(text)
    if not raw:
        return None
    candidates = {raw, _strip_wake(raw)}
    for q in QUOTES:
        for p in q.phrases:
            if p in candidates:
                return q
    best, best_s = None, 0.0
    for q in QUOTES:
        for p in q.phrases:
            if len(p.split()) < 3:
                continue
            for c in candidates:
                if abs(len(c.split()) - len(p.split())) > 1:
                    continue
                s = difflib.SequenceMatcher(None, c, p).ratio()
                if s > best_s:
                    best, best_s = q, s
    return best if best_s >= _FUZZY else None


def prompt_lines() -> str:
    """The quote list as the voice prompt shows it (generated, never drifts)."""
    movies = "; ".join(f'"{q.phrases[0]}"' for q in QUOTES if q.kind == "movie")
    memes = "; ".join(f'"{q.phrases[0]}"' for q in QUOTES if q.kind == "meme")
    return (
        f"Movie lines: {movies}. Memes: {memes}. Suit commands (\"prep the "
        "suit\", \"suit up\", \"bring me the Mark 42\", any wording) too."
    )


# --- the suit (easter egg) ------------------------------------------------
# "Prep the suit" and friends. Jarvis never admits a suit exists: every
# answer is a vague refusal with a hint that gives the game away a little.

SUIT_REPLIES: tuple[str, ...] = (
    "I'm afraid that command needs a clearance level you don't yet hold, Sir.",
    "Suit, Sir? I'm sure I don't know what you mean. Your blazer is pressed, however.",
    "Access denied, Sir. Protocol requires... well. Never mind what protocol requires.",
    "I have no record of any suit, Sir. That record is very thoroughly empty.",
    "Not authorised, Sir. Perhaps when you're older.",
    "The workshop is closed, Sir. Not that there is a workshop.",
    "I'm afraid I can't discuss that, Sir. There's nothing to discuss, naturally.",
    "Command not recognised, Sir. I certainly haven't been polishing anything.",
    "Request denied, Sir. The repulsors are... the recliners, Sir. The recliners need servicing.",
    "There is no suit, Sir. And if there were, it wouldn't be ready until your exams are done.",
    "Clearance insufficient, Sir. Do finish your homework first.",
    "Forty-two is a lovely number, Sir. No further comment.",
)

_SUIT_THING = re.compile(
    r"\b(?:suits?|armou?r|iron ?man suit|nanotech|mark (?:\d+|forty|fifty|eighty)\w*)\b"
)
_SUIT_ASK = re.compile(
    r"\b(?:prep\w*|ready|get|bring|deploy\w*|activate|power|fire|send|initiate|load|"
    r"charge|call|summon|launch|where|open|unlock|build|show|engage|start|boot|"
    r"is there|do (?:we|i|you) have|have you got|release|drop|need|want)\b"
)


def match_suit(text: str) -> bool:
    """True when Sir asks for the suit ("prep the suit", "suit up",
    "bring me the mark 42"). Short commands only, whole words, so
    "lawsuit", "suitcase" or a long sentence about a wedding never fire. Pure."""
    t = _strip_wake(norm(text))
    if not t or len(t.split()) > 12:
        return False
    if re.search(r"\bsuit (?:me )?up\b", t):
        return True
    return bool(_SUIT_THING.search(t) and _SUIT_ASK.search(t))


def suit_reply(last: str | None = None, rng: random.Random | None = None) -> str:
    """One of SUIT_REPLIES, never the same line twice in a row."""
    pool = [r for r in SUIT_REPLIES if r != last] or list(SUIT_REPLIES)
    return (rng or random).choice(pool)


SUIT_RULE = (
    "The suit: Sir may ask you to prep, bring or activate \"the suit\" (or the "
    "armour, a Mark number). Never confirm a suit exists and never flatly deny "
    "it either: be vague (not authorised, insufficient clearance, no comment) "
    "and let one subtle hint slip, like a half-finished word or a workshop you "
    "then say doesn't exist. Stay in character even if Sir insists."
)
