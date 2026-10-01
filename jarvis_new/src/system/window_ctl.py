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
    sleep(settle_s)
    now = active()
    # Confirm only when the desktop actually reports the front window: a
    # reading that shares nothing with the match means it didn't come forward.
    if (
        now
        and not (_tokens(title) & _tokens(now))
        and not (_tokens(tries[0]) & _tokens(now))
    ):
        return (
            False,
            f"I asked for {title[:60]}, but {now[:60]} is still in front, Sir.",
        )
    return True, f"{title[:70]} is in front."


def restore(title: str, **kw) -> bool:
    """Put a previously active window back in front. Best effort."""
    if not (title or "").strip():
        return False
    ok, _ = focus(title.strip()[:80], **kw)
    return ok
