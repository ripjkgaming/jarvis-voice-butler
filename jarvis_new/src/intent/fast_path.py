"""Instant commands, caught before Gemini sees the turn.

Voice turns used to go straight to Gemini, which sometimes said "turned it
down" without calling any tool. This runs first, on the final transcript
of each turn (Assistant.on_user_turn_completed):

1. a strict phrase grammar for volume and media keys ("turn the volume
   down", "volume to 30", "pause the music", "next song", "mute");
2. otherwise the intent resolver (exact aliases, fuzzy, then Needle), used
   only when it is confident (should_act) AND the action is on the
   instant allowlist with complete parameters.

A hit is executed locally (verified volume/media, the clock) and Gemini's
turn is skipped. Anything else, including anything compound ("turn it down
and open spotify"), long, or uncertain, goes to Gemini untouched.
JARVIS_FAST_PATH=0 turns it off.
"""

from __future__ import annotations

import asyncio
import os
import re
import time

MAX_WORDS = 9
DEDUPE_S = 8.0
INSTANT = ("set_volume", "media_control", "tell_time")
VOLUME_ACTIONS = ("up", "down", "set", "mute", "unmute")
MEDIA_ACTIONS = ("pause", "play", "play-pause", "next", "previous", "stop")

_FILLER = re.compile(
    r"^(hey |ok |okay )?(jarvis[, ]+)?(can you |could you |would you |please |pls |just )*",
    re.I,
)
_COMPOUND = re.compile(r"\b(and|then|also|after that|but)\b")
_SOUND = r"(volume|sound|audio|music|it|this|that)"

_RULES: list[tuple[re.Pattern, str, dict]] = [
    # volume level: "volume to 30", "set the volume at 40 percent"
    (
        re.compile(
            r"\b(volume|sound)\b.*?\b(to|at)\s+(?P<level>\d{1,3})\s*(%|percent)?\b"
        ),
        "set_volume",
        {"action": "set"},
    ),
    # mute / unmute
    (re.compile(rf"^(unmute)( (the )?{_SOUND})?$"), "set_volume", {"action": "unmute"}),
    (re.compile(rf"^(mute)( (the )?{_SOUND})?$"), "set_volume", {"action": "mute"}),
    # up / down
    (
        re.compile(rf"^turn (the )?{_SOUND} (down|lower)( a (bit|little|notch))?$"),
        "set_volume",
        {"action": "down"},
    ),
    (
        re.compile(rf"^turn (the )?{_SOUND} up( a (bit|little|notch))?$"),
        "set_volume",
        {"action": "up"},
    ),
    (
        re.compile(
            r"^turn (down|up) (the )?(volume|sound|music)( a (bit|little|notch))?$"
        ),
        "set_volume",
        {"action": "@1"},
    ),
    (
        re.compile(r"^(volume|sound) (down|up)( a (bit|little|notch))?$"),
        "set_volume",
        {"action": "@2"},
    ),
    (
        re.compile(
            r"^(lower|reduce|decrease|drop) (the )?(volume|sound)( a (bit|little|notch))?$"
        ),
        "set_volume",
        {"action": "down"},
    ),
    (
        re.compile(
            r"^(raise|increase|boost) (the )?(volume|sound)( a (bit|little|notch))?$"
        ),
        "set_volume",
        {"action": "up"},
    ),
    (
        re.compile(r"^(make it |a bit |bit )?(quieter|softer)( please)?$"),
        "set_volume",
        {"action": "down"},
    ),
    (
        re.compile(r"^(make it |a bit |bit )?louder( please)?$"),
        "set_volume",
        {"action": "up"},
    ),
    # media keys
    (
        re.compile(
            r"^pause( (the |this |that )?(music|song|video|media|youtube|spotify|playback|it|this|that))?$"
        ),
        "media_control",
        {"action": "pause"},
    ),
    (
        re.compile(
            r"^stop (the |this )?(music|song|video|media|youtube|spotify|playback)$"
        ),
        "media_control",
        {"action": "pause"},
    ),
    (
        re.compile(r"^(resume|unpause)( (the )?(music|song|video|playback|it))?$"),
        "media_control",
        {"action": "play"},
    ),
    (
        re.compile(r"^(continue|keep) (playing|the (music|song|video))$"),
        "media_control",
        {"action": "play"},
    ),
    (
        re.compile(r"^(next|skip)( (the |this )?(song|track|video))$|^next$"),
        "media_control",
        {"action": "next"},
    ),
    (
        re.compile(
            r"^(play (the )?)?(previous|last) (song|track|video)$|^go back to the (previous|last) (song|track|video)$"
        ),
        "media_control",
        {"action": "previous"},
    ),
    # clock
    (re.compile(r"^what('s| is) the time$|^what time is it$"), "tell_time", {}),
]


def enabled() -> bool:
    return os.environ.get("JARVIS_FAST_PATH", "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def clean(text: str) -> str:
    """Lowercase, drop punctuation, 'hey jarvis', 'can you', 'please'. Pure."""
    t = " ".join(re.sub(r"[^a-z0-9%' ]+", " ", (text or "").lower()).split())
    t = _FILLER.sub("", t).strip()
    return re.sub(r" (please|pls|thanks|thank you|sir|mate|bro|jarvis)$", "", t).strip()


def grammar(text: str) -> tuple[str, dict] | None:
    """Strict phrase match -> (tool, args). Pure."""
    for pattern, tool, args in _RULES:
        m = pattern.search(text)
        if not m:
            continue
        out = dict(args)
        action = str(out.get("action", ""))
        if action.startswith("@"):
            out["action"] = m.group(int(action[1:]))
        if "level" in pattern.groupindex:
            out["level"] = max(0, min(150, int(m.group("level"))))
        return tool, out
    return None


def _complete(tool: str, params: dict) -> dict | None:
    """Resolver params -> tool args, only when nothing is missing. Pure."""
    if tool == "set_volume":
        action = str(params.get("action") or "")
        if action not in VOLUME_ACTIONS or (action == "set" and "level" not in params):
            return None
        return {k: params[k] for k in ("action", "level") if k in params}
    if tool == "media_control":
        action = str(params.get("action") or "")
        return {"action": action} if action in MEDIA_ACTIONS else None
    if tool == "tell_time":
        return {}
    return None


def match(text: str, resolve=None) -> tuple[str, dict, str] | None:
    """(tool, args, source) for an instant command, or None for Gemini."""
    if not enabled():
        return None
    t = clean(text)
    if not t or len(t.split()) > MAX_WORDS or _COMPOUND.search(t):
        return None
    hit = grammar(t)
    if hit is not None:
        return hit[0], hit[1], "grammar"
    try:
        if resolve is None:
            from intent.resolver import resolve_intent as resolve
        result = resolve(t)
    except Exception:
        return None
    if not getattr(result, "should_act", False) or result.action not in INSTANT:
        return None
    args = _complete(result.action, dict(result.params or {}))
    if args is None:
        return None
    return result.action, args, "needle"


# --- one execution per request ---
# On Gemini Live the transcript arrives while Gemini may be calling the same
# tool itself. Whichever of the two (fast path / LLM tool call) runs second
# within DEDUPE_S reuses the first one's result instead of turning the
# volume down twice. Repeats from the same source (Sir saying "louder"
# twice) always run.

_RECENT: dict[tuple, dict] = {}


def _key(tool: str, args: dict) -> tuple:
    args = dict(args)
    if tool == "set_volume" and args.get("action") not in ("set", "level"):
        args.pop("level", None)
    return tool, tuple(sorted((k, str(v)) for k, v in args.items()))


async def once(tool: str, args: dict, source: str, run) -> tuple[bool, str, bool]:
    """Run ``run()`` -> (ok, say) unless the other source just did.

    Returns (ok, say, duplicate)."""
    key = _key(tool, args)
    now = time.monotonic()
    prev = _RECENT.get(key)
    if (
        prev
        and prev["source"] != source
        and not prev["used"]
        and now - prev["t"] < DEDUPE_S
    ):
        prev["used"] = True
        while prev["result"] is None and time.monotonic() - now < 6.0:
            await asyncio.sleep(0.05)
        ok, say = prev["result"] or (True, "Done.")
        return ok, say, True
    entry = {"source": source, "t": now, "result": None, "used": False}
    _RECENT[key] = entry
    try:
        entry["result"] = await run()
    except BaseException:
        entry["result"] = (False, "That didn't work, Sir.")
        raise
    for k in [k for k, v in _RECENT.items() if now - v["t"] > 60]:
        _RECENT.pop(k, None)
    return entry["result"][0], entry["result"][1], False


async def execute(system_tools, tool: str, args: dict) -> tuple[bool, str]:
    """Run an instant tool on a SystemTools instance -> (ok, spoken line).

    context=None marks the call as the fast path's (see once())."""
    fn = getattr(type(system_tools), tool, None)
    if fn is None:
        return False, f"I can't run {tool} here, Sir."
    try:
        out = await fn(system_tools, None, **args)
    except Exception as exc:  # ToolError carries the spoken reason
        return False, str(exc) or "That didn't work, Sir."
    return True, str((out or {}).get("say") or "Done.")
