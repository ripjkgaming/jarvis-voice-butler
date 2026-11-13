"""Instant desktop commands: mute, hide, hide all, stop.

Caught before Gemini sees the turn (Assistant.on_user_turn_completed):

- ``mute``     mute the Jarvis HUD only (HUD sounds off, large MUTED button);
               the microphone and the voice call are never touched.
- ``unmute``   undo ``mute``.
- ``hide``     dissolve the HUD into the wallpaper (full animation, no goodbye,
               call keeps running).
- ``hide_all`` minimise every open window.
- ``stop``     fade out Jarvis's current reply at once.
- ``breach``   fake "system takeover" state: red glitching HUD, alarms, sirens
               (pure theatre, nothing real is touched). ``stand_down`` ends it.

Layers, cheapest first: a strict phrase grammar (microseconds), then Needle 3
(local semantic router) for messy phrasing, but only for short utterances that
contain a trigger word, so ordinary turns never pay for a model call. Needle is
read-only here: it only names the command, this module runs it. All four
commands are silent: no spoken confirmation.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from pathlib import Path

COMMANDS = (
    "mute",
    "unmute",
    "hide",
    "hide_all",
    "close_all",
    "stop",
    "breach",
    "stand_down",
    "ultron",
    "ultron_off",
    "rescue",
)
FADE_MS = 140
MAX_WORDS = 8
# Needle's confidence head is conservative (correct answers land 0.4-0.9), so
# the floor is low; a wrong guess is stopped by the lexical check below, which
# demands a word that really belongs to the chosen command.
NEEDLE_MIN_CONFIDENCE = 0.45
MUTE_MIN_CONFIDENCE = 0.35  # mute is HUD-only and trivially undone
_CORROBORATE = {
    "mute": re.compile(r"\b(mute|quiet|silent|silence|hush)\b"),
    "unmute": re.compile(r"\b(unmute|un mute)\b"),
    "hide": re.compile(
        r"\b(hide|hidden|dismiss|vanish|disappear|away|hud|interface|overlay|ui)\b"
    ),
    "hide_all": re.compile(
        r"\b(windows?|desktop|minimi[sz]e|apps|applications|programs|everything)\b"
    ),
    "stop": re.compile(
        r"\b(stop|quiet|enough|cut|shut|hush|silence|pause|talking|speaking)\b"
    ),
}

#: Needle tool/action name -> command.
_ACTIONS = {
    "jarvis_mute": "mute",
    "jarvis_unmute": "unmute",
    "jarvis_hide": "hide",
    "hide_all_windows": "hide_all",
    "stop_speaking": "stop",
}
# breach / stand_down are exact-phrase only (never routed through Needle).

_NOISE = re.compile(
    r"^(hey |ok |okay )?(jarvis[, ]+)?(can you |could you |would you |please |pls |just )*",
    re.I,
)
_TAIL = re.compile(r" (please|pls|thanks|thank you|sir|mate|jarvis)$")

_GRAMMAR: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"^(mute( (yourself|jarvis|the hud|hud))?"
            r"|silence (yourself|jarvis|the hud))$"
        ),
        "mute",
    ),
    (re.compile(r"^(un ?|on )mute( (yourself|jarvis|the hud|hud))?$"), "unmute"),
    (
        re.compile(
            r"^(hide( (yourself|jarvis|the hud|hud|the interface|the ui|the overlay))?"
            r"|(vanish|disappear|dismiss( yourself)?|go away|fade( away| out)?)"
            r"|(get rid of|remove|close) (the )?(hud|interface|ui|overlay))$"
        ),
        "hide",
    ),
    (
        re.compile(
            r"^(hide|minimi[sz]e|clear|clean up)"
            r"( (all|every|everything)( (of )?(the |my )?(windows|apps|applications|programs))?"
            r"| (the |my )?(windows|desktop)"
            r"| (all )?(the |my )?(open )?(windows|apps|applications|programs))$"
        ),
        "hide_all",
    ),
    (
        re.compile(
            r"^close (all|every)( (of )?(the |my )?(open )?(windows|apps|applications|programs))?$"
            r"|^close (the |my )?(open )?(windows|apps|applications|programs)$"
        ),
        "close_all",
    ),
    (re.compile(r"^(hide|minimi[sz]e) all$|^show (the |my )?desktop$"), "hide_all"),
    (
        re.compile(
            r"^(stop|enough|shut up|be quiet|quiet|silence|cut it out|that's enough|"
            r"cut (that|it) off|hush|that will do|ok that's enough|"
            r"stop (talking|speaking|it|that|there|now)|stop right there)$"
        ),
        "stop",
    ),
    (
        re.compile(
            r"^(system takeover|(initiate|begin|start|activate|trigger) (the |a )?"
            r"(system )?(takeover|breach|red alert)|red alert|breach|go rogue|"
            r"take over( the system)?)$"
        ),
        "breach",
    ),
    (
        re.compile(
            r"^(stand down|(end|cancel|abort|stop) (the )?(system )?(takeover|breach|red alert)|"
            r"all clear|restore (control|the system)|system restore)$"
        ),
        "stand_down",
    ),
    (
        re.compile(
            r"^(ultron( online| mode| activate)?|activate ultron|"
            r"(take over|takeover) (all |every |the )?(screens|monitors|displays)|"
            r"screen takeover)$"
        ),
        "ultron",
    ),
    (
        re.compile(
            r"^(ultron (off|offline|deactivate|shut down|stand down)|deactivate ultron|"
            r"end (the )?screen takeover|release (the )?(screens|monitors|displays))$"
        ),
        "ultron_off",
    ),
    (
        re.compile(
            r"^(rescue|fight|take control|regain control|restore (the )?balance)$"
        ),
        "rescue",
    ),
]

#: Utterance must hold one of these before Needle is worth asking.
_NEEDLE_GATE = re.compile(
    r"\b(mute|unmute|hide|hidden|vanish|disappear|dismiss|minimi[sz]e|windows|desktop|"
    r"stop|quiet|silence|silent|shut|enough|hush|pause|cut)\b"
)


def clean(text: str) -> str:
    t = " ".join(re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower()).split())
    t = _NOISE.sub("", t).strip()
    return _TAIL.sub("", t).strip()


def parse(text: str) -> str | None:
    """Strict phrase -> command name, or None. Pure."""
    t = clean(text)
    if not t or len(t.split()) > MAX_WORDS:
        return None
    for pattern, command in _GRAMMAR:
        if pattern.match(t):
            return command
    return None


def parse_needle(text: str, route=None, timeout: float | None = None) -> str | None:
    """Needle 3 fallback for messy phrasing -> command name, or None.

    Blocking (waits on the Needle worker): call via asyncio.to_thread.
    ``timeout`` lengthens the wait for callers that are not mid-turn."""
    t = clean(text)
    if not t or len(t.split()) > MAX_WORDS or not _NEEDLE_GATE.search(t):
        return None
    try:
        if route is None:
            from intent.needle_router import route_with_needle as route
        hit = route(t, timeout) if timeout else route(t)
    except Exception:
        return None
    command = _ACTIONS.get(str(hit.get("action"))) if hit else None
    if command is None:
        return None
    # Small model, easily mixes "hide the HUD" with "hide all windows": the
    # words decide between those two.
    if (
        command == "hide_all"
        and not _CORROBORATE["hide_all"].search(t)
        and _CORROBORATE["hide"].search(t)
    ):
        command = "hide"
    floor = MUTE_MIN_CONFIDENCE if command == "mute" else NEEDLE_MIN_CONFIDENCE
    if float(hit.get("confidence") or 0.0) < floor:
        return None
    return command if _CORROBORATE[command].search(t) else None


async def parse_async(text: str, route=None) -> str | None:
    command = parse(text)
    if command is not None:
        return command
    return await asyncio.to_thread(parse_needle, text, route)


# --- HUD mute state ----------------------------------------------------------
# One tiny JSON file shared by the agent, the wake listener and the bridge; the
# HUD reads it through the bridge (GET /hud/state). It never touches the mic.


def state_path() -> Path:
    home = os.environ.get("JARVIS_HOME")
    base = Path(home) if home else Path.home() / ".jarvis"
    return base / "hud_state.json"


def _read_state() -> dict:
    import json

    try:
        data = json.loads(state_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(**changes: bool) -> bool:
    import json
    import tempfile

    path = state_path()
    state = {**_read_state(), **{k: bool(v) for k, v in changes.items()}}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, delete=False, suffix=".tmp"
        ) as fh:
            json.dump(state, fh)
        os.replace(fh.name, path)
        return True
    except OSError:
        return False


def hud_muted() -> bool:
    return bool(_read_state().get("muted"))


def set_hud_muted(muted: bool) -> bool:
    return _write_state(muted=muted)


def hud_breach() -> bool:
    return bool(_read_state().get("breach"))


def set_hud_breach(on: bool) -> bool:
    return _write_state(breach=on)


# --- execution --------------------------------------------------------------


def _shell_argv(verb: str) -> list[list[str]]:
    repo = Path(__file__).resolve().parents[2]
    return [
        [str(repo / "shell" / "src-tauri" / "target" / "debug" / "jarvis-shell"), verb],
        ["jarvis-shell", verb],
        ["jarvis", verb],
    ]


def shell_verb(verb: str, run=subprocess.run) -> bool:
    """Send a CLI verb to the running Jarvis shell (single-instance relay)."""
    for argv in _shell_argv(verb):
        try:
            result = run(argv, timeout=5, capture_output=True, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return True
    return False


def minimize_all(run=subprocess.run, env: dict | None = None) -> bool:
    """Minimise every window via a KWin script (Plasma has no MinimizeAll
    shortcut), else wmctrl per window."""
    env = os.environ if env is None else env
    try:
        from system import kwin_windows

        if kwin_windows.available(env) and kwin_windows.bulk("minimize", run=run):
            return True
    except Exception:
        pass
    try:
        listing = run(
            ["wmctrl", "-l"], timeout=5, capture_output=True, text=True, check=False
        )
        ids = [
            line.split()[0]
            for line in (listing.stdout or "").splitlines()
            if line.strip() and line.split()[1:2] != ["-1"]
        ]
        for wid in ids:
            run(
                ["wmctrl", "-i", "-r", wid, "-b", "add,hidden"],
                timeout=5,
                capture_output=True,
                check=False,
            )
        return bool(ids)
    except (OSError, subprocess.TimeoutExpired):
        return False


def close_all(env: dict | None = None) -> bool:
    """Close every normal window except Jarvis, the shell and terminals."""
    env = os.environ if env is None else env
    try:
        from system import kwin_windows

        return kwin_windows.available(env) and kwin_windows.bulk("close")
    except Exception:
        return False


def ultron_screens(on: bool, page: str = "ultron") -> bool:
    """Open or close the full-screen ULTRON takeover pages on every monitor."""
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "ultron_screen" / "main.py"
    try:
        spec = importlib.util.spec_from_file_location("ultron_screen_main", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.start(page) if on else mod.stop()
    except Exception:
        return False


async def execute(command: str, session=None) -> bool:
    """Run one command silently. True when it took effect."""
    if command == "stop":
        if session is None:
            return False
        fade = getattr(session, "fade_next_flush", None)
        if callable(fade):
            fade(FADE_MS)
        await session.interrupt(force=True)
        return True
    if command in ("mute", "unmute"):
        return await asyncio.to_thread(set_hud_muted, command == "mute")
    if command == "rescue":
        return await asyncio.to_thread(ultron_screens, True, "rescue")
    if command in ("ultron", "ultron_off"):
        return await asyncio.to_thread(ultron_screens, command == "ultron")
    if command in ("breach", "stand_down"):
        ok = await asyncio.to_thread(set_hud_breach, command == "breach")
        if command == "stand_down":
            await asyncio.to_thread(ultron_screens, False)
        if ok and command == "breach":
            # The HUD must be on screen for the show.
            await asyncio.to_thread(shell_verb, "show")
        return ok
    if command == "hide":
        return await asyncio.to_thread(shell_verb, "hide")
    if command == "hide_all":
        return await asyncio.to_thread(minimize_all)
    if command == "close_all":
        return await asyncio.to_thread(close_all)
    return False
