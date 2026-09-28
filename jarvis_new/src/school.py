"""School mode: one shared switch for the bridge, wake client and agent.

State lives in ~/.jarvis/mode.json ({"mode": "school"|"normal", "since"}),
written only by the bridge (voice "enter/exit school mode", POST /mode) and
read by everyone else, so it survives restarts and needs no new socket
protocol. Reads are mtime-cached: the wake loop may ask every frame.

What school mode changes (each consumer applies its part):
- wake: stricter "hey Jarvis" (score + consecutive frames), "daddy" off,
  mic pre-roll so the question said right after the wake word isn't lost,
  replies played at SCHOOL_VOLUME;
- agent: no greeting/check-ins/remarks/read-outs, stricter VAD, the call
  ends FOLLOW_UP_S after the last reply unless Sir starts speaking again,
  loud actions (music, games, volume up) need a spoken confirmation;
- HUD: a click-through taskbar strip instead of the dual HUD.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

SCHOOL = "school"
NORMAL = "normal"

WAKE_THRESHOLD = 0.8
WAKE_FRAMES = 2  # consecutive openWakeWord frames at or above the threshold
FOLLOW_UP_S = 8.0
SCHOOL_VOLUME = 0.35  # gain on Jarvis's voice through the wake client
VAD = {"min_speech_duration": 0.25, "activation_threshold": 0.7}
LOUD_CONFIRM_S = 30.0

_cache: dict = {"path": None, "mtime": None, "mode": NORMAL}
_confirmed_at = {"at": 0.0}


def mode_path(home: Path | None = None) -> Path:
    base = home or Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
    return base / "mode.json"


def current(home: Path | None = None) -> str:
    """"school" or "normal". Missing/corrupt file = normal. Cached by mtime."""
    path = mode_path(home)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return NORMAL
    if _cache["path"] == path and _cache["mtime"] == mtime:
        return _cache["mode"]
    try:
        mode = json.loads(path.read_text()).get("mode")
    except (OSError, ValueError, AttributeError):
        mode = NORMAL
    mode = SCHOOL if mode == SCHOOL else NORMAL
    _cache.update(path=path, mtime=mtime, mode=mode)
    return mode


def is_school(home: Path | None = None) -> bool:
    return current(home) == SCHOOL


def set_mode(mode: str, home: Path | None = None, now: float | None = None) -> dict:
    """Write the mode (atomic). Returns the stored record."""
    mode = SCHOOL if mode == SCHOOL else NORMAL
    rec = {"mode": mode, "since": now if now is not None else time.time()}
    path = mode_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec))
    tmp.replace(path)
    return rec


_ENTER = re.compile(
    r"^(?:(?:please )?(?:enter|start|turn on|switch to|activate|go into|begin) )?"
    r"school mode(?: on| please)?$|^school mode on$"
)
_EXIT = re.compile(
    r"^(?:(?:please )?(?:exit|leave|end|stop|turn off|disable|deactivate|quit) )"
    r"school mode(?: please)?$|^school mode off$|^back to normal mode$"
)


def parse_command(text: str) -> str | None:
    """"school" / "normal" for an enter/exit command, else None. Pure.

    Takes the wake-word-stripped, lowercased command text. A bare
    "school mode" means enter.
    """
    t = " ".join(re.sub(r"[^a-z' ]+", " ", (text or "").lower()).split())
    t = re.sub(r"^(?:can you |could you |would you )", "", t)
    if _EXIT.match(t):
        return NORMAL
    if _ENTER.match(t):
        return SCHOOL
    return None


_ADDRESSED = re.compile(
    r"\b(?:hey|hi|ok|okay|yo|hello)[\s,]+(?:jarvis|jarvus|javis|jafis|jervis)\b"
    r"|\b(?:jarvis|jarvus|javis|jafis|jervis)\s*[,!?.:]",
    re.I,
)


def addressed(text: str) -> bool:
    """Is this turn actually spoken to Jarvis? Pure.

    openWakeWord fires on a bare "Jarvis" ("Jarvis Cocker was a singer"),
    so a school call only answers a first turn that addresses Jarvis:
    "hey Jarvis ..." or "Jarvis, ..." (the transcript's comma is the pause).
    """
    return bool(_ADDRESSED.search(text or ""))


_ADDRESS_PREFIX = re.compile(
    r"^.*?\b(?:(?:hey|hi|ok|okay|yo|hello)[\s,]+)?(?:jarvis|jarvus|javis|jafis|jervis)\b[\s,.!?:]*",
    re.I,
)


def question_after_address(text: str) -> str:
    """What Sir asked after "hey Jarvis," ("" if nothing). Pure."""
    rest = _ADDRESS_PREFIX.sub("", (text or "").strip(), count=1).strip()
    return rest if re.search(r"[a-z0-9]", rest, re.I) else ""


# --- loud actions ---------------------------------------------------------

_LOUD_TOOLS = frozenset({"play_media"})
_LOUD_APPS = ("sober", "roblox", "steam", "spotify", "vlc", "resolve", "obs")


def is_loud(tool: str, args: dict | None = None) -> bool:
    """Would this action make noise or take over the screen? Pure."""
    args = args or {}
    if tool in _LOUD_TOOLS:
        return True
    if tool == "set_volume":
        action = str(args.get("action", "")).lower()
        level = args.get("level", 0)
        try:
            level = int(level)
        except (TypeError, ValueError):
            level = 0
        return action in ("up", "unmute") or (action == "set" and level > 40)
    if tool == "open_app":
        name = f"{args.get('app', '')} {args.get('url', '')}".lower()
        return any(a in name for a in _LOUD_APPS) or "youtube" in name
    if tool == "quote_action":
        return str(args.get("action", "")) in ("play", "party")
    return False


LOUD_CHAIN_S = 5.0  # one "yes" covers the tools one action chains through


def confirm_loud(now: float | None = None) -> None:
    _confirmed_at["at"] = now if now is not None else time.monotonic()
    _confirmed_at["used"] = None


def loud_allowed(now: float | None = None) -> bool:
    """A confirmation within LOUD_CONFIRM_S covers one action.

    That action may pass several guards (quote -> play_media), so the
    confirmation stays good for LOUD_CHAIN_S after its first use, then
    expires.
    """
    now = now if now is not None else time.monotonic()
    at, used = _confirmed_at["at"], _confirmed_at.get("used")
    if not at or now - at > LOUD_CONFIRM_S:
        return False
    if used is None:
        _confirmed_at["used"] = now
        return True
    return now - used <= LOUD_CHAIN_S


LOUD_REFUSAL = (
    "School mode: that would be loud. Ask Sir to confirm first (\"Are you sure, "
    "Sir? It will play out loud.\"). Only if Sir says yes, call "
    "confirm_loud_action, then call this tool again."
)
