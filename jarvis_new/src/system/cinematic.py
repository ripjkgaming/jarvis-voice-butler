"""Optional, bounded visual cues for Jarvis actions on the owner's compositor.

Only a resolved window or a specific app class is tagged. No global slow mode,
no action dispatch, and no sleeps: missing desktop support is a harmless no-op.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path


def enabled() -> bool:
    return (
        not os.environ.get("PYTEST_CURRENT_TEST")
        and not os.environ.get("JARVIS_DESKTOP_SANDBOX")
        and bool(os.environ.get("WAYLAND_DISPLAY"))
        and (Path.home() / ".local/share/kwin/effects/jarvis-energy").exists()
    )


def _emit(action, *, run=subprocess.run, **identity):
    request = dict(
        action=action,
        token=uuid.uuid4().hex,
        expires=int(time.time() * 1000) + 8000,
        **identity,
    )
    payload = json.dumps(request, separators=(",", ":"))
    if (Path.home() / ".local/share/kwin/effects/jarvis-ascii").exists():
        # Best effort: the ASCII transition effect reads the same cue.
        for argv in [
            ["kwriteconfig6", "--file", "kwinrc", "--group", "Effect-jarvis-ascii",
             "--key", "ActionRequest", payload],
            ["qdbus", "org.kde.KWin", "/Effects",
             "org.kde.kwin.Effects.reconfigureEffect", "jarvis-ascii"],
        ]:
            try:
                run(argv, capture_output=True, text=True, timeout=0.8)
            except Exception:
                break
    for argv in [
        [
            "kwriteconfig6",
            "--file",
            "kwinrc",
            "--group",
            "Effect-jarvis-energy",
            "--key",
            "ActionRequest",
            payload,
        ],
        [
            "qdbus",
            "org.kde.KWin",
            "/Effects",
            "org.kde.kwin.Effects.reconfigureEffect",
            "jarvis-energy",
        ],
    ]:
        if run(argv, capture_output=True, text=True, timeout=0.8).returncode:
            return False
    return True


def mark_window(action, window_id, *, run=subprocess.run):
    if (
        action not in ("close", "focus", "minimize", "maximize", "interact")
        or not enabled()
    ):
        return False
    try:
        result = run(
            [
                "busctl",
                "--user",
                "--json=short",
                "call",
                "org.kde.KWin",
                "/KWin",
                "org.kde.KWin",
                "getWindowInfo",
                "s",
                window_id,
            ],
            capture_output=True,
            text=True,
            timeout=0.8,
        )
        info = json.loads(result.stdout)["data"][0]
        pid, caption = info["pid"]["data"], info["caption"]["data"]
        if not pid or not caption:
            return False
        return _emit(action, pid=pid, caption=caption, run=run)
    except Exception:
        return False


def launch_classes(argv):
    if not argv:
        return []
    binary = Path(argv[0]).name.lower()
    if binary == "flatpak" and len(argv) > 2 and argv[1] == "run":
        app = next((v.lower() for v in argv[2:] if not v.startswith("-")), "")
    else:
        app = binary
    aliases = {
        "com.brave.browser": ["brave-browser", "brave"],
        "brave-browser": ["brave"],
        "brave": ["brave-browser"],
        "org.kde.dolphin": ["dolphin"],
        "org.kde.konsole": ["konsole"],
    }
    if app in ("sh", "bash", "env", "python", "python3", "xdg-open", "flatpak"):
        return []
    return [app, *aliases.get(app, [])]


def mark_launch(argv):
    if not enabled():
        return False
    try:
        classes = launch_classes(argv)
        return bool(classes) and _emit("open", classes=classes)
    except Exception:
        return False


def mark_geometry(windows):
    """Tag the exact windows selected by the native arrange/undo script."""
    if not enabled():
        return False
    try:
        identities = [
            {"pid": int(w["pid"]), "caption": str(w["caption"])}
            for w in windows[:9]
            if int(w.get("pid", 0)) > 0 and w.get("caption")
        ]
        return bool(identities) and _emit("geometry", windows=identities)
    except Exception:
        return False
