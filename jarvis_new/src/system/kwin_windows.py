"""Window control on KDE Plasma Wayland (wmctrl only sees X11 windows).

KWin exports its KRunner "Windows" plugin on D-Bus (/WindowsRunner):
Match(query) returns every window whose title or app matches, with ids
like "0_{uuid}"; the leading number is KWin's action (0 activate, 1 close,
2 minimize, 3 maximize), so Run("1_{uuid}") closes that exact window.
Verified live on Plasma 6.7: wmctrl listed nothing and "close Dolphin"
sent the agent into a screenshot loop; this closed it directly.
"""

from __future__ import annotations

import os
import re
import subprocess

ACTIONS = {"focus": 0, "close": 1, "minimize": 2, "maximize": 3}
_MATCH = re.compile(r'string "\d+_(\{[0-9a-fA-F-]+\})"\s*\n\s*string "(.*)"')


def available(env: dict | None = None) -> bool:
    """Plasma Wayland session (where wmctrl is blind). Pure (env only)."""
    env = os.environ if env is None else env
    return bool(env.get("WAYLAND_DISPLAY")) and "KDE" in env.get("XDG_CURRENT_DESKTOP", "")


def parse_matches(out: str) -> list[tuple[str, str]]:
    """(uuid, title) pairs from a dbus-send Match reply. Pure."""
    return [(u, t.replace('\\"', '"')) for u, t in _MATCH.findall(out or "")]


def _dbus(method: str, *args: str, run=subprocess.run) -> str:
    proc = run(
        [
            "dbus-send", "--session", "--print-reply", "--dest=org.kde.KWin",
            "/WindowsRunner", f"org.kde.krunner1.{method}", *args,
        ],
        capture_output=True, text=True, timeout=5,
    )
    return proc.stdout if proc.returncode == 0 else ""


def find(query: str, run=subprocess.run) -> list[tuple[str, str]]:
    """Windows matching a title/app fragment, best first."""
    q = (query or "").strip()
    if not q:
        return []
    return parse_matches(_dbus("Match", f"string:{q}", run=run))


def act(action: str, query: str, run=subprocess.run) -> tuple[str, int] | None:
    """Apply action to the best match. Returns (title, total matches) or None."""
    code = ACTIONS[action]
    wins = find(query, run=run)
    if not wins:
        return None
    uuid, title = wins[0]
    _dbus("Run", f"string:{code}_{uuid}", "string:", run=run)
    return title, len(wins)
