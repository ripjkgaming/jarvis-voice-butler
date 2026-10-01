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

import contextlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

SCHOOL = "school"
NORMAL = "normal"

WAKE_THRESHOLD = 0.65
WAKE_FRAMES = 2  # consecutive openWakeWord frames at or above the threshold
FOLLOW_UP_S = 8.0
# After "Are you sure?" the call waits this long for Sir's answer: at 8 s
# a quiet "yes" that the VAD missed let the call close mid-question.
CONFIRM_WAIT_S = 15.0
SCHOOL_VOLUME = 0.35  # gain on Jarvis's voice through the wake client
# Stricter than normal (coughs, chairs), but a short "yes" must still pass:
# 0.25 s / 0.7 dropped one-word answers.
VAD = {"min_speech_duration": 0.15, "activation_threshold": 0.6}
LOUD_CONFIRM_S = 30.0

_cache: dict = {"path": None, "mtime": None, "mode": NORMAL}
_confirmed_at = {"at": 0.0}
_asked_at = {"at": 0.0}


def mark_asked(now: float | None = None) -> None:
    """Jarvis just asked Sir to confirm a loud action."""
    _asked_at["at"] = now if now is not None else time.monotonic()


def awaiting_answer(now: float | None = None) -> bool:
    """A confirmation question is open (asked recently, not yet answered)."""
    now = now if now is not None else time.monotonic()
    at = _asked_at["at"]
    return bool(at) and now - at <= CONFIRM_WAIT_S + 10.0 and _confirmed_at["at"] < at


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


def since(home: Path | None = None) -> float:
    """Epoch of the last mode switch (0.0 when unknown). Never raises."""
    try:
        return float(json.loads(mode_path(home).read_text()).get("since", 0.0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0


#: A model call reversing a switch made this recently is refused: the
#: realtime model once entered school mode and undid it 2 s later in the
#: same turn, which aborted the entry transition mid-way.
FLIP_GUARD_S = 20.0


def flip_refusal(want: str, home: Path | None = None, now: float | None = None) -> str | None:
    """Why a model-initiated switch to `want` should not run, else None. Pure-ish.

    "already" when the mode already matches (no-op, no transition), a
    refusal text when it reverses a switch made < FLIP_GUARD_S ago.
    """
    want = SCHOOL if want == SCHOOL else NORMAL
    if current(home) == want:
        return "already"
    now = time.time() if now is None else now
    if now - since(home) < FLIP_GUARD_S:
        return (
            "School mode was switched moments ago; do NOT switch it back. "
            "Only call this again if Sir explicitly says to exit/enter school mode."
        )
    return None


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


# --- Meta (Super) key in school mode ---------------------------------------
#
# In school mode Jarvis replaces the Plasma panel with its own taskbar, so
# pressing Meta alone must open the Jarvis start menu instead of Plasma's
# Kickoff. KDE's Meta-alone action lives in ~/.config/kwinrc group
# [ModifierOnlyShortcuts] key Meta (absent = the Kickoff default
# "org.kde.plasmashell,/PlasmaShell,org.kde.PlasmaShell,
# activateLauncherMenu"). Format: service,path,interface,method. After
# writing it KWin must reconfigure:
#   dbus-send --session --type=method_call --dest=org.kde.KWin /KWin
#   org.kde.KWin.reconfigure
# (Verified live: kreadconfig6 reads the key, qdbus answers KWin, and the
# bridge owns org.jarvis.Focus at /org/jarvis/Focus.)
#
# Every helper is fail-soft (never raises), takes an injectable
# subprocess.run-shaped `run` and `home` (backup location), so tests never
# touch the live kwinrc.

#: D-Bus target KWin calls on Meta-alone while in school mode: the
#: bridge's own listener (active_window.handle_dbus_message "StartMenu").
META_JARVIS_VALUE = "org.jarvis.Focus,/org/jarvis/Focus,org.jarvis.Focus,StartMenu"

#: What an absent kwinrc Meta key means (KDE default when the key is missing).
META_DEFAULT_KICKOFF = (
    "org.kde.plasmashell,/PlasmaShell,org.kde.PlasmaShell,activateLauncherMenu"
)


def meta_backup_path(home: Path | None = None) -> Path:
    """Where the pre-school Meta value is stashed. Pure (env)."""
    base = home or Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
    return base / "meta_shortcut_backup.json"


def _meta_run(argv: list[str], run=None):
    runner = run or subprocess.run
    return runner(argv, capture_output=True, text=True, timeout=5.0)


def read_meta_shortcut(run=None) -> str:
    """Current kwinrc [ModifierOnlyShortcuts] Meta value ("" = absent).

    Never raises ("": absent or unreadable — both restore as absent).
    """
    try:
        proc = _meta_run(
            ["kreadconfig6", "--file", "kwinrc",
             "--group", "ModifierOnlyShortcuts", "--key", "Meta"],
            run,
        )
        if proc.returncode != 0:
            return ""
        return (proc.stdout or "").strip()
    except Exception:
        return ""


def _meta_reconfigure(run=None) -> None:
    with contextlib.suppress(Exception):
        _meta_run(
            ["dbus-send", "--session", "--type=method_call",
             "--dest=org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure"],
            run,
        )


def meta_enter_school(run=None, home: Path | None = None) -> bool:
    """Point Meta-alone at the Jarvis start menu. Never raises.

    Saves the current kwinrc Meta value (even when absent) to the backup
    file — only when no backup exists yet, so a double-enter keeps the
    original — then writes META_JARVIS_VALUE and reconfigures KWin.
    """
    try:
        backup = meta_backup_path(home)
        if not backup.exists():
            current_value = read_meta_shortcut(run=run)
            try:
                backup.parent.mkdir(parents=True, exist_ok=True)
                tmp = backup.with_suffix(".tmp")
                tmp.write_text(
                    json.dumps({"had_value": bool(current_value),
                                "value": current_value})
                )
                tmp.replace(backup)
            except (OSError, ValueError):
                pass
        proc = _meta_run(
            ["kwriteconfig6", "--file", "kwinrc",
             "--group", "ModifierOnlyShortcuts", "--key", "Meta",
             META_JARVIS_VALUE],
            run,
        )
        ok = proc.returncode == 0
    except Exception:
        ok = False
    _meta_reconfigure(run=run)
    return ok


def meta_exit_school(run=None, home: Path | None = None) -> bool:
    """Restore the pre-school Meta-alone action. Never raises.

    Restores the backup exactly (deletes the key when it was absent),
    removes the backup file, and reconfigures KWin. With no backup file
    only our own value is cleared (back to the absent default).
    """
    try:
        backup = meta_backup_path(home)
        had_value, value = False, ""
        if backup.exists():
            try:
                data = json.loads(backup.read_text())
                had_value = bool(data.get("had_value"))
                value = str(data.get("value") or "")
            except (OSError, ValueError, AttributeError):
                had_value, value = False, ""
            with contextlib.suppress(OSError):
                backup.unlink()
        elif read_meta_shortcut(run=run) != META_JARVIS_VALUE:
            return True
        if had_value:
            proc = _meta_run(
                ["kwriteconfig6", "--file", "kwinrc",
                 "--group", "ModifierOnlyShortcuts", "--key", "Meta", value],
                run,
            )
            ok = proc.returncode == 0
        else:
            try:
                proc = _meta_run(
                    ["kwriteconfig6", "--file", "kwinrc",
                     "--group", "ModifierOnlyShortcuts", "--key", "Meta",
                     "--delete"],
                    run,
                )
                ok = proc.returncode == 0
            except Exception:
                ok = False
    except Exception:
        ok = False
    _meta_reconfigure(run=run)
    return ok


def meta_sync_on_startup(run=None, home: Path | None = None) -> None:
    """Reconcile the Meta shortcut with the stored mode. Never raises.

    School mode ensures the Jarvis value (keeps an existing backup, so a
    restart mid-school never loses the original); normal mode with a
    leftover backup file (crash between enter and exit) restores it.
    """
    try:
        if current(home) == SCHOOL:
            meta_enter_school(run=run, home=home)
        elif meta_backup_path(home).exists():
            meta_exit_school(run=run, home=home)
    except Exception:
        pass


_ENTER = re.compile(
    r"^(?:(?:please )?(?:enter|start|turn on|switch on|switch to|activate|go into|get into"
    r"|begin|open|launch|enable|put on|put me in|go to|use) )?"
    r"(?:the )?school mode(?: on| please| now)?$|^(?:turn |switch )?school mode on$"
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
    r"|\b(?:jarvis|jarvus|javis|jafis|jervis)\s*[,!?.:]"
    # The whole turn is just the name ("Jarvis"): nothing else was said, so
    # it is a call, not chatter mentioning him.
    r"|^\W*(?:jarvis|jarvus|javis|jafis|jervis)\W*$"
    # Whisper hearing Sir's "hey" as another short word ("He Jarvis",
    # "Your Jarvis", "Be Jarvis"). Only at the very start of the turn, like
    # _MISHEARD: a sentence that merely begins "Jarvis ..." still needs the
    # comma pause.
    r"|^\W*(?:he|hay|hei|heh|her|hate|be|bee|a|ay|aye|eh|your|you're|yeah|say|hej)"
    r"[\s,.!]+(?:jarvis|jarvus|javis|jafis|jervis)\b",
    re.I,
)
# Whisper's usual mishearings of Sir's "hey Jarvis" ("He's nervous.", "Are
# you nervous?"). Only at the very start: the wake model has already fired
# on the audio, so this is the wake phrase, never chatter mid-sentence.
_MISHEARD = re.compile(
    r"^\W*(?:(?:hey|hi|he's|hes|a|are you|hey you|in)[\s,]+)?"
    r"(?:nervous|nervis|jarvice|jarvi)\b",
    re.I,
)


def addressed(text: str) -> bool:
    """Is this turn actually spoken to Jarvis? Pure.

    openWakeWord fires on a bare "Jarvis" ("Jarvis Cocker was a singer"),
    so a school call only answers a first turn that addresses Jarvis:
    "hey Jarvis ..." or "Jarvis, ..." (the transcript's comma is the pause).
    """
    return bool(_ADDRESSED.search(text or "") or _MISHEARD.match(text or ""))


_ADDRESS_PREFIX = re.compile(
    r"^.*?\b(?:(?:hey|hi|ok|okay|yo|hello)[\s,]+)?(?:jarvis|jarvus|javis|jafis|jervis)\b[\s,.!?:]*",
    re.I,
)


def question_after_address(text: str) -> str:
    """What Sir asked after "hey Jarvis," ("" if nothing). Pure."""
    text = (text or "").strip()
    misheard = _MISHEARD.match(text)
    if misheard and not _ADDRESSED.search(text):
        rest = text[misheard.end():].lstrip(" ,.!?:")
    else:
        rest = _ADDRESS_PREFIX.sub("", text, count=1).strip()
    return rest if re.search(r"[a-z0-9]", rest, re.I) else ""


# --- loud actions ---------------------------------------------------------

# School mode gates exactly one thing: launching Sober (Roblox). Music,
# volume, YouTube, Spotify and everything else work as normal — the mode
# only quiets Jarvis's own chatter and the HUD.
_LOUD_APPS = ("sober", "roblox")


def is_loud(tool: str, args: dict | None = None) -> bool:
    """Is this the one action school mode confirms first (launching
    Sober / Roblox)? Pure."""
    args = args or {}
    if tool == "open_app":
        name = f"{args.get('app', '')} {args.get('url', '')}".lower()
        return any(a in name for a in _LOUD_APPS)
    if tool == "play_game":
        return str(args.get("game", "roblox")).strip().lower() in _LOUD_APPS
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
    "School mode: launching Sober needs a yes first. Ask Sir (\"Are you sure, "
    "Sir? That's Roblox in school mode.\"). Only if Sir says yes, call "
    "confirm_loud_action, then call this tool again."
)
