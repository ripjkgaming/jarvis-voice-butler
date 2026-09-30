"""Localhost control plane for embedding Jarvis in a desktop shell.

A desktop app (Electron/Tauri) cannot reach into the LiveKit worker or the
wake-word listener directly, so this module exposes host-side state over a
loopback-only HTTP server using stdlib only (no new dependencies)::

    .venv/bin/python src/bridge.py            # serves 127.0.0.1:4317
    JARVIS_BRIDGE_PORT=4320 .venv/bin/python src/bridge.py

Endpoints (all JSON):
    GET  /health    liveness + uptime + version
    GET  /status    voice pipeline state: livekit configured (presence only,
                   never secret values), pipeline mode, wake threshold,
                   voice model present, WhatsApp CDP reachable, log stats
    GET  /actions   tail of ~/.jarvis/actions.log (?limit=1..200, default 50)
    GET  /sys       cpu load + memory + home-disk (Linux /proc; degraded
                   JSON elsewhere instead of crashing)
    GET  /config    sanitized config surface for a settings screen
    POST /mic       {"muted": bool} -> proxied to the wake_client listener
                   on $JARVIS_HOME/wake.sock (2s timeout); 503 fail-soft
                   {"ok": false} when the listener is absent
    POST /summon    {} -> PTT talk request to the wake listener (same
                   summon the "hey Jarvis" hotword performs, no hotword
                   needed); HUD NumpadEnter + shell talk arrive here.
                   503 fail-soft when the listener is absent
    GET  /mic       wake_client mute status passthrough; 200 {"ok": false}
                   when the listener is absent
    GET  /room      live wake room for HUD receive-only join
                   {"ok": true, "room": name|null} (null = no call)
    GET  /captions  tail of ~/.jarvis/captions.log (?limit=1..50, default 20)
                    [{ts, role, text}] for HUDs without a LiveKit client
    POST /window    {"id": window UUID, "action": activate|minimize}
    POST /launch    {"desktop": "x.desktop"} -> launch a pinned/menu app
    GET  /apps      all visible apps for the launcher menu
    GET  /quick     wifi/bluetooth/volume/brightness/dnd snapshot
    POST /quick     toggle wifi/bluetooth/dnd, set volume/mute/brightness
    POST /power     {"action": lock|sleep|logout|restart|shutdown}
Deliberately NOT here: hangup, sendText, hotword events. Those live
inside the LiveKit room (token via the shell's mint_token command) and
have no out-of-process API yet. Every endpoint is fail-soft: a missing
file, binary, or socket degrades one field, never the whole response.

Phone API (JarvisLink Android app over Tailscale): the same server, with
`JARVIS_BRIDGE_BIND=0.0.0.0` (or the tailnet IP) + a MANDATORY
`JARVIS_BRIDGE_TOKEN` (refuses to start public without one):

    POST /type {text?, key?}     type text / press key via wtype
    POST /tool {tool, args?}     allowlisted control (volume/media/apps/
                                 screenshot/notify/lock — argv only, the
                                 same binaries the system tools use)
    POST /talk {audio_b64, rate?} 16k PCM -> whisper transcript +
                                 Gemini-direct reply + Piper audio back
                                 {transcript, reply, audio_b64, audio_rate}
    POST /camera/frame {image_b64} ingest phone photo (pruned to last 50)
    GET  /camera/latest          newest ingested photo (image/jpeg)

Security: binds 127.0.0.1 by default. If JARVIS_BRIDGE_TOKEN is set, all
endpoints require `Authorization: Bearer <token>` (constant-time compare).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse

import school as _school

VERSION = "0.2.0"
DEFAULT_PORT = 4317
_WAKE_RPC_TIMEOUT = 2.0
_STARTED_AT = time.time()
_PHONE_WHISPER = None  # lazy singleton for /talk (wake venv has it too)
# Stable text model for direct REST calls (the /chat + /talk side-channels).
# Env-overridable (JARVIS_TEXT_MODEL). 2026-09: gemini-3.8-flash (GA) is the
# newest text Flash; the old 3.5-flash-lite default existed only for its free
# tier (~500 RPD). 3.8-flash is free-of-charge on the free tier too.
# NOTE: *-live models are NOT usable here: they reject generateContent and
# demand the Live API websocket (bidiGenerateContent) — there is no
# "3.8-flash-live" SKU. The voice brain lives in agent.py's RealtimeModel
# (live voice API namespace); the two must not be "kept in sync".
GEMINI_TEXT_MODEL = os.environ.get("JARVIS_TEXT_MODEL", "gemini-3.8-flash")
# High-usage backups for direct text calls: tried in order when the
# previous ID hits quota/rate limits (429/5xx/overloaded). Separate
# quotas per model ID, so a saturated primary still leaves options.
# (gemini-2.5-flash retired — 404s for new keys — so backup is 3.6-flash,
# mirroring agent.py TEXT_FALLBACK_CHAIN.)
GEMINI_TEXT_FALLBACKS = ("gemini-3.7-flash", "gemini-3.6-flash")

# Guest mode (phone sends {"guest": true}): cold persona, and only the
# harmless tools below are allowed. Everything else is refused with a
# frosty one-liner — enforced here so no client can bypass it.
_GUEST_VOICE_TOOLS = frozenset(
    {
        "volume_get",
        "volume_up",
        "volume_down",
        "volume_mute",
        "volume_unmute",
        "volume_set",
        "media_play_pause",
        "media_next",
        "media_prev",
        "play_media",
    }
)
_GUEST_REFUSAL = "No. Guests don't get that."

# Spoken/chatted text -> allowlisted PC tool. First regex match wins, so
# unlock sits before lock ("unlock" contains "lock") and unmute before
# mute. Entries: (pattern, tool, args_from_match, success_reply).
# A None reply means the reply is built dynamically per tool.
_VOICE_TOOLS = (
    (
        # "play some music" / "play a video": no subject -> saved playlist.
        r"^play\s+(?:me\s+)?(?:some|a|any|my)?\s*"
        r"(?:music|songs?|videos?|something|playlist)(?:\s+on\s+youtube)?[.!?]*$",
        "play_media",
        lambda m: {"query": ""},
        None,
    ),
    (
        # "play <anything>" -> first matching YouTube video in Brave.
        # Bare "play", "play it", "play next ..." stay on the media controls.
        r"^play\s+(?!(?:pause|it|that|again|next|previous|prev|last|(?:the\s+)?(?:next|previous|last)\s+\w+)\b)(.{2,120}?)"
        r"(?:\s+on\s+youtube)?[.!?]*$",
        "play_media",
        lambda m: {"query": _clean_play_query(m.group(1))},
        None,
    ),
    (r"\bunlock\b", "unlock", lambda m: {}, "Unlocked, Sir."),
    (r"\block\b", "lock", lambda m: {}, "Locked, Sir."),
    (r"un-?mute", "volume_unmute", lambda m: {}, "Unmuted, Sir."),
    (r"\bmute\b", "volume_mute", lambda m: {}, "Muted, Sir."),
    (
        r"(?:set|turn|change|put)?\s*volume\s*(?:to|at)?\s*(\d{1,3})\s*(?:%|percent)?|"
        r"\bmax(?:imum)?\s*volume\b",
        "volume_set",
        lambda m: {
            "level": 100 if m.group(0).strip().startswith("max") else int(m.group(1))
        },
        None,
    ),
    (
        r"volume up|turn (it|the volume) up|louder",
        "volume_up",
        lambda m: {},
        "Turned it up, Sir.",
    ),
    (
        r"volume down|turn (it|the volume) down|quieter",
        "volume_down",
        lambda m: {},
        "Turned it down, Sir.",
    ),
    (r"volume|how loud", "volume_get", lambda m: {}, None),
    (
        r"\bnext\b|skip( this| the)?( track| song)?",
        "media_next",
        lambda m: {},
        "Skipped, Sir.",
    ),
    (
        r"\bprevious\b|last (track|song)|go back",
        "media_prev",
        lambda m: {},
        "Back one, Sir.",
    ),
    (r"\bpause\b|\bresume\b|\bplay\b", "media_play_pause", lambda m: {}, "Done, Sir."),
    (
        r"screenshot|capture (the |my )?screen|picture of (the |my )?screen",
        "screenshot",
        lambda m: {},
        "Screenshot taken, Sir — it's on the Screens tab.",
    ),
    (
        r"black ?out|turn off (the |my )?screens?|screens? off",
        "screen_off",
        lambda m: {},
        "Screens off, Sir.",
    ),
    (
        r"\brestore\b|turn (the |my )?screens? back on|screens? back",
        "screens_restore",
        lambda m: {},
        "Screens back, Sir.",
    ),
    (
        r"open (brave|files|terminal|calculator|whatsie)",
        "open_app",
        lambda m: {"app": m.group(1)},
        None,
    ),
    (
        r"^(?:start|open|begin)\s+(?:a\s+|the\s+)?remote(?:\s+desktop)?\s+session\b",
        "remote_start",
        lambda m: {},
        "Remote session ready, Sir.",
    ),
    (
        r"^remote\s+(?:into|in to)\s+(?:my\s+|the\s+)?(?:laptop|pc|computer)\b",
        "remote_start",
        lambda m: {},
        "Remote session ready, Sir.",
    ),
    (
        # Bare noun: STT leaves trailing punctuation ("remote session.") and
        # sometimes a trailing "please" or plural — tolerate all three.
        r"^remote\s+sessions?\b(?:\s+please)?\s*[.?!]*$",
        "remote_start",
        lambda m: {},
        "Remote session ready, Sir.",
    ),
    (
        r"^(?:end|stop|close)\s+(?:the\s+)?remote(?:\s+desktop)?\s+session\b",
        "remote_stop",
        lambda m: {},
        "Remote session closed, Sir.",
    ),
    (
        # Universal launcher: anything else Sir asks to open (apps, visited
        # sites, or the top web result). Anchored so "start the music" style
        # media phrases above always win first.
        # A leading "you" survives STT eating "can" ("you launch roblox?");
        # trailing punctuation never reaches the app name.
        r"^(?:you\s+)?(?:open|launch|start|fire up|bring up|pull up)\s+"
        r"(?!(?:the\s+|my\s+)?(?:music|song|track|playback|playlist|timer|stopwatch|recording"
        r"|volume|sound|brightness|screen)\b)"
        r"(?:the\s+|my\s+)?(.{2,60}?)[\s.,!?]*$",
        "open_app",
        lambda m: {"app": m.group(1).strip()},
        None,
    ),
    (
        # "close YouTube" / "quit Steam": the site's Brave tab(s), else the
        # app's window. Vague "close it/that" and Jarvis's own panels
        # (helper, deep research) stay with the agent.
        r"^(?:close|quit|kill)\s+(?!(?:it|that|this|everything|all|the\s+helper"
        r"|(?:the\s+)?deep\s+research)\b)(?:the\s+|my\s+)?(.{2,60}?)[\s.,!?]*$",
        "close_app",
        lambda m: {"name": m.group(1).strip()},
        None,
    ),
    (
        r"\bping\b|\bnotify\b|send( a)? notification",
        "notify",
        lambda m: {"title": "Jarvis phone", "body": "Ping from phone"},
        "Pinged, Sir.",
    ),
    (
        r"^type (.+)",
        "type",
        lambda m: {"text": m.group(1).strip()[:500]},
        "Typed, Sir.",
    ),
)


# Leading speech cruft that STT leaves on commands ("jarvis, start a
# remote session", "hey jarvis stop it", "please lock the pc"). The layered
# resolver's normalize() strips wakewords/punctuation; the instant regexes
# below must do the same, or every ^-anchored pattern (remote start/stop,
# the universal launcher, type) misses whenever Sir's name — or "please" —
# leads. Stripped only at the START so a "type …" payload keeps its words.
_WAKEWORD_PREFIX = re.compile(
    r"^(?:hey\s+)?(?:jarvis|jeeves|jarves|jervis)\b[\s,.:;\-!?'\"]*"
)
_FILLER_PREFIX = re.compile(
    r"^(?:please|hey|uh|um|er|ah|(?:can|could|would|will)\s+you)\b[\s,.:;\-!?'\"]*"
)


def _clean_voice_text(text: str) -> str:
    """Lowercased command with leading wakeword/filler stripped. Pure."""
    cleaned = (text or "").lower().strip()
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = _WAKEWORD_PREFIX.sub("", cleaned).strip()
        cleaned = _FILLER_PREFIX.sub("", cleaned).strip()
    return cleaned


# Spoken arithmetic -> expression, answered locally with no model round
# trip ("take 2,401 and subtract 1,605", "7 to the power of 4").
_MATH_LEAD = re.compile(
    r"^(?:(?:hey |ok |okay )?(?:jarvis|jafis|javis)[,\s]*)?"
    r"(?:(?:can|could|would) you(?: please)?\s+)?(?:please\s+)?"
    r"(?:(?:tell me|work out|calculate|compute|figure out|solve)\s+)?"
    r"(?:what(?:'s| is| are)?\s+|how much is\s+)?(?:the\s+)?"
)
_MATH_WORDS: tuple[tuple[str, str], ...] = (
    (r"\bsquare root of (\d+(?:\.\d+)?)", r"(\1)**0.5"),
    (r"(\d+(?:\.\d+)?)\s*(?:%|percent|per cent) of (\d+(?:\.\d+)?)", r"(\1*\2/100)"),
    (
        r"\btake (\d+(?:\.\d+)?) and (?:subtract|minus|take away) (\d+(?:\.\d+)?)",
        r"\1 - \2",
    ),
    (r"\btake (\d+(?:\.\d+)?) and (?:add|plus) (\d+(?:\.\d+)?)", r"\1 + \2"),
    (
        r"\btake (\d+(?:\.\d+)?) and (?:multiply(?: it)? by|times) (\d+(?:\.\d+)?)",
        r"\1 * \2",
    ),
    (r"\btake (\d+(?:\.\d+)?) and divide(?: it)? by (\d+(?:\.\d+)?)", r"\1 / \2"),
    (r"\bsubtract (\d+(?:\.\d+)?) from (\d+(?:\.\d+)?)", r"\2 - \1"),
    (r"\badd (\d+(?:\.\d+)?) (?:and|to) (\d+(?:\.\d+)?)", r"\1 + \2"),
    (r"\bmultiply (\d+(?:\.\d+)?) (?:and|by) (\d+(?:\.\d+)?)", r"\1 * \2"),
    (r"\bdivide (\d+(?:\.\d+)?) by (\d+(?:\.\d+)?)", r"\1 / \2"),
    (r"\s*(?:to the power of|raised to(?: the power of)?|to the)\s*", " ** "),
    (r"\s*squared\b", " ** 2"),
    (r"\s*cubed\b", " ** 3"),
    (r"\s*(?:multiplied by|times|x)\s*", " * "),
    (r"\s*(?:divided by|over)\s*", " / "),
    (r"\s*(?:plus|add)\s*", " + "),
    (r"\s*(?:minus|take away|subtract)\s*", " - "),
)
_MATH_EXPR = re.compile(r"^[\d.\s()+\-*/]+$")


def _spoken_math(text: str) -> str | None:
    """Safe arithmetic expression for a spoken sum, or None. Pure."""
    t = (text or "").lower().strip()
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)  # 2,401 -> 2401
    t = t.replace("^", " ** ").replace("×", " * ").replace("÷", " / ")
    t = re.sub(r"[?!.]+$", "", t).strip()
    t = _MATH_LEAD.sub("", t, count=1)
    t = re.sub(r"\s+(?:is|equals?|please|for me)$", "", t).strip()
    for rx, rep in _MATH_WORDS:
        t = re.sub(rx, rep, t)
    t = " ".join(t.split())
    if not _MATH_EXPR.match(t) or not re.search(r"[\d)]\s*(?:\*\*|[+\-*/])\s*\(?\d", t):
        return None
    # Keep exponents voice-sized: 9 ** 99999 would stall the bridge.
    if any(float(e) > 64 for e in re.findall(r"\*\*\s*\(?(\d+(?:\.\d+)?)", t)):
        return None
    return t


def _match_voice_tool(text: str) -> tuple[str, dict, str | None] | None:
    """Map spoken/chatted text to (tool, args, reply). None = pure chat.

    Pure: regexes over the cleaned text (lowercased, leading wakeword and
    filler stripped), first match wins.
    """
    lowered = _clean_voice_text(text)
    if not lowered:
        return None
    expr = _spoken_math(text)
    if expr is not None:
        return ("do_math", {"expr": expr}, None)
    # Watch mode ("I'm leaving" / "watch the laptop"): camera guard +
    # fullscreen card, stopped only by its keybind (src/watch_mode.py).
    try:
        import watch_mode as _watch

        if _watch.parse_command(lowered):
            return ("watch_mode", {}, "Watching the laptop, Sir.")
    except Exception:
        pass
    mode = _school.parse_command(lowered)
    if mode is not None:
        return (
            "school_mode",
            {"mode": mode},
            "School mode, Sir." if mode == _school.SCHOOL else "Back to normal, Sir.",
        )
    # Voice-only Project Archive ("open research projects, navigate to the
    # battery project and open the first document and start scrolling").
    # Before the generic open_app route, which would launch "research
    # projects" as an app.
    try:
        import projects as _projects

        cmds = _projects.parse_voice(lowered)
    except Exception:
        cmds = None
    if cmds:
        return (
            "projects_ui",
            {"commands": cmds, "heard": (text or "").strip()[:200]},
            _projects.reply_for(cmds),
        )
    # Voice-only Drafts window ("open my drafts", "close drafts").
    # Before the generic open_app route, same as the archive above.
    try:
        import drafts_ui as _drafts

        dop = _drafts.parse_voice(lowered)
    except Exception:
        dop = None
    if dop:
        op = dop.get("op")
        return (
            "drafts_ui",
            {"op": op, "heard": (text or "").strip()[:200]},
            None,
        )
    for pattern, tool, args_fn, reply in _VOICE_TOOLS:
        match = re.search(pattern, lowered)
        if match:
            try:
                args = args_fn(match)
            except (IndexError, AttributeError):
                continue
            if tool == "type" and not (isinstance(args, dict) and args.get("text")):
                continue
            return tool, args if isinstance(args, dict) else {}, reply
    return None


_PLAY_FILLER = re.compile(
    r"^(?:me\s+)?(?:(?:a|an|some|the)\s+)?(?:(?:music\s+)?videos?|songs?|tracks?|music)"
    r"\s+(?:by|from|of|about)\s+",
)
_YT_PLAYLIST = "https://www.youtube.com/watch?v=ABFW7Tp_2HI&list=PLR1n3ezbUDL0"
_YT_VIDEO_ID = re.compile(r'"videoId":"([A-Za-z0-9_-]{11})"')


def _clean_play_query(text: str) -> str:
    """Strip filler: a video by grant wisler -> grant wisler. Pure."""
    q = (text or "").strip().rstrip(".!?")
    return _PLAY_FILLER.sub("", q).strip() or q


def _youtube_url(query: str, fetch=None) -> str:
    """First YouTube video for the query, else the results page. Never raises."""
    import urllib.parse

    results = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(
        query
    )
    try:
        if fetch is None:
            req = urllib.request.Request(
                results,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
                    ),
                    "Accept-Language": "en",
                },
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                html = resp.read(2_000_000).decode("utf-8", "replace")
        else:
            html = fetch(results)
        m = _YT_VIDEO_ID.search(html)
        if m:
            return f"https://www.youtube.com/watch?v={m.group(1)}"
    except Exception:
        pass
    return results


def _play_media(query: str) -> dict:
    """Open YouTube in Sir's Brave: first matching video, or the playlist."""
    query = (query or "").strip()[:200]
    url = _youtube_url(query) if query else _YT_PLAYLIST
    brave = _which("brave-browser") or _which("brave")
    argv = [brave, "--new-window", url] if brave else ["xdg-open", url]
    try:
        subprocess.Popen(
            argv,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return _tool_result(False, error=str(exc)[:200])
    return _tool_result(True, query=query, url=url)


_TYPE_MAX = 500
_TOOL_BODY_MAX = 65536
_TALK_AUDIO_MAX = 2 * 1024 * 1024
_CAM_KEEP = 50

# Phone-safe app launcher subset (argv only, detached like open_app).
# JARVIS_APP_MAP (JSON name->[argv]) extends or overrides it.
_APP_MAP = {
    "brave": ["brave-browser"],
    "files": ["dolphin"],
    "terminal": ["konsole"],
    "calculator": ["kcalc"],
    "whatsie": ["flatpak", "run", "com.ktechpit.whatsie"],
}

# RustDesk remote-session script (scripts/remote_session.sh next to src/).
_REMOTE_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "remote_session.sh"
)
_REMOTE_VERBS = {
    "remote_start": "start",
    "remote_stop": "stop",
    "remote_status": "status",
}


def _remote_host() -> str | None:
    """Laptop Tailscale IPv4 (first line of `tailscale ip -4`). None if absent."""
    rc, out, _ = _run(["tailscale", "ip", "-4"], 5.0)
    if rc != 0 or not out.strip():
        return None
    host = out.strip().splitlines()[0].strip()
    if not re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", host):
        return None
    return host


def _run_remote_tool(tool: str) -> dict:
    """Run scripts/remote_session.sh via _run; return parsed JSON + host."""
    verb = _REMOTE_VERBS[tool]
    _rc, out, err = _run([str(_REMOTE_SCRIPT), verb], 15.0)
    try:
        data = json.loads(out.strip() or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict) or "ok" not in data:
        return _tool_result(False, error=(err or out or "remote script failed")[:200])
    host = _remote_host()
    if host:
        data["host"] = host
    return data


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _actions_log_path() -> Path:
    override = _env("JARVIS_ACTIONS_LOG")
    if override:
        return Path(override)
    return Path.home() / ".jarvis" / "actions.log"


def _hud_room_path() -> Path:
    home = _env("JARVIS_HOME").strip()
    base = Path(home) if home else Path.home() / ".jarvis"
    return base / "hud_room"


def read_hud_room(max_age_s: float = 15 * 60) -> str | None:
    """Live wake room for HUD joiners, or None (no call / stale / bad).

    Pure-ish (one small file read). Mirrors wake_client.read_hud_room so
    the bridge stays dependency-free.
    """
    try:
        path = _hud_room_path()
        room = path.read_text().strip()
        if not room or len(room) > 64:
            return None
        if time.time() - path.stat().st_mtime > max_age_s:
            return None
        return room
    except OSError:
        return None


#: A wake stamp older than this is a summon that never connected.
HUD_WAKING_MAX_AGE_S = 15.0


def read_hud_waking(max_age_s: float = HUD_WAKING_MAX_AGE_S) -> bool:
    """True right after the wake word fired, before the call room exists.

    Mirrors wake_client.mark_hud_waking (file ~/.jarvis/hud_waking). Never
    raises.
    """
    try:
        path = _hud_room_path().with_name("hud_waking")
        return time.time() - path.stat().st_mtime <= max_age_s
    except OSError:
        return False


def read_hud_boot(max_age_s: float = HUD_WAKING_MAX_AGE_S) -> list | None:
    """The call-setup log [[stage, ts], ...] from the waking file while it
    is fresh, else None. Old plain-timestamp files read as None. Never
    raises."""
    try:
        path = _hud_room_path().with_name("hud_waking")
        if time.time() - path.stat().st_mtime > max_age_s:
            return None
        log = json.loads(path.read_text()).get("log")
        return log if isinstance(log, list) else None
    except (OSError, ValueError, AttributeError):
        return None


def read_captions(limit: int = 20) -> list[dict]:
    """Tail of ~/.jarvis/captions.log as [{ts, role, text}]. Never raises.

    Mirrors hud_events.read_captions so the bridge stays dependency-free.
    """
    try:
        home = _env("JARVIS_HOME").strip()
        base = Path(home) if home else Path.home() / ".jarvis"
        lines = (base / "captions.log").read_text().splitlines()[-max(1, limit) :]
    except OSError:
        return []
    out: list[dict] = []
    for line in lines:
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        try:
            ts = int(parts[0])
        except ValueError:
            continue
        out.append({"ts": ts, "role": parts[1], "text": parts[2]})
    return out


def read_live_caption(max_age_s: float = 120.0) -> dict | None:
    """Jarvis's word-synced live line ({id, text, ts, done}) or None when
    missing/stale/corrupt. Mirrors hud_events.live_caption. Never raises."""
    try:
        home = _env("JARVIS_HOME").strip()
        base = Path(home) if home else Path.home() / ".jarvis"
        data = json.loads((base / "caption_live.json").read_text())
        ts = float(data.get("ts", 0))
        if time.time() - ts > max_age_s:
            return None
        return {
            "id": str(data.get("id", "")),
            "text": str(data.get("text", "")),
            "ts": ts,
            "done": bool(data.get("done")),
        }
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def read_activity() -> list[dict]:
    """HUD EXECUTION feed: running + recently finished activities
    (src/activity.py). Never raises."""
    try:
        import activity

        return activity.list_items()
    except Exception:
        return []


def start_activity_watch() -> None:
    """Background producers for the activity feed (downloads, package
    updates, projects). The bridge is the always-on supervised sidecar, so
    it hosts them. JARVIS_ACTIVITY_WATCH=0 disables. Never raises."""
    if _env("JARVIS_ACTIVITY_WATCH", "1").strip().lower() in (
        "0",
        "false",
        "off",
        "no",
    ):
        return
    try:
        import activity_watch

        activity_watch.start_thread()
    except Exception as exc:
        print(f"activity watch unavailable: {exc}", flush=True)


BRAVE_ENTER_S = 0.6  # Brave focused this long -> orb (ignores alt-tab flicks)
BRAVE_EXIT_S = 1.5  # away from Brave this long -> back to the HUD
ORB_STARTUP_DELAY_S = 10.0  # let the shell finish booting first
ORB_REASSERT_S = 60.0  # re-send "orbon" this often while in Brave


def is_brave_window(info: dict | None) -> bool:
    """Active-window record is Brave? Pure."""
    if not info:
        return False
    app = str(info.get("app") or "").lower()
    return "brave" in app


def is_jarvis_window(info: dict | None) -> bool:
    """One of Jarvis's own windows (HUD / orb / strip)? Pure."""
    return bool(info) and "jarvis" in str(info.get("app") or "").lower()


def orb_step(
    state: dict, brave: bool | None, now: float, school_mode: bool
) -> str | None:
    """Debounced orb switch. Returns "orbon"/"orboff" when the shell must
    change, else None. brave=None (Jarvis's own window is active) holds the
    current state: showing the orb activated it, which read as "left Brave"
    and flapped the orb on/off every 2 s. Pure (mutates only `state`)."""
    if brave is None:
        brave = bool(state.get("orb"))
    want = brave and not school_mode
    if want != state.get("pending"):
        state["pending"], state["since"] = want, now
    hold = BRAVE_ENTER_S if want else BRAVE_EXIT_S
    if want != state.get("orb", False) and now - state.get("since", now) >= hold:
        state["orb"] = want
        return "orbon" if want else "orboff"
    return None


def _brave_orb_loop(stop) -> None:
    import active_window
    import projects as _projects
    import school as _school

    # The shell spawns the bridge while it is still booting: a verb sent
    # then never reached it (orb stayed off). Wait, then keep re-asserting
    # the current state now and then — orbon/orboff are idempotent.
    if stop.wait(ORB_STARTUP_DELAY_S):
        return
    state: dict = {"orb": False}
    last_sent = 0.0
    while not stop.wait(0.4):
        try:
            now = time.monotonic()
            info = active_window.active()
            brave = None if is_jarvis_window(info) else is_brave_window(info)
            verb = orb_step(state, brave, now, _school.is_school())
            if verb is None and state.get("orb") and now - last_sent >= ORB_REASSERT_S:
                verb = "orbon"
            if verb:
                last_sent = now
                _projects.shell_verb(verb)
                with contextlib.suppress(Exception):
                    from system import log_action

                    log_action("orb", verb)
        except Exception:
            time.sleep(2.0)


#: Background services the bridge hosts: (module, env kill switch).
SIDECAR_THREADS = (
    ("second_brain", "JARVIS_BRAIN"),
    ("telegram_bot", "JARVIS_TELEGRAM"),
    # Lease-based shared webcam: idle (camera closed, LED off) until
    # presence/eyes/gestures ask for frames.
    ("camera_hub", "JARVIS_CAMERA_HUB"),
    # Focus sessions survive agent restarts: the drift loop lives here.
    ("focus", "JARVIS_FOCUS"),
    # Email drafts + urgency triage every minute; WhatsApp replies every 5.
    ("mail_watch_job", "JARVIS_MAIL_WATCH"),
    ("wa_autoreply", "JARVIS_WA_WATCH"),
    # Queued notifications are announced once Sir is back at the keyboard.
    ("notify", "JARVIS_NOTIFY_QUEUE"),
)


def start_sidecar_threads() -> None:
    """Start each hosted service's start_thread(). A service whose kill
    switch is 0/off, or that fails to import, is skipped. Never raises."""
    import importlib

    for module, switch in SIDECAR_THREADS:
        if _env(switch, "1").strip().lower() in ("0", "false", "off", "no"):
            continue
        try:
            importlib.import_module(module).start_thread()
        except Exception as exc:
            print(f"{module} unavailable: {exc}", flush=True)


def start_brave_orb_watch() -> None:
    """Keep the user's HUD layout unchanged when switching to Brave.

    The orb remains available as an explicit shell command, but changing the
    active application must not resize, move, hide, or replace the HUD. Only
    Projects UI and School Mode are allowed to request a layout change.
    """
    return


def _wake_socket_path() -> Path:
    """Unix socket of the wake_client mic-control listener. Pure (env)."""
    home = _env("JARVIS_HOME").strip()
    base = Path(home) if home else Path.home() / ".jarvis"
    return base / "wake.sock"


def _wake_rpc(payload: dict, timeout: float = _WAKE_RPC_TIMEOUT) -> dict | None:
    """Send one JSON message to wake.sock, return its reply. Fail-soft.

    Returns None when the listener is absent, too slow, or answers
    garbage — callers degrade to {"ok": false} instead of raising.
    """
    try:
        raw = json.dumps(payload).encode()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as cli:
            cli.settimeout(timeout)
            cli.connect(str(_wake_socket_path()))
            cli.sendall(raw)
            chunks = []
            while True:
                data = cli.recv(4096)
                if not data:
                    break
                chunks.append(data)
                if len(b"".join(chunks)) > 65536:
                    return None
        reply = json.loads(b"".join(chunks).decode())
        return reply if isinstance(reply, dict) else None
    except Exception:
        return None


def _voice_model_present() -> bool:
    model = _env("JARVIS_VOICE_MODEL", "")
    if model:
        return Path(model.split(":")[0]).expanduser().exists()
    return (Path.home() / ".jarvis" / "voices").exists()


def _whatsapp_reachable(timeout: float = 1.0) -> bool | None:
    """Probe the WhatSie CDP endpoint. None = probe not possible."""
    try:
        req = urllib.request.Request("http://127.0.0.1:9223/json/version")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


# Last telemetry pushed by the phone (POST /phone/telemetry). In-memory:
# a bridge restart simply shows "no link" until the next push (~60s).
_PHONE_TELEMETRY: dict = {}
_PHONE_STALE_S = 10 * 60


def record_phone_telemetry(body: dict) -> dict | None:
    """Validate + store a phone telemetry push. Returns the stored dict."""
    battery = body.get("battery")
    if isinstance(battery, bool) or not isinstance(battery, (int, float)):
        return None
    if not 0 <= battery <= 100:
        return None
    _PHONE_TELEMETRY.clear()
    _PHONE_TELEMETRY.update(
        battery=int(battery),
        charging=body.get("charging") is True,
        ts=time.time(),
    )
    return dict(_PHONE_TELEMETRY)


def _phone_stats(now: float | None = None) -> dict | None:
    """Phone battery + link age, or None when never seen / stale. Pure-ish."""
    if not _PHONE_TELEMETRY:
        return None
    age = (now or time.time()) - _PHONE_TELEMETRY["ts"]
    if age > _PHONE_STALE_S:
        return None
    return {
        "battery": _PHONE_TELEMETRY["battery"],
        "charging": _PHONE_TELEMETRY["charging"],
        "age_s": int(age),
    }


# Tailnet presence: the phone app only pushes telemetry while it's open,
# so "no telemetry" != "phone offline". `tailscale status --json` knows
# whether the phone is actually reachable. Cached; the HUD polls often.
_TAILNET_CACHE: dict = {}
_TAILNET_TTL_S = 15.0


def _phone_peer(status: dict) -> dict | None:
    """Pick the phone out of `tailscale status --json`. Pure.

    JARVIS_PHONE_TAILNET_IP pins it; otherwise the first Android/iOS peer.
    """
    peers = list((status.get("Peer") or {}).values())
    pinned = os.environ.get("JARVIS_PHONE_TAILNET_IP", "").strip()
    if pinned:
        return next((p for p in peers if pinned in (p.get("TailscaleIPs") or [])), None)
    return next(
        (p for p in peers if str(p.get("OS", "")).lower() in ("android", "ios")), None
    )


def _phone_tailnet() -> dict | None:
    """{"online", "name"} for the phone on the tailnet, None if unknown."""
    now = time.monotonic()
    if _TAILNET_CACHE and now - _TAILNET_CACHE["at"] < _TAILNET_TTL_S:
        return _TAILNET_CACHE["value"]
    value: dict | None = None
    try:
        out = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        peer = _phone_peer(json.loads(out.stdout)) if out.returncode == 0 else None
        if peer is not None:
            value = {
                "online": peer.get("Online") is True,
                "name": peer.get("HostName", ""),
            }
    except (OSError, subprocess.SubprocessError, ValueError):
        value = None
    _TAILNET_CACHE.update(at=now, value=value)
    return value


def set_school_mode(mode: str) -> dict:
    """Switch school mode and tell the shell to re-layout. Never raises."""
    rec = _school.set_mode(mode)
    with contextlib.suppress(Exception):
        import projects as _projects

        _projects.shell_verb(
            "schoolon" if rec["mode"] == _school.SCHOOL else "schooloff"
        )
    # Meta-alone opens the Jarvis start menu in school mode, Kickoff
    # outside it (school.meta_* are fail-soft; never break the switch).
    with contextlib.suppress(Exception):
        if rec["mode"] == _school.SCHOOL:
            _school.meta_enter_school()
        else:
            _school.meta_exit_school()
    with contextlib.suppress(Exception):
        from system import log_action

        log_action("mode", rec["mode"])
    return {"mode": rec["mode"]}


def _running_research() -> list[dict]:
    """Live research jobs for the HUD progress strip. Never raises."""
    try:
        import projects as _projects

        return [
            {
                "id": m["id"],
                "title": m.get("title", "")[:80],
                "progress": int(m.get("progress") or 0),
                "stage": m.get("stage") or "Starting",
            }
            for m in _projects.list_projects()
            if m.get("kind") == "research" and m.get("status") == "running"
        ][:3]
    except Exception:
        return []


def _read_sys(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _laptop_power(root: Path = Path("/sys/class/power_supply")) -> dict | None:
    """Laptop battery %, state, AC and draw in watts from sysfs. Never raises."""
    bats = sorted(root.glob("BAT*"))
    if not bats:
        return None
    bat = bats[0]
    cap = _read_sys(bat / "capacity")
    out: dict = {
        "battery": int(cap) if cap and cap.isdigit() else None,
        "status": _read_sys(bat / "status") or "Unknown",
    }
    power = _read_sys(bat / "power_now")
    watts = None
    if power and power.lstrip("-").isdigit():
        watts = abs(int(power)) / 1e6
    else:
        cur, volt = _read_sys(bat / "current_now"), _read_sys(bat / "voltage_now")
        if cur and volt and cur.lstrip("-").isdigit() and volt.isdigit():
            watts = abs(int(cur)) * int(volt) / 1e12
    out["watts"] = round(watts, 1) if watts is not None else None
    for ac in [*root.glob("AC*"), *root.glob("ADP*")]:
        online = _read_sys(ac / "online")
        if online is not None:
            out["ac"] = online == "1"
            break
    return out


def _cpu_temp_c(root: Path = Path("/sys/class/thermal")) -> float | None:
    """CPU package temperature (TCPU / x86_pkg_temp preferred, else max)."""
    temps: dict[str, float] = {}
    for zone in root.glob("thermal_zone*"):
        kind, raw = _read_sys(zone / "type"), _read_sys(zone / "temp")
        if kind and raw and raw.lstrip("-").isdigit():
            temps[kind] = int(raw) / 1000
    for preferred in ("x86_pkg_temp", "TCPU", "cpu_thermal", "coretemp"):
        if preferred in temps:
            return round(temps[preferred], 1)
    return round(max(temps.values()), 1) if temps else None


def _mode_since(path: Path | None = None) -> float | None:
    """Epoch seconds the current school/normal mode started. None if unknown.

    Reads ~/.jarvis/mode.json key "since" (see school.mode_path()).
    Fail-soft: missing/corrupt/non-numeric -> None. Never raises.
    """
    try:
        p = path if path is not None else _school.mode_path()
        data = json.loads(p.read_text())
        since = float(data.get("since"))
        return since
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _split_escaped(value: str) -> list[str]:
    """Split nmcli -t output on unescaped colons. Pure.

    nmcli escapes literal colons as "\\:" (and backslashes as "\\\\"),
    so a naive split breaks SSIDs like "My:Home:Net".
    """
    parts: list[str] = []
    current: list[str] = []
    chars = value or ""
    i = 0
    while i < len(chars):
        c = chars[i]
        if c == "\\" and i + 1 < len(chars):
            current.append(chars[i + 1])
            i += 2
            continue
        if c == ":":
            parts.append("".join(current))
            current = []
        else:
            current.append(c)
        i += 1
    parts.append("".join(current))
    return parts


def _parse_nmcli_devices(out: str) -> list[tuple[str, str, str]]:
    """Parse `nmcli -t -f TYPE,STATE,CONNECTION device`. Pure.

    Returns [(type, state, connection)] lowercased type/state, unescaped
    connection ("" when nmcli reports "--").
    """
    rows: list[tuple[str, str, str]] = []
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = _split_escaped(line)
        if len(parts) < 2:
            continue
        dtype = parts[0].strip().lower()
        state = parts[1].strip().lower()
        conn = ":".join(parts[2:]).strip() if len(parts) > 2 else ""
        if conn == "--":
            conn = ""
        rows.append((dtype, state, conn))
    return rows


def _parse_nmcli_wifi(out: str) -> tuple[str, int | None] | None:
    """Parse `nmcli -t -f ACTIVE,SSID,SIGNAL dev wifi`. Pure.

    Returns (ssid, signal) for the ACTIVE=yes line, or None when no
    active network is listed. Signal outside 0-100 degrades to None.
    """
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = _split_escaped(line)
        if len(parts) < 2:
            continue
        if parts[0].strip().lower() != "yes":
            continue
        ssid = ":".join(parts[1:-1]).strip() if len(parts) > 2 else ""
        signal: int | None = None
        try:
            signal = int(parts[-1].strip())
        except (ValueError, TypeError):
            signal = None
        if signal is not None and not 0 <= signal <= 100:
            signal = None
        return (ssid, signal)
    return None


_NET_CACHE: dict = {}
_NET_TTL_S = 5.0
_VOL_CACHE: dict = {}
_VOL_TTL_S = 5.0


def _net_status(run=None, clock=None) -> dict | None:
    """Link kind/name/signal for the taskbar. None when nmcli is unusable.

    wifi -> SSID + signal from `dev wifi`; ethernet -> connection name;
    nothing connected -> {"kind": "none", ...}. Results cached 5s
    (monotonic clock). `run` defaults to subprocess.run so tests can
    inject a fake; `clock` defaults to time.monotonic. Never raises.
    """
    now_fn = clock or time.monotonic
    try:
        now = now_fn()
    except Exception:
        now = time.monotonic()
    try:
        if _NET_CACHE and now - _NET_CACHE["at"] < _NET_TTL_S:
            return _NET_CACHE["value"]
    except (KeyError, TypeError):
        pass
    runner = run or subprocess.run
    value: dict | None = None
    try:
        proc = runner(
            ["nmcli", "-t", "-f", "TYPE,STATE,CONNECTION", "device"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if proc.returncode != 0:
            raise ValueError("nmcli device failed")
        rows = _parse_nmcli_devices(proc.stdout or "")
        wifi = [r for r in rows if r[0] == "wifi" and r[1] == "connected"]
        eth = [r for r in rows if r[0] == "ethernet" and r[1] == "connected"]
        if wifi:
            fallback = wifi[0][2]
            name, signal = fallback, None
            try:
                wproc = runner(
                    ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if wproc.returncode == 0:
                    hit = _parse_nmcli_wifi(wproc.stdout or "")
                    if hit is not None:
                        name, signal = hit[0] or fallback, hit[1]
            except Exception:
                pass
            value = {"kind": "wifi", "name": name, "signal": signal}
        elif eth:
            value = {"kind": "ethernet", "name": eth[0][2], "signal": None}
        else:
            value = {"kind": "none", "name": "", "signal": None}
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        value = None
    except Exception:
        value = None
    _NET_CACHE.update(at=now, value=value)
    return value


def _parse_wpctl_volume(out: str) -> dict | None:
    """Parse `wpctl get-volume` ("Volume: 0.45" / "... [MUTED]"). Pure."""
    match = re.search(r"Volume:\s*([0-9]*\.?[0-9]+)", out or "")
    if not match:
        return None
    try:
        pct = round(float(match.group(1)) * 100)
    except ValueError:
        return None
    return {"pct": max(0, min(150, pct)), "muted": "muted" in (out or "").lower()}


def _volume_status(run=None, clock=None) -> dict | None:
    """Sink volume pct (0-150) + muted. None when no backend works.

    Tries `wpctl get-volume`, falls back to pactl volume + mute.
    Results cached 5s. Injectable run/clock like _net_status. Never raises.
    """
    now_fn = clock or time.monotonic
    try:
        now = now_fn()
    except Exception:
        now = time.monotonic()
    try:
        if _VOL_CACHE and now - _VOL_CACHE["at"] < _VOL_TTL_S:
            return _VOL_CACHE["value"]
    except (KeyError, TypeError):
        pass
    runner = run or subprocess.run
    value: dict | None = None
    try:
        proc = runner(
            ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if proc.returncode == 0:
            value = _parse_wpctl_volume(proc.stdout or "")
    except (OSError, subprocess.SubprocessError, AttributeError):
        value = None
    except Exception:
        value = None
    if value is None:
        try:
            vproc = runner(
                ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if vproc.returncode == 0:
                pct = _parse_volume_pct(vproc.stdout or "")
                if pct is not None:
                    muted = False
                    try:
                        mproc = runner(
                            ["pactl", "get-sink-mute", "@DEFAULT_SINK@"],
                            capture_output=True,
                            text=True,
                            timeout=2,
                        )
                        if mproc.returncode == 0:
                            muted = "yes" in (mproc.stdout or "").lower()
                    except Exception:
                        muted = False
                    value = {"pct": pct, "muted": muted}
        except (OSError, subprocess.SubprocessError, AttributeError):
            value = None
        except Exception:
            value = None
    _VOL_CACHE.update(at=now, value=value)
    return value


#: Cap for an app icon file served over /appicon (bigger files are skipped).
_APPICON_MAX_BYTES = 200 * 1024
#: Raster pixmaps (e.g. a 1024px chatgpt.png) may be this big on disk; they
#: are shrunk to _APPICON_SHRINK_PX before the data URL is built.
_APPICON_RASTER_EXTS = (".png", ".jpg", ".jpeg")
_APPICON_RASTER_MAX_BYTES = 8 * 1024 * 1024
_APPICON_SHRINK_PX = 128
#: Per-app icon data-URL cache (misses cached as None).
_APPICON_CACHE: dict[str, str | None] = {}
#: Theme fallback chain when kdeglobals names nothing usable.
_APPICON_THEMES = ("breeze-dark", "breeze", "Papirus-Dark", "hicolor")


def _app_search_dirs(roots=None) -> list[Path]:
    """Dirs holding .desktop files. `roots` injects a fake tree (tests)."""
    if roots is not None:
        return [Path(r) for r in roots]
    home = Path.home()
    return [
        home / ".local/share/applications",
        Path("/usr/share/applications"),
        Path("/var/lib/flatpak/exports/share/applications"),
        home / ".local/share/flatpak/exports/share/applications",
    ]


def _icon_search_dirs(roots=None) -> list[Path]:
    """Icon theme roots. `roots` injects a fake tree (tests)."""
    if roots is not None:
        return [Path(r) for r in roots]
    home = Path.home()
    return [
        home / ".local/share/icons",
        Path("/usr/share/icons"),
        Path("/var/lib/flatpak/exports/share/icons"),
        home / ".local/share/flatpak/exports/share/icons",
    ]


def _pixmap_dirs(roots=None) -> list[Path]:
    """Fallback loose-icon dirs. `roots` injects a fake tree (tests)."""
    if roots is not None:
        return [Path(r) for r in roots]
    return [Path("/usr/share/pixmaps")]


def _current_icon_theme(kdeglobals=None) -> str:
    """Theme from ~/.config/kdeglobals [Icons] Theme= (fallback breeze-dark).

    `kdeglobals` injects a fake file (tests). Never raises.
    """
    try:
        path = Path(kdeglobals) if kdeglobals is not None else Path.home() / ".config/kdeglobals"
        text = path.read_text()
    except (OSError, ValueError):
        return "breeze-dark"
    try:
        section, theme = "", ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1].strip().lower()
            elif section == "icons" and stripped.lower().startswith("theme"):
                _, _, value = stripped.partition("=")
                theme = value.strip().strip("\"'")
                if theme:
                    break
        return theme or "breeze-dark"
    except Exception:
        return "breeze-dark"


def _icon_theme_chain(theme=None, kdeglobals=None) -> list[str]:
    """Current theme first, then the fallbacks, de-duplicated. Pure."""
    try:
        first = theme or _current_icon_theme(kdeglobals=kdeglobals)
    except Exception:
        first = "breeze-dark"
    chain = [first, *_APPICON_THEMES]
    seen: list[str] = []
    for name in chain:
        if name and name not in seen:
            seen.append(name)
    return seen


def _desktop_value(path: Path, key: str) -> str:
    """First `key=` value in a .desktop file ("" when absent). Never raises."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                name, sep, value = stripped.partition("=")
                if sep and name.strip() == key:
                    return value.strip()
    except (OSError, ValueError):
        pass
    except Exception:
        pass
    return ""


def find_desktop_file(app: str, app_dirs=None) -> Path | None:
    """Locate <app>.desktop: exact, case-insensitive, StartupWMClass match.

    `app` may be a desktop id (with/without .desktop) or a resourceClass.
    `app_dirs` injects fake roots (tests). Never raises.
    """
    try:
        want = (app or "").strip()
        if not want:
            return None
        dirs = _app_search_dirs(app_dirs)
        base = want[:-8] if want.lower().endswith(".desktop") else want
        for directory in dirs:
            try:
                candidate = directory / (want if want.lower().endswith(".desktop") else f"{want}.desktop")
                if candidate.is_file():
                    return candidate
            except (OSError, ValueError):
                continue
        lowered = f"{base}.desktop".lower()
        for directory in dirs:
            try:
                names = sorted(p.name for p in directory.glob("*.desktop") if p.is_file())
            except (OSError, ValueError):
                continue
            for name in names:
                if name.lower() == lowered:
                    return directory / name
        match = base.lower()
        for directory in dirs:
            try:
                names = sorted(p.name for p in directory.glob("*.desktop") if p.is_file())
            except (OSError, ValueError):
                continue
            for name in names:
                if _desktop_value(directory / name, "StartupWMClass").lower() == match:
                    return directory / name
    except Exception:
        pass
    return None


def _mime_for_icon(path: Path) -> str:
    """MIME for an icon file by suffix. Pure (name only)."""
    suffix = path.suffix.lower()
    if suffix in (".svg", ".svgz"):
        return "image/svg+xml"
    if suffix == ".png":
        return "image/png"
    if suffix in (".jpg", ".jpeg"):
        return "image/jpeg"
    if suffix == ".xpm":
        return "image/x-xpm"
    if suffix == ".gif":
        return "image/gif"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".ico":
        return "image/x-icon"
    return "application/octet-stream"


def _icon_candidate_ok(path: Path) -> bool:
    """A usable icon file: exists and within the size cap (big PNG/JPEG
    pixmaps pass up to a higher cap; they get shrunk). Never raises."""
    try:
        if not path.is_file():
            return False
        size = path.stat().st_size
        if path.suffix.lower() in _APPICON_RASTER_EXTS:
            return size <= _APPICON_RASTER_MAX_BYTES
        return size <= _APPICON_MAX_BYTES
    except (OSError, ValueError):
        return False
    except Exception:
        return False


def find_icon_path(icon: str, theme=None, icon_dirs=None, pixmap_dirs=None, kdeglobals=None) -> Path | None:
    """Resolve an Icon= value to a file: absolute path, theme, pixmaps.

    Theme order per dir: scalable/apps/*.svg, then 48/64/32 layouts in
    both 48x48/apps and apps/48 shapes, then an apps/** fallback preferring
    48px. Files over the size cap are skipped. Never raises.
    """
    try:
        name = (icon or "").strip()
        if not name:
            return None
        direct = Path(name)
        if direct.is_absolute():
            return direct if _icon_candidate_ok(direct) else None
        exts = (".svg", ".png", ".svgz", ".xpm")
        themes = _icon_theme_chain(theme, kdeglobals=kdeglobals)
        roots = _icon_search_dirs(icon_dirs)
        for theme_name in themes:
            for root in roots:
                base = root / theme_name
                hit = base / "scalable" / "apps" / f"{name}.svg"
                if _icon_candidate_ok(hit):
                    return hit
                for size in ("48x48", "64x64", "32x32"):
                    for ext in exts:
                        hit = base / size / "apps" / f"{name}{ext}"
                        if _icon_candidate_ok(hit):
                            return hit
                for size in ("48", "64", "32", "scalable"):
                    for ext in exts:
                        hit = base / "apps" / size / f"{name}{ext}"
                        if _icon_candidate_ok(hit):
                            return hit
                try:
                    apps_dir = base / "apps"
                    if apps_dir.is_dir():
                        loose = sorted(
                            (p for p in apps_dir.rglob(f"{name}.*") if p.is_file()),
                            key=lambda p: str(p),
                        )
                        preferred = sorted(
                            loose,
                            key=lambda p: (
                                0 if "48" in p.parts else 1 if "64" in p.parts else 2 if "32" in p.parts else 3
                            ),
                        )
                        for hit in preferred:
                            if _icon_candidate_ok(hit):
                                return hit
                except (OSError, ValueError):
                    pass
                except Exception:
                    pass
        for root in _pixmap_dirs(pixmap_dirs):
            for ext in ("", *exts, ".jpg", ".jpeg"):
                hit = root / f"{name}{ext}"
                if _icon_candidate_ok(hit):
                    return hit
    except Exception:
        pass
    return None


def _shrink_raster_icon(raw: bytes) -> bytes:
    """PNG bytes of `raw` scaled to fit _APPICON_SHRINK_PX. b"" when Pillow
    is missing or the image won't decode. Never raises."""
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(raw)) as im:
            im = im.convert("RGBA")
            im.thumbnail((_APPICON_SHRINK_PX, _APPICON_SHRINK_PX), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, format="PNG", optimize=True)
            return out.getvalue()
    except Exception:
        return b""


def app_icon_data_url(app: str, app_dirs=None, icon_dirs=None, pixmap_dirs=None, kdeglobals=None, theme=None) -> str | None:
    """<app> -> base64 data URL for its icon, or None on any miss.

    Results (including misses) are cached per app for the default roots;
    injectable roots bypass the cache (tests). Never raises.
    """
    try:
        key = (app or "").strip().lower()
        if not key:
            return None
        custom = (
            app_dirs is not None
            or icon_dirs is not None
            or pixmap_dirs is not None
            or kdeglobals is not None
            or theme is not None
        )
        if not custom and key in _APPICON_CACHE:
            return _APPICON_CACHE[key]
        result: str | None = None
        desktop = find_desktop_file(key, app_dirs=app_dirs)
        icon = _desktop_value(desktop, "Icon") if desktop is not None else ""
        if not icon:
            icon = key
        path = find_icon_path(
            icon, theme=theme, icon_dirs=icon_dirs, pixmap_dirs=pixmap_dirs, kdeglobals=kdeglobals
        )
        if path is not None:
            try:
                raw = path.read_bytes()
                mime = _mime_for_icon(path)
                if len(raw) > _APPICON_MAX_BYTES and path.suffix.lower() in _APPICON_RASTER_EXTS:
                    raw, mime = _shrink_raster_icon(raw), "image/png"
                if raw and len(raw) <= _APPICON_MAX_BYTES:
                    result = f"data:{mime};base64,{base64.b64encode(raw).decode()}"
            except (OSError, ValueError):
                result = None
            except Exception:
                result = None
        if not custom:
            _APPICON_CACHE[key] = result
        return result
    except Exception:
        return None


def _sys_stats() -> dict:
    stats: dict = {}
    # Real HUD telemetry: phone battery ("suit power"), laptop power
    # ("arc reactor"), CPU temp, and whether a voice call is live.
    stats["phone"] = _phone_stats()
    stats["phone_tailnet"] = _phone_tailnet()
    stats["research"] = _running_research()
    stats["mode"] = _school.current()
    stats["mode_since"] = _mode_since()
    stats["net"] = _net_status()
    stats["volume"] = _volume_status()
    stats["laptop_power"] = _laptop_power()
    stats["cpu_temp_c"] = _cpu_temp_c()
    stats["call_live"] = read_hud_room() is not None
    try:
        import taskbar

        stats["launchers"] = taskbar.launchers()
    except Exception:
        stats["launchers"] = []
    try:
        import active_window

        # Tests must never steal the live bridge's D-Bus name or unload
        # its KWin watcher (see tests/conftest.py): skip the listener
        # under pytest (PYTEST_CURRENT_TEST is set for every test).
        if os.environ.get("PYTEST_CURRENT_TEST") is None:
            with contextlib.suppress(Exception):
                active_window.ensure_listener()
        stats["windows"] = active_window.windows()
    except Exception:
        stats["windows"] = []
    try:
        with open("/proc/loadavg") as fh:
            stats["load_1_5_15"] = fh.read().split()[:3]
        # Core count lets gauges normalize load (load-per-core) and size
        # CPU speedometers without a second sampling pass.
        stats["cpu_count"] = os.cpu_count() or 1
        mem: dict = {}
        with open("/proc/meminfo") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[0].rstrip(":") in (
                    "MemTotal",
                    "MemAvailable",
                ):
                    mem[parts[0].rstrip(":")] = int(parts[1]) * 1024
        stats["mem_bytes"] = mem
        du = os.statvfs(str(Path.home()))
        stats["home_free_bytes"] = du.f_bavail * du.f_frsize
    except OSError:
        stats["unsupported"] = True
    return stats


def _tail_log(path: Path, limit: int) -> list[str]:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            step, buf = 4096, b""
            while len(buf.splitlines()) <= limit and size > 0:
                step = min(step, size)
                size -= step
                fh.seek(size)
                buf = fh.read(step) + buf
                if size == 0:
                    break
        lines = buf.decode(errors="replace").splitlines()
    except (OSError, ValueError):
        return []
    return lines[-limit:]


def _log_stats() -> dict:
    path = _actions_log_path()
    try:
        st = path.stat()
        return {"path": str(path), "bytes": st.st_size, "mtime": int(st.st_mtime)}
    except OSError:
        return {"path": str(path), "bytes": 0, "mtime": 0}


def build_status() -> dict:
    return {
        "ok": True,
        "version": VERSION,
        "uptime_s": int(time.time() - _STARTED_AT),
        "pipeline": _env("JARVIS_PIPELINE", "realtime"),
        "agent_name": _env("AGENT_NAME", "my-agent"),
        "local_unlocked": _env("JARVIS_LOCAL") == "1",
        "livekit_configured": bool(
            _env("LIVEKIT_URL")
            and _env("LIVEKIT_API_KEY")
            and _env("LIVEKIT_API_SECRET")
        ),
        "needle_enabled": _env("JARVIS_NEEDLE", "1") != "0",
        "wake_threshold": _env("JARVIS_WAKE_THRESHOLD", "0.5"),
        "voice_model_present": _voice_model_present(),
        "whatsapp_reachable": _whatsapp_reachable(),
        "log": _log_stats(),
    }


def resolve_bind() -> tuple[str, bool]:
    """(host, token_required). Non-loopback binds demand a token.

    Pure: JARVIS_BRIDGE_BIND (default 127.0.0.1). Anything other than
    127.0.0.1/localhost fails closed without JARVIS_BRIDGE_TOKEN.
    """
    host = _env("JARVIS_BRIDGE_BIND", "127.0.0.1").strip() or "127.0.0.1"
    public = host not in ("127.0.0.1", "localhost", "::1")
    return host, public


def _run(argv: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
    """Run an argv (no shell). Returns (rc, stdout, stderr), never raises."""
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "", f"command not found: {argv[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s: {argv[0]}"
    except OSError as exc:
        return 1, "", str(exc)[:200]
    decode = lambda b: b.decode(errors="replace").strip() if b else ""  # noqa: E731
    return proc.returncode, decode(proc.stdout), decode(proc.stderr)


def _which(name: str) -> str | None:
    return shutil.which(name)


def _read_json_body(handler: BaseHTTPRequestHandler, limit: int) -> dict | None:
    """Read + parse a JSON object body. None = missing/invalid/too big."""
    try:
        length = int(handler.headers.get("Content-Length", "0") or "0")
    except ValueError:
        return None
    if length <= 0 or length > limit:
        return None
    try:
        body = json.loads(handler.rfile.read(length).decode() or "{}")
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _launch_anything(name: str) -> dict:
    """Universal launcher for names outside the fixed map. Never raises.

    Installed app -> visited site -> closest app -> top web result, with
    the local router model arbitrating ambiguity (system/launcher.py).
    """
    if not name or len(name) > 60:
        return _tool_result(False, error="which app?")
    try:
        from system.launcher import resolve_launch

        try:
            known = __import__("tools").KNOWN_SITES
        except Exception:
            known = None
        decision = resolve_launch(name, known_sites=known)
    except Exception as exc:
        return _tool_result(False, error=f"launcher failed: {str(exc)[:120]}")
    from system.launcher import blocked_say, is_blocked

    if blocked := is_blocked(name, decision.target, *decision.argv):
        return _tool_result(False, error=blocked_say(blocked))
    if decision.kind == "app" and decision.argv:
        argv = list(decision.argv)
    else:
        if not re.match(r"^https?://[A-Za-z0-9]", decision.target):
            return _tool_result(False, error="unsafe url")
        argv = ["xdg-open", decision.target]
    try:
        subprocess.Popen(
            argv,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return _tool_result(False, error=str(exc)[:200])
    return _tool_result(True, app=decision.target, via=decision.reason)


def _app_map() -> dict[str, list[str]]:
    try:
        extra = json.loads(_env("JARVIS_APP_MAP", "") or "{}")
        merged = dict(_APP_MAP)
        for name, argv in extra.items():
            if (
                isinstance(argv, list)
                and argv
                and all(isinstance(a, str) for a in argv)
            ):
                merged[str(name)] = argv
        return merged
    except ValueError:
        return dict(_APP_MAP)


def _parse_volume_pct(out: str) -> int | None:
    """Parse `pactl get-sink-volume` ("... / 120%") to 0-150. Pure.

    Sinks allow over-amplification past 100%, so the clamp is 150 to
    match volume_set — clamping lower made honest read-backs lie.
    """
    match = re.search(r"(\d{1,3})\s*%", out or "")
    if not match:
        return None
    return max(0, min(150, int(match.group(1))))


def _parse_kscreen_outputs(out: str) -> list[dict]:
    """Parse `kscreen-doctor -o` into [{id, name, enabled, geometry}]. Pure.

    Lines look like: `Output: 1 HDMI-A-2 <uuid>` followed by a line
    containing `enabled` or `disabled`, plus `Geometry: X,Y WxH`.
    ANSI colors are stripped. Geometry is {"x","y","w","h"} or absent.
    """
    clean = re.sub(r"\x1b\[[0-9;]*m", "", out or "")
    outputs: list[dict] = []
    current: dict | None = None
    for line in clean.splitlines():
        head = re.match(r"Output:\s*(\d+)\s+(\S+)", line.strip())
        if head:
            current = {"id": head.group(1), "name": head.group(2), "enabled": True}
            outputs.append(current)
            continue
        if current is None:
            continue
        if "disabled" in line:
            current["enabled"] = False
        geo = re.search(r"Geometry:\s*(\d+),(\d+)\s+(\d+)x(\d+)", line)
        if geo:
            current["geometry"] = {
                "x": int(geo.group(1)),
                "y": int(geo.group(2)),
                "w": int(geo.group(3)),
                "h": int(geo.group(4)),
            }
    return outputs


def _screens_off_path() -> Path:
    home = _env("JARVIS_HOME").strip()
    base = Path(home) if home else Path.home() / ".jarvis"
    return base / "screens_off.json"


def _is_usage_error(exc: Exception) -> bool:
    """True for quota/rate/overload failures worth retrying on a backup.

    Pure: matches 429/5xx status codes and quota/overload/timeout
    wording. Auth (401/403), bad-request (400) and missing-model (404)
    fail fast — no backup would answer those either.
    """
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    try:
        code = int(code) if code is not None else 0
    except (TypeError, ValueError):
        code = 0
    if code == 429 or 500 <= code <= 599:
        return True
    text = f"{type(exc).__name__} {exc}".lower()
    return any(
        marker in text
        for marker in (
            "resource_exhausted",
            "quota",
            "rate limit",
            "rate_limit",
            "ratelimit",
            "overload",
            "too many requests",
            "timeout",
            "timed out",
            "deadline exceeded",
            "temporarily unavailable",
            "service unavailable",
        )
    )


def _gemini_reply(transcript: str, guest: bool = False) -> tuple[str, str | None]:
    """Direct Gemini reply with high-usage backups. Returns (reply, warning).

    Tries GEMINI_TEXT_MODEL then GEMINI_TEXT_FALLBACKS, moving on only
    on usage errors (429/quota/5xx/timeout). Anything else (auth, bad
    request) fails fast with the first error. Lazy import. Guest mode
    answers cold, curt and faintly contemptuous, one short sentence.
    """
    from quotes import SUIT_RULE

    persona = (
        "You are Jarvis in guest mode: cold, curt, faintly contemptuous. One "
        "short spoken sentence, no formatting, no warmth. Answer simple "
        "questions only; refuse anything else with a frosty no."
        if guest
        else "You are Jarvis, a terse British butler voice assistant. Reply in "
        "one or two short spoken sentences, no formatting. " + SUIT_RULE
    )
    try:
        from google import genai
    except ImportError:
        return _fallback_chat(transcript, persona, "voice stack missing (google-genai)")
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        return _fallback_chat(transcript, persona, "GOOGLE_API_KEY not configured")
    if guest:
        brief = (
            "You are Jarvis in guest mode: cold, curt, faintly contemptuous. "
            "One short spoken sentence, no formatting, no warmth. Answer "
            "simple questions only; refuse anything else with a frosty no. "
            f"Guest said: {transcript}"
        )
    else:
        brief = (
            "You are Jarvis, a terse British butler voice assistant. "
            "Reply in one or two short spoken sentences, no formatting. "
            f"User said: {transcript}"
        )
    # No custom http_options: SDK 2.x mis-handles a timeout dict
    # (instant ReadTimeout on every call) — defaults work.
    client = genai.Client(api_key=api_key)
    chain = [GEMINI_TEXT_MODEL, *GEMINI_TEXT_FALLBACKS]
    seen = set()
    models = [m for m in chain if not (m in seen or seen.add(m))]
    last_err: Exception | None = None
    for model_id in models:
        try:
            return (
                client.models.generate_content(
                    model=model_id,
                    contents=brief,
                ).text.strip(),
                None,
            )
        except Exception as exc:
            last_err = exc
            if not _is_usage_error(exc):
                return "", f"LLM unavailable: {last_err}"[:200]
    # Every Gemini text model is out of quota: Ling 3.0 Flash via OpenRouter
    # (free tier) keeps chat alive.
    return _fallback_chat(transcript, persona, f"LLM unavailable: {last_err}"[:200])


def _fallback_chat(
    transcript: str, persona: str, warning: str
) -> tuple[str, str | None]:
    """Ling 3.0 Flash via OpenRouter; the original warning if it
    fails too. Never raises."""
    try:
        import openrouter_chat

        reply, _ = openrouter_chat.chat_reply(
            transcript,
            system=persona,
            timeout=45.0,
        )
    except Exception:
        reply = ""
    return (reply, None) if reply else ("", warning)


def _tool_result(ok: bool, **fields) -> dict:
    return {"ok": ok, **fields}


def run_phone_tool(tool: str, args: dict) -> dict:
    """Execute one allowlisted phone-control tool. No shell, argv only."""
    if not isinstance(args, dict):
        args = {}
    if tool == "volume_get":
        rc, out, err = _run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], 5.0)
        _, mute_out, _ = _run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"], 5.0)
        if rc != 0:
            return _tool_result(False, error=err or "pactl failed")
        return _tool_result(
            True,
            volume=_parse_volume_pct(out),
            muted="yes" in mute_out.lower(),
        )
    if tool in ("volume_up", "volume_down"):
        if _which("pactl") is None:
            return _tool_result(False, error="pactl not installed")
        delta = "+5%" if tool == "volume_up" else "-5%"
        rc, _, err = _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", delta], 5.0)
        return _tool_result(rc == 0, error=None if rc == 0 else err)
    if tool == "volume_set":
        # Absolute level 0..150 (pactl allows >100% boost). Always
        # verifies by reading back: the reply reports the real level,
        # never the requested one.
        if _which("pactl") is None:
            return _tool_result(False, error="pactl not installed")
        try:
            level = int(args.get("level", -1))
        except (TypeError, ValueError):
            return _tool_result(False, error="level must be 0..150")
        if not 0 <= level <= 150:
            return _tool_result(False, error="level must be 0..150")
        rc, _, err = _run(
            ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"], 5.0
        )
        if rc != 0:
            return _tool_result(False, error=err or "pactl failed")
        return run_phone_tool("volume_get", {})
    if tool in ("volume_mute", "volume_unmute"):
        toggle = "1" if tool == "volume_mute" else "0"
        rc, _, err = _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", toggle], 5.0)
        return _tool_result(rc == 0, error=None if rc == 0 else err)
    if tool in ("media_play_pause", "media_next", "media_prev"):
        action = {
            "media_play_pause": "play-pause",
            "media_next": "next",
            "media_prev": "previous",
        }[tool]
        rc, out, err = _run(["playerctl", action], 5.0)
        return _tool_result(rc == 0, state=out or None, error=None if rc == 0 else err)
    if tool == "play_media":
        return _play_media(str(args.get("query", "")))
    if tool == "close_app":
        from system.closer import close_target

        out = close_target(str(args.get("name", ""))[:80])
        if not out.get("ok"):
            return _tool_result(False, error=out.get("say"))
        return _tool_result(True, say=out.get("say"), closed=out.get("closed"))
    if tool == "projects_ui":
        import projects as _projects

        cmds = args.get("commands")
        if not isinstance(cmds, list) or not cmds:
            return _tool_result(False, error="no commands")
        out = _projects.execute_voice(cmds, heard=str(args.get("heard", ""))[:200])
        if not out.get("ok"):
            missing = next((c for c in cmds if c.get("action") == "missing"), None)
            return _tool_result(
                False,
                error=(
                    f"no project called {missing.get('query')}"
                    if missing
                    else "the project archive didn't respond"
                ),
            )
        return _tool_result(True)
    if tool == "drafts_ui":
        import drafts_ui as _drafts

        op = args.get("op")
        if op == "open":
            say, opened = _drafts.open_drafts()
            if not opened and say.startswith("No drafts"):
                return _tool_result(True, say=say, opened=False)
            if opened:
                return _tool_result(True, say=say, opened=True)
            return _tool_result(False, error="the drafts window didn't respond")
        if op == "close":
            if _drafts.close_drafts():
                return _tool_result(True, say="Drafts closed.")
            return _tool_result(False, error="the drafts window didn't respond")
        return _tool_result(False, error="op must be open or close")
    if tool == "open_app":
        name = str(args.get("app", "")).strip().lower().rstrip(".,!? ")
        from system.launcher import blocked_say, is_blocked

        if blocked := is_blocked(name):
            return _tool_result(False, error=blocked_say(blocked))
        try:
            from system.launcher import priority_app

            pri = priority_app(name)
        except Exception:
            pri = None
        if pri is not None and not pri.argv:
            # Single-instance app already up: never spawn a duplicate.
            return _tool_result(True, app=pri.target, state="already running")
        if pri is not None and pri.argv:
            try:
                subprocess.Popen(  # argv from Sir's priority table / .desktop
                    pri.argv,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                return _tool_result(False, error=str(exc)[:200])
            return _tool_result(True, app=pri.target)
        entry = _app_map().get(name)
        if entry is None:
            return _launch_anything(name)
        if _which(entry[0]) is None and entry[0] != "flatpak":
            return _tool_result(False, error=f"{entry[0]} not installed")
        try:
            subprocess.Popen(  # argv from fixed map only, never user input
                entry,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return _tool_result(False, error=str(exc)[:200])
        return _tool_result(True, app=name)
    if tool == "screenshot":
        # args.output: "all"/"" = whole desktop; otherwise an output id
        # or name from screens_state ("HDMI-A-2"). Spectacle can't shoot
        # one output non-interactively, so shoot fullscreen once and crop
        # to the output's kscreen geometry with Pillow.
        want = str(args.get("output", "") or "").strip()
        region = None
        if want and want.lower() != "all":
            rc, out, err = _run(["kscreen-doctor", "-o"], 10.0)
            if rc != 0:
                return _tool_result(False, error=err or "kscreen-doctor failed")
            outs = _parse_kscreen_outputs(out)
            hit = [
                o for o in outs if o["name"].lower() == want.lower() or o["id"] == want
            ]
            if not hit:
                return _tool_result(
                    False,
                    error=f"no such output {want!r} (have {[o['name'] for o in outs]})",
                )
            region = hit[0].get("geometry")
            if not region:
                return _tool_result(False, error=f"no geometry for {want!r}")
        shot = _which("spectacle") or _which("import")
        if shot is None:
            return _tool_result(False, error="need spectacle or imagemagick")
        path = Path(f"/tmp/jarvis-phone-shot-{int(time.time())}.png")
        if shot.endswith("spectacle"):
            rc, _, err = _run(["spectacle", "-b", "-n", "-o", str(path)], 15.0)
        else:
            rc, _, err = _run(["import", "-window", "root", str(path)], 15.0)
        if rc != 0 or not path.is_file():
            return _tool_result(False, error=err or "capture failed")
        try:
            raw = path.read_bytes()
            if region:
                try:
                    from PIL import Image

                    with Image.open(path) as img:
                        img = img.convert("RGB")
                        left = max(0, region["x"])
                        top = max(0, region["y"])
                        box = (
                            left,
                            top,
                            min(img.width, left + region["w"]),
                            min(img.height, top + region["h"]),
                        )
                        crop = img.crop(box)
                        out_path = path.with_name(path.stem + "-crop.png")
                        crop.save(out_path, "PNG")
                        raw = out_path.read_bytes()
                        out_path.unlink(missing_ok=True)
                except ImportError:
                    return _tool_result(False, error="Pillow missing for crop")
                except Exception as exc:
                    return _tool_result(False, error=f"crop failed: {exc}"[:150])
            image_b64 = base64.b64encode(raw).decode()
        finally:
            path.unlink(missing_ok=True)
        result = _tool_result(True, image_b64=image_b64, format="png")
        if region:
            result["output"] = want
        return result
    if tool == "school_mode":
        return _tool_result(True, **set_school_mode(str(args.get("mode", ""))))
    if tool == "watch_mode":
        import watch_mode as _watch

        got = dict(_watch.launch())
        return _tool_result(bool(got.pop("ok", False)), **got)
    if tool == "notify":
        title = str(args.get("title", "Jarvis phone"))[:120]
        body = str(args.get("body", ""))[:300]
        try:
            import notify

            result = notify.send(
                body,
                title=title,
                kind="phone",
                source="phone",
                allow_speech=False,
            )
            route = result.get("route")
            ok = route != "empty"
            return _tool_result(ok, error=None if ok else f"route={route}")
        except Exception as exc:
            return _tool_result(False, error=str(exc)[:200])
    if tool == "lock":
        # lock-sessions (plural, all) — bare lock-session locks only the
        # caller's session (the headless manager session under systemd,
        # no seat) and returns 0 while the graphical seat stays unlocked.
        rc, _, err = _run(["loginctl", "lock-sessions"], 5.0)
        return _tool_result(rc == 0, error=None if rc == 0 else err)
    if tool == "unlock":
        # Explicit phone-button press = user consent; unlocks own session.
        rc, _, err = _run(["loginctl", "unlock-sessions"], 5.0)
        return _tool_result(rc == 0, error=None if rc == 0 else err)
    if tool == "screens_state":
        if _which("kscreen-doctor") is None:
            return _tool_result(False, error="kscreen-doctor not installed")
        rc, out, err = _run(["kscreen-doctor", "-o"], 10.0)
        if rc != 0:
            return _tool_result(False, error=err or "kscreen-doctor failed")
        return _tool_result(True, outputs=_parse_kscreen_outputs(out))
    if tool in ("screen_off", "screen_on"):
        name = str(args.get("output", "")).strip()
        if _which("kscreen-doctor") is None:
            return _tool_result(False, error="kscreen-doctor not installed")
        rc, out, err = _run(["kscreen-doctor", "-o"], 10.0)
        if rc != 0:
            return _tool_result(False, error=err or "kscreen-doctor failed")
        outputs = _parse_kscreen_outputs(out)
        targets = [o for o in outputs if not name or o["name"] == name]
        if not targets:
            return _tool_result(False, error=f"no such output {name!r}")
        verb = "disable" if tool == "screen_off" else "enable"
        failed = []
        for o in targets:
            rc, _, err = _run(["kscreen-doctor", f"output.{o['name']}.{verb}"], 10.0)
            if rc != 0:
                failed.append(o["name"])
        if failed:
            return _tool_result(False, error=f"failed: {failed}")
        if tool == "screen_off":
            with contextlib.suppress(OSError):
                _screens_off_path().write_text(
                    json.dumps({"off": [o["name"] for o in targets]})
                )
            return _tool_result(True, outputs=[o["name"] for o in targets])
    if tool == "screens_restore":
        try:
            saved = json.loads(_screens_off_path().read_text() or "{}")
        except (OSError, ValueError):
            saved = {}
        names = saved.get("off", [])
        if not names:
            return _tool_result(False, error="nothing recorded as off")
        failed = []
        for name in names:
            rc, _, err = _run(["kscreen-doctor", f"output.{name}.enable"], 10.0)
            if rc != 0:
                failed.append(name)
        if failed:
            return _tool_result(False, error=f"failed: {failed}")
        with contextlib.suppress(OSError):
            _screens_off_path().unlink(missing_ok=True)
        return _tool_result(True, outputs=names)
    if tool in ("remote_start", "remote_stop", "remote_status"):
        # RustDesk (direct IP, Tailscale) via scripts/remote_session.sh (argv, timeout 15).
        # Returns the script's JSON plus the tailscale host. No log_action
        # import here (bridge stays stdlib-only), so no logging.
        return _run_remote_tool(tool)
    return _tool_result(False, error=f"unknown tool {tool!r}")


def _phone_cam_dir() -> Path:
    d = Path.home() / ".jarvis" / "phone_cam"
    d.mkdir(parents=True, exist_ok=True)
    return d


def store_camera_frame(image_b64: str) -> dict:
    """Persist one phone photo, prune to the last _CAM_KEEP. Returns stats."""
    try:
        raw = base64.b64decode(image_b64, validate=True)
    except (ValueError, base64.binascii.Error):
        return _tool_result(False, error="image_b64 is not valid base64")
    if not raw[:4] or len(raw) > 8 * 1024 * 1024:
        return _tool_result(False, error="image empty or >8MB")
    d = _phone_cam_dir()
    name = f"{int(time.time() * 1000)}.jpg"
    (d / name).write_bytes(raw)
    (d / "latest.jpg").write_bytes(raw)
    olds = sorted(
        (p for p in d.iterdir() if p.suffix == ".jpg" and p.name != "latest.jpg"),
        key=lambda p: p.name,
    )
    for stale in olds[: max(0, len(olds) - _CAM_KEEP)]:
        stale.unlink(missing_ok=True)
    return _tool_result(True, name=name, bytes=len(raw))


def handle_type(body: dict) -> tuple[int, dict]:
    """Remote keyboard: {"text": ...} or {"key": ...} via wtype.

    Shared by POST /type and voice-command routing ("type hello").
    Returns (http_code, payload); never raises.
    """
    text, key = body.get("text", ""), body.get("key", "")
    if key:
        if not isinstance(key, str) or len(key) > 40:
            return 400, {"ok": False, "error": "bad key"}
        rc, _, err = _run(["wtype", "-k", key], 10.0)
    else:
        if not isinstance(text, str) or not text or len(text) > _TYPE_MAX:
            return 400, {"ok": False, "error": f"text 1..{_TYPE_MAX} chars"}
        rc, _, err = _run(["wtype", "--", text], 10.0)
    if rc != 0:
        return 500, {"ok": False, "error": err or "wtype failed"}
    return 200, {"ok": True}


def _speak_quietly(reply: str) -> tuple[str, int]:
    """Render TTS for an action reply. ("", 22050) when TTS is down.

    The phone falls back to on-device TTS for empty audio, so a dead
    voice stack degrades the reply instead of failing the action.
    """
    try:
        try:
            from src.local_voice import PiperTTS
        except ImportError:
            from local_voice import PiperTTS

        out = asyncio.run(_render_tts(PiperTTS(), reply))
        return base64.b64encode(out[0]).decode(), out[1]
    except Exception:
        return "", 22050


def _run_voice_action(tool: str, args: dict) -> tuple[int, dict]:
    """Execute one routed voice command. "type" goes via wtype."""
    if tool == "type":
        return handle_type(args)
    result = run_phone_tool(tool, args if isinstance(args, dict) else {})
    if tool == "screenshot" and result.get("ok"):
        result = {"ok": True}  # voice can't show the image; keep replies light
    return (200 if result.get("ok") else 500, result)


def _dynamic_voice_reply(tool: str, args: dict, result: dict) -> str:
    """Success replies that need the tool result. Pure."""
    if tool in ("volume_get", "volume_set"):
        vol = result.get("volume")
        if isinstance(vol, int):
            state = "muted" if result.get("muted") else "live"
            return f"Volume {vol} percent, {state}, Sir."
        return "Volume unknown, Sir."
    if tool == "close_app":
        return (result.get("say") or "Closed").rstrip(". ") + ", Sir."
    if tool == "drafts_ui":
        say = result.get("say")
        return say if isinstance(say, str) and say else "Very good."
    if tool == "play_media":
        q = args.get("query") or ""
        return f"Playing {q} on YouTube, Sir." if q else "Playing your playlist, Sir."
    if tool == "open_app":
        name = result.get("app") or args.get("app", "it")
        if result.get("state") == "already running":
            return f"{name} is already running, Sir."
        return f"Opening {name}, Sir."
    return "Done, Sir."


def _voice_tool_reply(
    cmd: tuple[str, dict, str | None], transcript: str, guest: bool
) -> tuple[int, dict]:
    """Execute a routed voice command for /talk (with Piper audio).

    Never claims success on failure: a dead tool answers with what went
    wrong instead of a cheerful lie. Returns (http_code, payload) with
    an "action" block the phone may show.
    """
    tool, args, ok_reply = cmd
    if guest and tool not in _GUEST_VOICE_TOOLS:
        audio_b64, rate = _speak_quietly(_GUEST_REFUSAL)
        return 200, {
            "ok": True,
            "transcript": transcript,
            "reply": _GUEST_REFUSAL,
            "audio_b64": audio_b64,
            "audio_rate": rate,
            "action": {"tool": tool, "ok": False, "denied": "guest mode"},
        }
    code, result = _run_voice_action(tool, args)
    if code == 200 and result.get("ok"):
        reply = (
            ok_reply
            if ok_reply is not None
            else _dynamic_voice_reply(tool, args, result)
        )
    else:
        reply = f"I couldn't, Sir — {(result.get('error') or 'it failed')[:150]}."
    audio_b64, rate = _speak_quietly(reply)
    action = {"tool": tool, "ok": bool(result.get("ok"))}
    if not result.get("ok"):
        action["error"] = result.get("error")
    elif tool == "remote_start":
        if result.get("host"):
            action["host"] = result["host"]
        if result.get("port"):
            action["port"] = result["port"]
    return 200, {
        "ok": True,
        "transcript": transcript,
        "reply": reply,
        "audio_b64": audio_b64,
        "audio_rate": rate,
        "action": action,
    }


# ---- Instant command route: text -> tool in ms, never touching an LLM.
#
# Separate from /chat and /talk on purpose: those are conversational
# (STT + Gemini + Piper TTS, seconds end to end, and they 429 on quota
# droughts). /route is deterministic plumbing for commands: regex
# (microseconds), then the layered intent resolver (exact/keyword/fuzzy
# local, Needle-2 semantic when installed — hundreds of ms, offline),
# then straight to the tool with a canned reply. No audio, no LLM.
_PHONE_ALIASES: dict[str, dict] = {
    # Paraphrases the voice regexes miss; merged with the resolver's
    # own seeds (resolve_intent takes them as extra aliases).
    "secure my laptop": {"action": "lock_pc", "params": {}},
    "secure the computer": {"action": "lock_pc", "params": {}},
    "lock up the pc": {"action": "lock_pc", "params": {}},
    "lock everything": {"action": "lock_pc", "params": {}},
    "let me in": {"action": "unlock_pc", "params": {}},
    "unlock the laptop": {"action": "unlock_pc", "params": {}},
    "unlock my pc": {"action": "unlock_pc", "params": {}},
    "snap the screen": {"action": "take_screenshot", "params": {}},
    "grab the screen": {"action": "take_screenshot", "params": {}},
    "kill the screens": {"action": "screens_off", "params": {}},
    "wake the screens": {"action": "screens_restore", "params": {}},
}

# Resolver agent-actions -> phone bridge tools (param mapping inline).
# Anything absent here is not phone-executable and stays a no-route.
_ROUTE_VOLUME = {
    "up": "volume_up",
    "down": "volume_down",
    "mute": "volume_mute",
    "unmute": "volume_unmute",
    "status": "volume_get",
}
_ROUTE_MEDIA = {
    "play": "media_play_pause",
    "pause": "media_play_pause",
    "next": "media_next",
    "previous": "media_prev",
}
_ROUTE_APPS = {
    "files": "files",
    "terminal": "terminal",
    "calculator": "calculator",
    "browser": "brave",
    "whatsie": "whatsie",
}


def _resolve_intent(text: str):
    """Layered intent resolve, or None when the resolver is unavailable.

    Thin wrapper so tests can monkeypatch without importing intent.
    """
    try:
        from intent.resolver import resolve_intent

        return resolve_intent(text, _PHONE_ALIASES)
    except Exception:
        return None


def _safe_math(expr: str) -> float | None:
    """Tiny safe arithmetic eval (+-*/%** parens). None = not arithmetic."""
    import ast
    import operator

    ops = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops:
            return ops[type(node.op)](walk(node.operand))
        raise ValueError("not arithmetic")

    try:
        if not expr or len(expr) > 60:
            return None
        return walk(ast.parse(expr.strip(), mode="eval"))
    except Exception:
        return None


def _tell_time_reply() -> str:
    return time.strftime("It's %-I:%M %p, Sir.")


def _route_from_intent(result) -> tuple[str, dict, str | None] | None:
    """IntentResult -> (tool, args, reply). None = not phone-executable."""
    action = result.action
    params = result.params or {}
    if action == "set_volume":
        tool = _ROUTE_VOLUME.get(str(params.get("action", "")))
        return (tool, {}, None) if tool else None
    if action == "media_control":
        tool = _ROUTE_MEDIA.get(str(params.get("action", "")))
        return (tool, {}, None) if tool else None
    if action == "open_app":
        raw = str(params.get("app", "")).strip()
        app = _ROUTE_APPS.get(raw, raw)
        return ("open_app", {"app": app}, None) if app else None
    if action == "tell_time":
        return ("tell_time", {}, None)
    if action == "do_math":
        return ("do_math", {"expr": str(params.get("expr", ""))}, None)
    if action == "lock_pc":
        return ("lock", {}, "Locked, Sir.")
    if action == "unlock_pc":
        return ("unlock", {}, "Unlocked, Sir.")
    if action == "take_screenshot":
        return (
            "screenshot",
            {},
            "Screenshot taken, Sir — it's on the Screens tab.",
        )
    if action == "screens_off":
        return ("screen_off", {}, "Screens off, Sir.")
    if action == "screens_restore":
        return ("screens_restore", {}, "Screens back, Sir.")
    if action == "transfer_to_system_control":
        # "secure my laptop" lands here via Needle/fuzzy: re-match the
        # task for a concrete phone tool, else refuse to half-act.
        return _match_voice_tool(str(params.get("task", "")))
    return None


def handle_route(body: dict) -> tuple[int, dict]:
    """POST /route {"text": ...}: text -> executed tool in milliseconds.

    Layers: voice regexes (µs) -> layered resolver incl. Needle (ms).
    Acts only at act-tier confidence (>=0.8); confirm-tier returns the
    clarification as the reply with no action; anything else is an
    honest no-route (the phone falls back to /chat). No LLM, no TTS —
    the phone speaks the reply on-device. Never raises.
    """
    text = body.get("text", "")
    if not isinstance(text, str) or not text.strip() or len(text) > 500:
        return 400, {"ok": False, "error": "body needs text 1..500 chars"}
    guest = body.get("guest") is True
    clean = text.strip()

    def denied(tool: str) -> tuple[int, dict]:
        return 200, {
            "ok": True,
            "reply": _GUEST_REFUSAL,
            "action": {"tool": tool, "ok": False, "denied": "guest mode"},
        }

    def done(tool: str, args: dict, ok_reply: str | None) -> tuple[int, dict]:
        if guest and tool not in _GUEST_VOICE_TOOLS:
            return denied(tool)
        if tool == "tell_time":
            return 200, {
                "ok": True,
                "reply": _tell_time_reply(),
                "action": {"tool": "tell_time", "ok": True},
            }
        if tool == "do_math":
            value = _safe_math(args.get("expr", ""))
            if value is None:
                return 404, {"ok": False, "error": "no-route"}
            shown = int(value) if float(value).is_integer() else round(value, 4)
            return 200, {
                "ok": True,
                "reply": f"That's {shown}, Sir.",
                "action": {"tool": "do_math", "ok": True},
            }
        if tool == "type":
            code, result = handle_type(args)
        else:
            result = _run_voice_action(tool, args)
            result = result[1]
            code = 200 if result.get("ok") else 500
        if code == 200 and result.get("ok"):
            reply = (
                ok_reply
                if ok_reply is not None
                else _dynamic_voice_reply(tool, args, result)
            )
        else:
            reply = f"I couldn't, Sir — {(result.get('error') or 'it failed')[:150]}."
        action: dict = {"tool": tool, "ok": bool(result.get("ok"))}
        if not result.get("ok"):
            action["error"] = result.get("error")
        elif tool == "remote_start":
            if result.get("host"):
                action["host"] = result["host"]
            if result.get("port"):
                action["port"] = result["port"]
        return 200, {"ok": True, "reply": reply, "action": action}

    cmd = _match_voice_tool(clean)
    if cmd is not None:
        return done(*cmd)
    result = _resolve_intent(clean)
    if result is None:
        return 404, {"ok": False, "error": "no-route"}
    if result.should_act:
        routed = _route_from_intent(result)
        if routed is not None:
            return done(*routed)
        return 404, {"ok": False, "error": "no-route"}
    if result.should_confirm and result.clarification:
        return 200, {"ok": True, "reply": result.clarification}
    if result.clarification:
        return 200, {"ok": True, "reply": result.clarification}
    return 404, {"ok": False, "error": "no-route"}


def handle_talk(body: dict) -> tuple[int, dict]:
    """16k PCM -> transcript + Gemini reply + Piper audio. Lazy heavy deps.

    Returns (http_code, payload). 501 when the venv lacks a stage
    (faster-whisper / google-genai / local voice); the bridge itself stays
    stdlib-runnable without them.
    """
    audio_b64 = body.get("audio_b64", "")
    rate = body.get("rate", 16000)
    if not isinstance(audio_b64, str) or not audio_b64:
        return 400, {"ok": False, "error": "body needs audio_b64"}
    if rate != 16000:
        return 400, {"ok": False, "error": "only rate 16000 supported"}
    try:
        pcm = base64.b64decode(audio_b64, validate=True)
    except (ValueError, base64.binascii.Error):
        return 400, {"ok": False, "error": "audio_b64 is not valid base64"}
    if len(pcm) > _TALK_AUDIO_MAX or len(pcm) < 3200:
        return 400, {"ok": False, "error": "audio must be 0.1s..60s of 16k PCM"}
    try:
        import numpy as np
        from faster_whisper import WhisperModel
    except ImportError:
        return 501, {"ok": False, "error": "voice stack missing (faster-whisper)"}
    global _PHONE_WHISPER
    if _PHONE_WHISPER is None:
        _PHONE_WHISPER = WhisperModel(
            os.environ.get("JARVIS_PHONE_WHISPER", "tiny"),
            device="cpu",
            compute_type="int8",
        )
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    try:
        segments, _ = _PHONE_WHISPER.transcribe(audio, language="en", beam_size=1)
        transcript = " ".join(s.text.strip() for s in segments).strip()
    except Exception as exc:
        return 500, {"ok": False, "error": f"transcribe failed: {exc}"[:200]}
    if not transcript:
        return 200, {"ok": True, "transcript": "", "reply": "", "audio_b64": ""}
    guest = body.get("guest") is True
    cmd = _match_voice_tool(transcript)
    if cmd is not None:
        return _voice_tool_reply(cmd, transcript, guest)
    reply, warning = _gemini_reply(transcript, guest)
    if warning and not reply:
        # Degrade, don't fail: the phone shows what was heard and notes the
        # brain outage (free-tier quota droughts read-timeout this path).
        return 200, {
            "ok": True,
            "transcript": transcript,
            "reply": "",
            "audio_b64": "",
            "warning": warning,
        }
    try:
        try:
            from src.local_voice import PiperTTS
        except ImportError:
            from local_voice import PiperTTS

        out = asyncio.run(_render_tts(PiperTTS(), reply))
    except Exception as exc:
        return 500, {"ok": False, "error": f"TTS failed: {exc}"[:200]}
    return 200, {
        "ok": True,
        "transcript": transcript,
        "reply": reply,
        "audio_b64": base64.b64encode(out[0]).decode(),
        "audio_rate": out[1],
    }


async def _render_tts(tts, text: str) -> tuple[bytes, int]:
    pcm = await asyncio.to_thread(tts._render_sentence, text)
    rate = getattr(tts, "_sample_rate", 22050) or 22050
    return pcm, rate


def handle_chat(body: dict) -> tuple[int, dict]:
    """Text chat for the phone Chat tab. Gemini-direct, no audio.

    Body: {"text": str (1..2000), "history": [[role, text]...] (max 20,
    optional), "voice": bool (optional)}. History roles: user/jarvis.
    Returns 200 {reply} or {reply: "", warning} on LLM outage (same
    degrade rule). With "voice": true the text instead summons a voice
    call seeded with it (text→voice trigger): 200 {"voice": "summoned"}
    and the answer comes back spoken. When the wake listener is down it
    degrades to the normal text reply so the phone always gets something.
    Voice seeds cap at 500 chars (400 beyond that).
    """
    text = body.get("text", "")
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        return 400, {"ok": False, "error": "body needs text 1..2000 chars"}
    if body.get("voice") is True:
        seed = text.strip()
        if len(seed) > 500:
            return (
                400,
                {"ok": False, "error": "voice seed max 500 chars"},
            )
        reply = _wake_rpc({"talk": True, "text": seed})
        if reply is not None:
            return 200, {"ok": True, "voice": "summoned"}
        # Listener absent: fall through to the text answer below.
    guest = body.get("guest") is True
    cmd = _match_voice_tool(text.strip())
    if cmd is not None:
        tool, args, ok_reply = cmd
        if guest and tool not in _GUEST_VOICE_TOOLS:
            return 200, {
                "ok": True,
                "reply": _GUEST_REFUSAL,
                "action": {"tool": tool, "ok": False, "denied": "guest mode"},
            }
        code, result = _run_voice_action(tool, args)
        if code == 200 and result.get("ok"):
            reply = (
                ok_reply
                if ok_reply is not None
                else _dynamic_voice_reply(tool, args, result)
            )
        else:
            reply = f"I couldn't, Sir — {(result.get('error') or 'it failed')[:150]}."
        action = {"tool": tool, "ok": bool(result.get("ok"))}
        if not result.get("ok"):
            action["error"] = result.get("error")
        elif tool == "remote_start":
            if result.get("host"):
                action["host"] = result["host"]
            if result.get("port"):
                action["port"] = result["port"]
        return 200, {"ok": True, "reply": reply, "action": action}
    history = body.get("history", [])
    turns = []
    if isinstance(history, list):
        for turn in history[-20:]:
            if (
                isinstance(turn, list)
                and len(turn) == 2
                and turn[0] in ("user", "jarvis")
                and isinstance(turn[1], str)
            ):
                who = "User" if turn[0] == "user" else "Jarvis"
                turns.append(f"{who}: {turn[1][:1000]}")
    if guest:
        prompt = (
            "You are Jarvis in guest mode: cold, curt, faintly contemptuous. "
            "Keep replies to one short sentence, plain text, no formatting. "
            "Answer simple questions only; refuse anything else with frost. "
        )
    else:
        prompt = (
            "You are Jarvis, a terse British butler texting with Sir. "
            "Keep replies short (a few sentences max), plain text, no formatting. "
        )
    if turns:
        prompt += "Conversation so far:\n" + "\n".join(turns) + "\n"
    prompt += f"Sir: {text.strip()}"
    reply, warning = _gemini_reply(prompt)
    if warning and not reply:
        return 200, {"ok": True, "reply": "", "warning": warning}
    return 200, {"ok": True, "reply": reply}


class _Handler(BaseHTTPRequestHandler):
    token: str = ""

    def log_message(self, *args: object) -> None:  # keep voice logs clean
        pass

    def _authorized(self) -> bool:
        if not self.token:
            return True
        got = self.headers.get("Authorization", "")
        want = "Bearer " + self.token
        return (
            hashlib.sha256(got.encode()).digest()
            == hashlib.sha256(want.encode()).digest()
        )

    def _send(self, code: int, payload: dict | list) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # The Tauri webview (tauri://localhost) and the Next dev server
        # fetch the bridge cross-origin. Loopback-only server + no cookies:
        # a wildcard is safe here (auth rides the Authorization header,
        # which browsers allow under `*` outside credentialed mode).
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        """Preflight sink: allow reads + the /mic mute POST, short-circuit."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_bytes(self, code: int, content_type: str, raw: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(401, {"ok": False, "error": "unauthorized"})
            return
        parsed = urlparse(self.path)
        route = parsed.path
        if route == "/mode":
            body = _read_json_body(self, 256)
            want = (body or {}).get("mode") if isinstance(body, dict) else None
            if want not in (_school.SCHOOL, _school.NORMAL):
                self._send(400, {"ok": False, "error": "mode must be school or normal"})
                return
            self._send(200, {"ok": True, **set_school_mode(want)})
            return
        if route == "/phone/telemetry":
            body = _read_json_body(self, 1024)
            stored = record_phone_telemetry(body) if isinstance(body, dict) else None
            if stored is None:
                self._send(
                    400,
                    {
                        "ok": False,
                        "error": 'body must be {"battery": 0-100, "charging": bool}',
                    },
                )
            else:
                self._send(200, {"ok": True})
            return
        if route == "/mic":
            body = _read_json_body(self, 1024)
            if not isinstance(body, dict) or not isinstance(body.get("muted"), bool):
                self._send(400, {"ok": False, "error": 'body must be {"muted": bool}'})
                return
            reply = _wake_rpc({"mute": body["muted"]})
            if reply is None:
                self._send(503, {"ok": False, "error": "wake listener unavailable"})
                return
            self._send(200, reply)
            return
        if route == "/summon":
            # PTT: HUD NumpadEnter / shell talk. Optional {"text": "..."}
            # seeds the call: the wake listener delivers it as the opening
            # user turn once the agent joins (text→voice). Blank/missing
            # text is plain talk; wrong types or >500 chars are 400.
            # (Cap mirrors wake_client.TALK_TEXT_MAX; kept local so the
            # bridge never imports the wake venv's modules.)
            body = _read_json_body(self, 1024)
            talk: dict = {"talk": True}
            if isinstance(body, dict) and body.get("text") is not None:
                seed = body.get("text")
                if isinstance(seed, str) and not seed.strip():
                    pass  # blank reads as plain talk
                elif not isinstance(seed, str):
                    self._send(400, {"ok": False, "error": "text must be a string"})
                    return
                elif len(seed.strip()) > 500:
                    self._send(400, {"ok": False, "error": "seed text max 500 chars"})
                    return
                else:
                    talk["text"] = seed.strip()
            reply = _wake_rpc(talk)
            if reply is None:
                self._send(503, {"ok": False, "error": "wake listener unavailable"})
                return
            self._send(200, reply)
            return
        if route == "/type":
            body = _read_json_body(self, 4096)
            if body is None:
                self._send(400, {"ok": False, "error": "invalid JSON body"})
                return
            if body.get("guest") is True:
                self._send(403, {"ok": False, "error": "guest mode refuses type"})
                return
            code, result = handle_type(body)
            self._send(code, result)
            return
        if route == "/tool":
            body = _read_json_body(self, _TOOL_BODY_MAX)
            if body is None or not isinstance(body.get("tool"), str):
                self._send(400, {"ok": False, "error": 'body needs {"tool": str}'})
                return
            if body.get("guest") is True and body["tool"] not in _GUEST_VOICE_TOOLS:
                self._send(
                    403,
                    {"ok": False, "error": f"guest mode refuses {body['tool']!r}"},
                )
                return
            args = body.get("args", {})
            result = run_phone_tool(
                body["tool"], args if isinstance(args, dict) else {}
            )
            self._send(200 if result.get("ok") else 400, result)
            return
        if route == "/talk":
            body = _read_json_body(self, _TALK_AUDIO_MAX + 4096)
            if body is None:
                self._send(400, {"ok": False, "error": "invalid JSON body"})
                return
            code, result = handle_talk(body)
            self._send(code, result)
            return
        if route == "/projects":
            import projects as _projects

            body = _read_json_body(self, 16384)
            if body is None:
                self._send(400, {"ok": False, "error": "invalid JSON body"})
                return
            if body.get("guest") is True:
                self._send(403, {"ok": False, "error": "guest mode refuses projects"})
                return
            kind = body.get("kind")
            if kind == "research":
                topic = body.get("topic")
                if not isinstance(topic, str) or not 3 <= len(topic.strip()) <= 2000:
                    self._send(400, {"ok": False, "error": "topic 3..2000 chars"})
                    return
                meta = _projects.start_research(topic)
            elif kind == "code":
                task = body.get("task")
                if not isinstance(task, str) or not 3 <= len(task.strip()) <= 4000:
                    self._send(400, {"ok": False, "error": "task 3..4000 chars"})
                    return
                meta = _projects.start_code(
                    task,
                    directory=str(body.get("directory") or ""),
                    variant=str(body.get("variant") or ""),
                )
            else:
                self._send(400, {"ok": False, "error": 'kind "research" or "code"'})
                return
            self._send(200, {"ok": True, "project": meta})
            return
        if route.startswith("/projects/") and route.endswith("/cancel"):
            import projects as _projects

            pid = route[len("/projects/") : -len("/cancel")]
            self._send(200, {"ok": _projects.cancel_project(pid)})
            return
        if route == "/drafts/close":
            import drafts_ui as _drafts

            self._send(200, {"ok": True, "closed": bool(_drafts.sync_close())})
            return
        if route == "/chat":
            body = _read_json_body(self, 65536)
            if body is None:
                self._send(400, {"ok": False, "error": "invalid JSON body"})
                return
            code, result = handle_chat(body)
            self._send(code, result)
            return
        if route == "/route":
            # Instant command path: text -> tool in ms, no LLM, no TTS.
            # 200 with action, 200 with bare reply (confirm/clarify),
            # 404 no-route (caller falls back to /chat), 400 bad body.
            body = _read_json_body(self, 4096)
            if body is None:
                self._send(400, {"ok": False, "error": "invalid JSON body"})
                return
            code, result = handle_route(body)
            self._send(code, result)
            return
        if route == "/token":
            # Mobile room join: mint a 15-min LiveKit token (dispatches
            # the agent unless {"dispatch": false} joins a live room).
            # Same shape as mint_token.py CLI output. Body optional:
            # {"room": "jarvis-123", "dispatch": true}.
            from mint_token import mint_token_payload, valid_room_name

            body = _read_json_body(self, 1024)
            if body is None:
                body = {}
            room = body.get("room", "")
            if not isinstance(room, str):
                self._send(400, {"ok": False, "error": "room must be a string"})
                return
            if room and not valid_room_name(room):
                self._send(400, {"ok": False, "error": "bad room name"})
                return
            dispatch = body.get("dispatch", True)
            if not isinstance(dispatch, bool):
                dispatch = True
            try:
                payload = mint_token_payload(room, dispatch)
            except RuntimeError as exc:
                self._send(503, {"ok": False, "error": str(exc)[:200]})
                return
            except ImportError:
                self._send(501, {"ok": False, "error": "livekit sdk not installed"})
                return
            except Exception as exc:
                self._send(500, {"ok": False, "error": str(exc)[:200]})
                return
            self._send(200, {"ok": True, **payload})
            return
        if route == "/camera/frame":
            body = _read_json_body(self, 8 * 1024 * 1024 + 1024)
            if body is None or not isinstance(body.get("image_b64"), str):
                self._send(400, {"ok": False, "error": "body needs image_b64"})
                return
            result = store_camera_frame(body["image_b64"])
            self._send(200 if result.get("ok") else 400, result)
            return
        if route == "/window":
            import taskbar

            body = _read_json_body(self, 1024)
            if not isinstance(body, dict):
                self._send(400, {"ok": False, "error": "invalid JSON body"})
                return
            code, result = taskbar.handle_window(body)
            self._send(code, result)
            return
        if route == "/launch":
            import taskbar

            body = _read_json_body(self, 1024)
            if not isinstance(body, dict):
                self._send(400, {"ok": False, "error": "invalid JSON body"})
                return
            code, result = taskbar.handle_launch(body)
            self._send(code, result)
            return
        if route == "/quick":
            import taskbar

            body = _read_json_body(self, 1024)
            if not isinstance(body, dict):
                self._send(400, {"ok": False, "error": "invalid JSON body"})
                return
            code, result = taskbar.handle_quick(body)
            if code == 200 and result.get("ok") and body.get("action") in (
                "volume",
                "mute",
            ):
                _VOL_CACHE.clear()  # volume changed: drop the /sys read cache
            self._send(code, result)
            return
        if route == "/power":
            import taskbar

            body = _read_json_body(self, 1024)
            if not isinstance(body, dict):
                self._send(400, {"ok": False, "error": "invalid JSON body"})
                return
            code, result = taskbar.handle_power(body)
            self._send(code, result)
            return
        self._send(404, {"ok": False, "error": "unknown route"})

    def do_GET(self) -> None:
        if not self._authorized():
            self._send(401, {"ok": False, "error": "unauthorized"})
            return
        parsed = urlparse(self.path)
        route, qs = parsed.path, parse_qs(parsed.query)
        if route == "/health":
            self._send(
                200,
                {
                    "ok": True,
                    "version": VERSION,
                    "uptime_s": int(time.time() - _STARTED_AT),
                },
            )
        elif route == "/status":
            self._send(200, build_status())
        elif route == "/actions":
            try:
                limit = max(1, min(200, int(qs.get("limit", ["50"])[0])))
            except ValueError:
                limit = 50
            self._send(
                200, {"ok": True, "actions": _tail_log(_actions_log_path(), limit)}
            )
        elif route == "/sys":
            self._send(200, {"ok": True, **_sys_stats()})
        elif route == "/appicon":
            app = (qs.get("app") or [""])[0]
            if not isinstance(app, str) or not app.strip() or len(app) > 200:
                self._send(400, {"ok": False, "error": "app required"})
                return
            try:
                data = app_icon_data_url(app.strip())
            except Exception:
                data = None
            if data:
                self._send(200, {"ok": True, "data": data})
            else:
                self._send(200, {"ok": False})
        elif route == "/apps":
            import taskbar

            try:
                apps = taskbar.list_apps()
            except Exception:
                apps = []
            self._send(200, {"ok": True, "apps": apps})
        elif route == "/quick":
            import taskbar

            try:
                state = taskbar.quick_state(volume=_volume_status())
            except Exception:
                state = {
                    "wifi": None,
                    "bluetooth": None,
                    "volume": None,
                    "brightness": None,
                    "dnd": None,
                }
            self._send(200, {"ok": True, **state})
        elif route == "/mode":
            self._send(200, {"ok": True, "mode": _school.current()})
        elif route == "/mic":
            reply = _wake_rpc({"status": True})
            if reply is None:
                self._send(200, {"ok": False, "error": "wake listener unavailable"})
            else:
                self._send(200, reply)
        elif route == "/school/geom":
            # School entry transition: the shell's KWin measure pushed this
            # over D-Bus (active_window.SchoolGeom); the page polls for it.
            try:
                import active_window

                geom = active_window.school_geom()
            except Exception:
                geom = None
            self._send(200, {"ok": True, "geom": geom})
        elif route == "/room":
            self._send(
                200,
                {
                    "ok": True,
                    "room": read_hud_room(),
                    "waking": read_hud_waking(),
                    "boot": read_hud_boot(),
                },
            )
        elif route == "/captions":
            try:
                limit = max(1, min(50, int(qs.get("limit", ["20"])[0])))
            except ValueError:
                limit = 20
            self._send(200, {"ok": True, "captions": read_captions(limit)})
        elif route == "/caption/live":
            self._send(200, {"ok": True, "live": read_live_caption()})
        elif route == "/activity":
            self._send(200, {"ok": True, "items": read_activity()})
        elif route == "/camera/latest":
            latest = _phone_cam_dir() / "latest.jpg"
            try:
                raw = latest.read_bytes()
            except OSError:
                self._send(404, {"ok": False, "error": "no frames yet"})
                return
            self._send_bytes(200, "image/jpeg", raw)
        elif route == "/projects":
            import projects as _projects

            self._send(200, {"ok": True, "projects": _projects.list_projects()})
        elif route == "/projects/ui":
            import projects as _projects

            raw = (qs.get("since") or [None])[0]
            try:
                since = int(raw) if raw is not None else None
            except ValueError:
                since = None
            self._send(200, _projects.BUS.since(since))
        elif route.startswith("/projects/"):
            import projects as _projects

            proj = _projects.get_project(route.split("/", 2)[2])
            if proj is None:
                self._send(404, {"ok": False, "error": "no such project"})
            else:
                self._send(200, {"ok": True, "project": proj})
        elif route == "/drafts":
            import drafts_ui as _drafts

            rows = _drafts.pending_drafts()
            self._send(
                200,
                {
                    "ok": True,
                    "count": len(rows),
                    "drafts": [
                        {
                            "id": d.get("id"),
                            "to": d.get("to"),
                            "subject": d.get("subject"),
                            "body": d.get("body"),
                            "summary": d.get("summary"),
                            "sender": d.get("sender"),
                            "created": d.get("created"),
                            "status": d.get("status"),
                            "priority": d.get("priority"),
                        }
                        for d in rows
                    ],
                },
            )
        elif route == "/config":
            self._send(
                200,
                {
                    "ok": True,
                    "pipeline": _env("JARVIS_PIPELINE", "realtime"),
                    "wake_threshold": _env("JARVIS_WAKE_THRESHOLD", "0.5"),
                    "needle_enabled": _env("JARVIS_NEEDLE", "1") != "0",
                    "local_unlocked": _env("JARVIS_LOCAL") == "1",
                    "livekit_configured": bool(
                        _env("LIVEKIT_URL")
                        and _env("LIVEKIT_API_KEY")
                        and _env("LIVEKIT_API_SECRET")
                    ),
                    "bridge_token_set": bool(self.token),
                },
            )
        else:
            self._send(404, {"ok": False, "error": "unknown route"})


def create_server(
    port: int = DEFAULT_PORT, token: str = "", host: str = "127.0.0.1"
) -> ThreadingHTTPServer:
    """Create (not start) the server. Port 0 = ephemeral (tests)."""

    class BoundHandler(_Handler):
        pass

    BoundHandler.token = token
    server = ThreadingHTTPServer((host, port), BoundHandler)
    server.daemon_threads = True
    return server


def serve_forever(
    port: int = DEFAULT_PORT, token: str = "", host: str = "127.0.0.1"
) -> None:
    server = create_server(port, token, host)
    # Crash recovery + reboot: the Meta shortcut must match the stored
    # mode (school -> Jarvis menu; stale backup in normal -> restore).
    with contextlib.suppress(Exception):
        _school.meta_sync_on_startup()
    start_activity_watch()
    # Do not start a Brave-focus layout watcher here. The HUD belongs where
    # the user left it; Projects UI and School Mode are the only intentional
    # layout transitions.
    start_sidecar_threads()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    try:
        port = int(_env("JARVIS_BRIDGE_PORT", str(DEFAULT_PORT)))
    except ValueError:
        port = DEFAULT_PORT
    host, token_required = resolve_bind()
    token = _env("JARVIS_BRIDGE_TOKEN")
    if token_required and not token:
        print(
            f"REFUSING to bind {host} without JARVIS_BRIDGE_TOKEN "
            "(set a token or keep JARVIS_BRIDGE_BIND=127.0.0.1).",
            flush=True,
        )
        raise SystemExit(2)
    serve_forever(port=port, token=token, host=host)


if __name__ == "__main__":
    main()


def _run_in_thread(
    port: int = 0, token: str = ""
) -> tuple[ThreadingHTTPServer, Thread]:
    """Test helper: serve on an ephemeral port in a background thread."""
    server = create_server(port, token)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
