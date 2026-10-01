"""One action per spoken request, whichever path gets there first.

On the realtime pipeline Gemini Live hears Sir's audio itself, so a single
"play some music" reaches two executors: the desktop fast path (transcript
-> bridge route, milliseconds) and Gemini's own tool call a moment later.
clear_user_turn() cannot stop a Live turn that the server already took, so
both used to run: two Brave windows, two volume steps, two launches.

Every executor claims the action before running it. When a different
source already ran the same action family within WINDOW_S, the claim
returns that earlier entry and the caller reuses its reply instead of
acting again. Each other source is absorbed once per entry; repeats from
the same source (Sir saying "louder" twice) always run.

Thread-safe (the route runs in a worker thread, the model's tools on the
event loop). In-process only: both executors live in the agent process.
"""

from __future__ import annotations

import re
import threading
import time
from urllib.parse import urlsplit

WINDOW_S = 8.0
# YouTube resolution alone may take 12s. Protect legacy pending claims longer
# than the echo window, but recover if an executor disappeared without settling.
# Workers with an explicit lease stay protected until they settle.
IN_FLIGHT_S = 120.0

_lock = threading.Lock()
_recent: list[dict] = []

_VOLUME = frozenset(
    {
        "volume_up",
        "volume_down",
        "volume_set",
        "volume_mute",
        "volume_unmute",
        "set_volume",
    }
)
_MEDIA = frozenset({"media_play_pause", "media_next", "media_prev", "media_control"})
_MEDIA_KEYS = frozenset({"play", "pause", "play-pause", "next", "previous", "stop"})

# Words that say "play something" without naming what. "play some music"
# reaches the route as query "" but Gemini may pass "music" or "some songs".
_PLAY_NOISE = frozenset(
    {
        "a",
        "an",
        "any",
        "by",
        "for",
        "from",
        "me",
        "music",
        "my",
        "of",
        "on",
        "playlist",
        "some",
        "something",
        "song",
        "songs",
        "the",
        "track",
        "tracks",
        "video",
        "videos",
        "youtube",
    }
)


def family(tool: str, args: dict | None = None) -> str | None:
    """Action family both executors share, or None when unguarded. Pure."""
    args = args or {}
    if tool == "play_media":
        return "play"
    if tool == "open_app":
        return "open"
    if tool == "close_app":
        return "close"
    if tool == "set_suit_diagnostics":
        return "suit_diagnostics"
    if tool in _VOLUME:
        if tool == "set_volume" and str(args.get("action", "")).lower() == "status":
            return None
        return "volume"
    if tool in _MEDIA:
        action = str(args.get("action", "")).lower().replace("_", "-")
        if tool == "media_control" and action not in _MEDIA_KEYS:
            return None  # seek/loop/shuffle/speed never come from the route
        return "media"
    return None


def _play_words(args: dict | None) -> frozenset[str]:
    """Content words of a play query; empty means "anything". Pure."""
    q = str((args or {}).get("query", "")).lower()
    return frozenset(w for w in re.findall(r"[^\W_]+", q) if w not in _PLAY_NOISE)


def _target(args: dict, field: str) -> str:
    """Normalize common spoken names and browser URL targets."""
    value = str(args.get("url") or args.get(field, "")).strip()
    aliases = {
        "browser": "brave",
        "brave-browser": "brave",
        "files": "dolphin",
        "file manager": "dolphin",
        "terminal": "konsole",
        "calculator": "kcalc",
        "calc": "kcalc",
        "youtube": "youtube.com",
        "you tube": "youtube.com",
    }
    if "://" in value:
        parsed = urlsplit(value)
        value = parsed.netloc.lower().removeprefix("www.") + parsed.path.rstrip("/")
        if parsed.query:
            value += "?" + parsed.query
    else:
        value = value.lower()
    return aliases.get(value, value)


def _action(tool: str, args: dict) -> tuple:
    action = str(args.get("action", "")).lower().replace("_", "-")
    if tool.startswith("volume_"):
        action = tool.removeprefix("volume_")
    elif tool in _MEDIA:
        action = {
            "media_play_pause": "play-pause",
            "media_next": "next",
            "media_prev": "previous",
        }.get(tool, action)
    if action == "level":
        action = "set"
    return (action, str(args.get("level", 50))) if action == "set" else (action,)


def _same_request(fam: str, prev: dict, tool: str, args: dict) -> bool:
    """Compare the requested effect, not merely the broad tool family."""
    prev_args = prev["args"]
    if fam == "suit_diagnostics":
        return bool(prev_args.get("open")) == bool(args.get("open"))
    if fam in ("open", "close"):
        field = "app" if fam == "open" else "name"
        return _target(prev_args, field) == _target(args, field)
    if fam in ("volume", "media"):
        a, b = _action(prev["tool"], prev_args), _action(tool, args)
        # The bridge exposes a toggle for both spoken play and pause.
        if fam == "media" and (a == ("play-pause",) or b == ("play-pause",)):
            return a[0] in _MEDIA_KEYS - {"next", "previous", "stop"} and b[
                0
            ] in _MEDIA_KEYS - {"next", "previous", "stop"}
        return a == b
    a, b = _play_words(prev_args), _play_words(args)
    if not a or not b:
        return a == b
    return a <= b or b <= a


def claim(
    tool: str,
    args: dict | None,
    source: str,
    now: float | None = None,
    *,
    lease: object | None = None,
) -> dict | None:
    """Claim ``tool`` for ``source``; None means run it.

    A dict return is the earlier claim this one duplicates (another source,
    same family, within WINDOW_S): skip the action and reuse ``entry["say"]``
    (None while the first run is still in flight) and ``entry["ok"]``.

    A worker may supply a unique ``lease`` token and must settle with the
    same token. Its pending claim then lasts until settlement, and concurrent
    identical workers can safely finish out of order. Legacy claims retain
    their abandoned-work timeout.
    """
    fam = family(tool, args)
    if fam is None:
        return None
    args = dict(args or {})
    now = time.monotonic() if now is None else now
    with _lock:
        _recent[:] = [
            entry
            for entry in _recent
            if (entry["ok"] is None and entry.get("lease") is not None)
            or now - entry["t"] < (IN_FLIGHT_S if entry["ok"] is None else WINDOW_S)
        ]
        for prev in _recent:
            if (
                prev["family"] == fam
                and source not in prev["seen"]
                and prev["ok"] is not False
                and _same_request(fam, prev, tool, args)
            ):
                prev["seen"].add(source)
                return dict(prev)
        _recent.append(
            {
                "family": fam,
                "tool": tool,
                "args": args,
                "source": source,
                "seen": {source},
                "t": now,
                "ok": None,
                "say": None,
                "lease": lease,
            }
        )
        # Bound completed history without evicting work still in progress.
        # Legacy abandoned pending entries are bounded by IN_FLIGHT_S above.
        _recent[:] = [
            entry
            for index, entry in enumerate(_recent)
            if entry["ok"] is None or index >= len(_recent) - 256
        ]
        return None


def settle(
    tool: str,
    args: dict | None,
    source: str,
    ok: bool,
    say: str = "",
    *,
    now: float | None = None,
    lease: object | None = None,
) -> None:
    """Record a claim's outcome; owned claims require their exact lease token."""
    fam = family(tool, args)
    if fam is None:
        return
    with _lock:
        for entry in _recent:
            if (
                entry["family"] == fam
                and entry["source"] == source
                and entry["tool"] == tool
                and entry["args"] == dict(args or {})
                and entry["ok"] is None
                and entry.get("lease") is lease
            ):
                entry["ok"] = bool(ok)
                entry["say"] = say or None
                entry["t"] = time.monotonic() if now is None else now
                break


def duplicate_reply(entry: dict, fallback: str) -> dict[str, str]:
    """Tool result for a model call the fast path already carried out.

    Tells Gemini the action is done so it confirms in a word instead of
    retrying or narrating it a second time. Pure.
    """
    if entry.get("ok") is None:
        return {
            "say": "That action is still in progress, Sir.",
            "note": (
                "Another executor is handling this request. Do not call it again "
                "or claim it completed; its outcome is not known yet."
            ),
        }
    say = entry.get("say") or fallback
    return {
        "say": say,
        "note": (
            "Already done a moment ago by the instant path, which also "
            "confirmed it aloud. Do not call this again; at most say 'Done, Sir.'"
        ),
    }


def reset() -> None:
    """Forget every claim (tests)."""
    with _lock:
        _recent.clear()
