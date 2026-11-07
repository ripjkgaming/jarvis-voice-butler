"""Switch to a window, and keep new ones in the background.

focus_window used to talk to KWin only (silent on X11) and never checked
the window really came forward. Here:

- ``focus(query)``  pulls a window to the front: KWin on Plasma Wayland,
  ``wmctrl -a`` elsewhere, with friendly aliases ("browser" -> brave,
  "terminal" -> konsole, "roblox" -> sober), then confirms from the
  active-window reading when one is available.
- ``active_title()`` / ``restore(title)`` let a launcher (play_media) put
  Sir's previous window back in front once the new one has opened, so
  music starts in the background instead of covering what he was doing.

Every function is fail-soft with injectable runners; nothing raises.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time

ALIASES: dict[str, tuple[str, ...]] = {
    "browser": ("brave", "firefox", "chrome", "chromium"),
    "internet": ("brave", "firefox", "chrome"),
    "web": ("brave", "firefox", "chrome"),
    "terminal": ("konsole", "terminal"),
    "console": ("konsole",),
    "files": ("dolphin", "files"),
    "file manager": ("dolphin", "files"),
    "roblox": ("sober", "roblox"),
    "music": ("spotify", "youtube"),
    "code": ("code", "visual studio", "vscode"),
    "editor": ("code", "kate"),
    "claude": ("claude", "konsole"),
    "resolve": ("resolve", "davinci"),
}

_FILLER = re.compile(r"^(?:the |my |a |an )+|(?: window| app| application| tab)+$")


def candidates(query: str) -> list[str]:
    """Queries to try, best first: as said, then known aliases. Pure."""
    q = _FILLER.sub("", " ".join((query or "").lower().split())).strip()
    if not q:
        return []
    out = [q]
    for alias in ALIASES.get(q, ()):
        if alias not in out:
            out.append(alias)
    return out


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", (text or "").lower()))


def active_title() -> str:
    """Caption of the window in front now ('' if unknown)."""
    try:
        import active_window

        info = active_window.active() or {}
        return str(info.get("title") or info.get("app") or "")
    except Exception:
        return ""


def _focus_kwin(query: str, run=subprocess.run) -> str | None:
    from system import kwin_windows

    if not kwin_windows.available():
        return None
    hit = kwin_windows.act("focus", query, run=run)
    return hit[0] if hit else None


def _focus_wmctrl(query: str, run=subprocess.run, which=shutil.which) -> str | None:
    if which("wmctrl") is None:
        return None
    try:
        proc = run(["wmctrl", "-a", query], capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    return query if getattr(proc, "returncode", 1) == 0 else None


def _came_forward(
    title: str, tries: list[str], active, sleep, settle_s: float = 0.4
) -> tuple[bool, str]:
    """Poll the front-window reading until it is the match -> (came, reading).

    Poll instead of one fixed settle: a window already in front confirms on
    the first 50ms tick. Confirm only when the desktop reports the matched
    window (or an unreadable desktop, accepted as before).
    """
    step = 0.05
    now = ""
    for _ in range(max(1, round(settle_s / step))):
        sleep(step)
        now = active()
        if (
            not now
            or (_tokens(title) & _tokens(now))
            or (_tokens(tries[0]) & _tokens(now))
        ):
            return True, now
    return False, now


def focus(
    query: str,
    run=subprocess.run,
    which=shutil.which,
    active=active_title,
    settle_s: float = 0.4,
    sleep=time.sleep,
) -> tuple[bool, str]:
    """Bring a window to the front -> (ok, spoken line). Never raises."""
    tries = candidates(query)
    if not tries:
        return False, "Which window, Sir?"
    title = None
    for q in tries:
        title = _focus_kwin(q, run=run) or _focus_wmctrl(q, run=run, which=which)
        if title:
            break
    if not title:
        return False, f"I can't find a window matching {tries[0][:60]}, Sir."
    came, now = _came_forward(title, tries, active, sleep, settle_s)
    if not came:
        return (
            False,
            f"I asked for {title[:60]}, but {now[:60]} is still in front, Sir.",
        )
    return True, f"{title[:70]} is in front."


# --- window state: maximise, full screen, restore, attention ---

#: What each state change is called when spoken, and the KWin check for it.
_STATE_WORDS = {
    "maximize": "maximised",
    "fullscreen": "full screen",
    "restore": "back to normal size",
    "attention": "in front",
}


def state_script(uuid: str, mode: str) -> str:
    """One-shot KWin script: un-minimise, activate, then apply `mode`. Pure.

    The window is matched by its KWin internalId, which equals the
    WindowsRunner uuid once braces and case are dropped.
    """
    target = (uuid or "").strip("{} ").lower()
    return f"""
var target = {json.dumps(target)};
var mode = {json.dumps(mode)};
var w = null;
workspace.windowList().forEach(function (c) {{
  if (!w && String(c.internalId).replace(/[{{}}]/g, "").toLowerCase() === target) w = c;
}});
if (w) {{
  try {{ w.minimized = false; }} catch (e) {{}}
  if (mode == "maximize") {{ w.fullScreen = false; w.setMaximize(true, true); }}
  if (mode == "fullscreen") {{ w.fullScreen = true; }}
  if (mode == "restore") {{ w.fullScreen = false; w.setMaximize(false, false); }}
  if (mode == "attention") {{ try {{ w.demandsAttention = true; }} catch (e) {{}} }}
  workspace.activeWindow = w;
}}
"""


def parse_window_info(out: str) -> dict[str, str]:
    """`qdbus ... getWindowInfo` text ('key: value' lines) -> dict. Pure."""
    info: dict[str, str] = {}
    for line in (out or "").splitlines():
        key, sep, value = line.partition(": ")
        if sep and key.isidentifier():
            info[key] = value.strip()
    return info


def _flag(value: str | None) -> bool:
    v = (value or "").strip().lower()
    return v == "true" or (v.isdigit() and int(v) > 0)


def window_state(uuid: str, run=subprocess.run) -> dict[str, str] | None:
    """KWin's getWindowInfo for one window, or None when unreadable."""
    try:
        proc = run(
            ["qdbus", "org.kde.KWin", "/KWin", "org.kde.KWin.getWindowInfo", uuid],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    return parse_window_info(proc.stdout) or None


def _state_ok(mode: str, info: dict[str, str]) -> bool:
    maximized = _flag(info.get("maximizeHorizontal")) and _flag(
        info.get("maximizeVertical")
    )
    fullscreen = _flag(info.get("fullscreen"))
    if mode == "maximize":
        return maximized and not fullscreen
    if mode == "fullscreen":
        return fullscreen
    if mode == "attention":
        return not _flag(info.get("minimized"))
    return not maximized and not fullscreen  # restore


def _kwin_on() -> bool:
    try:
        from system import kwin_windows

        return kwin_windows.available()
    except Exception:
        return False


def _find_kwin(tries: list[str], run) -> tuple[str, str] | None:
    """(uuid, title) of the best KWin match, same order as focus(). Never raises."""
    from system import kwin_windows

    for q in tries:
        try:
            wins = kwin_windows.find(q, run=run)
        except Exception:
            wins = []
        if wins:
            return wins[0]
    return None


def _set_state(
    title: str,
    mode: str,
    run=subprocess.run,
    which=shutil.which,
    active=active_title,
    sleep=time.sleep,
) -> tuple[bool, str]:
    """Shared body of maximize / fullscreen / restore_window / attention."""
    tries = candidates(title)
    if not tries:
        return False, "Which window, Sir?"
    word = _STATE_WORDS[mode]
    try:
        if _kwin_on():
            from system import window_layout

            hit = _find_kwin(tries, run)
            if hit is None:
                return False, f"I can't find a window matching {tries[0][:60]}, Sir."
            uuid, name = hit
            if not window_layout.run_script(
                state_script(uuid, mode), window_layout.SCRIPT_NAME + "-state", run=run
            ):
                return False, f"KWin didn't take the change for {name[:60]}, Sir."
            # Read the state back from KWin itself (a few short polls).
            info = None
            matched = None
            for _ in range(10):
                info = window_state(uuid, run=run)
                if info is None:
                    break
                matched = _state_ok(mode, info)
                if matched:
                    break
                sleep(0.1)
            came, now = _came_forward(name, tries, active, sleep)
            if not came:
                return (
                    False,
                    f"{name[:60]} is not in front, {now[:60]} is, Sir.",
                )
            if info is None:
                return (
                    True,
                    f"I asked for {name[:70]} to be {word}, Sir, but I can't read "
                    "the window state back from KWin to confirm it.",
                )
            if not matched:
                return (
                    False,
                    f"I asked for {name[:60]} to be {word}, but KWin reports it otherwise, Sir.",
                )
            return True, f"{name[:70]} is {word}."
        # Not Plasma Wayland: wmctrl can raise and (on X11) set the state.
        if mode == "attention":
            return focus(title, run=run, which=which, active=active, sleep=sleep)
        if which("wmctrl") is None:
            return (
                False,
                "I can only change window size under KWin or wmctrl, Sir, and neither is here.",
            )
        props = {
            "maximize": "add,maximized_vert,maximized_horz",
            "fullscreen": "add,fullscreen",
            "restore": "remove,maximized_vert,maximized_horz,fullscreen",
        }[mode]
        for q in tries:
            try:
                proc = run(
                    ["wmctrl", "-r", q, "-b", props],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except Exception:
                continue
            if getattr(proc, "returncode", 1) == 0:
                return (
                    True,
                    f"Sent {q[:60]} {word}, Sir, but this desktop can't read the state back to confirm it.",
                )
        return False, f"I can't find a window matching {tries[0][:60]}, Sir."
    except Exception:
        return False, "Window control failed, Sir."


def maximize(
    title: str,
    fullscreen: bool = False,
    run=subprocess.run,
    which=shutil.which,
    active=active_title,
    sleep=time.sleep,
) -> tuple[bool, str]:
    """Raise a window and maximise it (or full screen) -> (ok, spoken line).

    ok is True only when KWin reads the new state back; when the state
    cannot be read it is True with a line saying it is unconfirmed.
    """
    return _set_state(
        title, "fullscreen" if fullscreen else "maximize", run, which, active, sleep
    )


def restore_window(
    title: str,
    run=subprocess.run,
    which=shutil.which,
    active=active_title,
    sleep=time.sleep,
) -> tuple[bool, str]:
    """Un-maximise / leave full screen and raise (not the same as restore()).

    `restore(title)` above puts a previous window back in front; this one
    changes the window's own size state back to normal.
    """
    return _set_state(title, "restore", run, which, active, sleep)


def attention(
    title: str,
    run=subprocess.run,
    which=shutil.which,
    active=active_title,
    sleep=time.sleep,
) -> tuple[bool, str]:
    """Bring a window that is behind others to the front -> (ok, line).

    KWin: un-minimise, activate (scripts may raise under Wayland's focus
    rules), set demandsAttention, then confirm from the front-window reading.
    Elsewhere it is focus() via wmctrl.
    """
    return _set_state(title, "attention", run, which, active, sleep)


def restore(title: str, **kw) -> bool:
    """Put a previously active window back in front. Best effort."""
    if not (title or "").strip():
        return False
    ok, _ = focus(title.strip()[:80], **kw)
    return ok


def brave_window_ids(run=subprocess.run) -> set[str] | None:
    """KWin uuids of open Brave windows, or None off Plasma Wayland."""
    try:
        from system import kwin_windows

        if not kwin_windows.available():
            return None
        return {uuid for uuid, _ in kwin_windows.find("brave", run=run)}
    except Exception:
        return None


def reveal_new_brave(
    before: set[str],
    previous: str = "",
    run=subprocess.run,
    sleep=time.sleep,
    wait_s: float = 10.0,
    tries: int = 3,
    active=active_title,
) -> dict:
    """Keep the Brave window(s) opened since `before` visible, in front.

    Never minimises and never refocuses `previous` (that buried the player).
    For each new window: un-minimise + activate via KWin, then read the state
    back (minimized false and the front window's title matches); retry up to
    `tries` times over ~3 s. Returns {"found", "visible", "front", "title"};
    visible/front are True only when KWin confirmed them. Never raises.
    """
    out = {"found": False, "visible": False, "front": False, "title": ""}
    try:
        from system import window_layout

        new: list[str] = []
        for _ in range(max(1, int(wait_s / 0.25))):
            sleep(0.25)
            new = sorted((brave_window_ids(run=run) or set()) - before)
            if new:
                break
        if not new:
            return out
        out["found"] = True
        uuid = new[-1]
        sleep(0.5)  # let the first frame map before asking KWin to raise it
        for attempt in range(max(1, tries)):
            window_layout.run_script(
                state_script(uuid, "attention"),
                window_layout.SCRIPT_NAME + "-state",
                run=run,
            )
            sleep(0.4)
            info = window_state(uuid, run=run)
            if info is not None:
                out["title"] = info.get("caption", "")
                out["visible"] = not _flag(info.get("minimized"))
            now = active() or ""
            cap = out["title"]
            out["front"] = bool(
                cap and now and (_tokens(cap) & _tokens(now) or "brave" in now.lower())
            )
            if out["visible"] and out["front"]:
                break
            if attempt + 1 < tries:
                sleep(0.8)
        return out
    except Exception:
        return out


def background_new_brave(before: set[str], previous: str = "", **kw) -> bool:
    """Compat name: now reveals (never minimises). True if a window was found."""
    return bool(reveal_new_brave(before, previous, **kw)["found"])
