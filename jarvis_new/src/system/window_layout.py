"""Arrange windows on KDE Plasma (KWin scripting), with one-step undo.

IRONMAN_SPEC §3. Layouts are fractions of the screen's work area, computed
here (pure, tested) and applied by a one-shot KWin script. Before moving
anything the script pushes every affected window's current geometry to the
Jarvis D-Bus listener (member "Layout", see active_window), which stores it
under ~/.jarvis/layout_undo/; undo_layout replays the newest snapshot.

Layouts: left, right (the named/active window to half the screen),
split (two windows side by side), grid (all named windows, or every normal
window on the current desktop), thirds, fullscreen (maximize one).
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import subprocess
import time
from pathlib import Path

LAYOUTS = ("left", "right", "split", "grid", "thirds", "fullscreen")
UNDO_KEEP = 20
SCRIPT_NAME = "jarvis-layout"
LAYOUT_MEMBER = "Layout"


def layout_fracs(layout: str, n: int) -> list[tuple[float, float, float, float]]:
    """(x, y, w, h) fractions of the work area for n windows. Pure."""
    n = max(1, int(n))
    if layout == "left":
        return [(0.0, 0.0, 0.5, 1.0)]
    if layout == "right":
        return [(0.5, 0.0, 0.5, 1.0)]
    if layout == "fullscreen":
        return [(0.0, 0.0, 1.0, 1.0)]
    if layout == "split":
        return (
            [(0.0, 0.0, 0.5, 1.0), (0.5, 0.0, 0.5, 1.0)][:n]
            if n <= 2
            else layout_fracs("grid", n)
        )
    if layout == "thirds":
        k = min(n, 3)
        return [(i / k, 0.0, 1 / k, 1.0) for i in range(k)]
    if layout == "grid":
        cols = math.ceil(math.sqrt(n))
        rows = math.ceil(n / cols)
        out = []
        for i in range(n):
            r, c = divmod(i, cols)
            # The last row stretches when it is short.
            in_row = cols if r < rows - 1 else n - cols * (rows - 1)
            w = 1 / in_row
            out.append(
                (
                    c * w if r == rows - 1 else c / cols,
                    r / rows,
                    w if r == rows - 1 else 1 / cols,
                    1 / rows,
                )
            )
        return out
    raise ValueError(f"unknown layout {layout!r}")


def _js(value) -> str:
    return json.dumps(value)


def arrange_script(layout: str, queries: list[str], bus: tuple[str, str, str]) -> str:
    """One-shot KWin script: pick windows, push undo snapshot, place them. Pure."""
    name, path, iface = bus
    want = (
        1
        if layout in ("left", "right", "fullscreen")
        else (2 if layout == "split" else 9)
    )
    fracs = layout_fracs(layout, want if layout != "grid" else 9)
    all_fracs = {str(k): layout_fracs(layout, k) for k in range(1, 10)}
    return f"""
var queries = {_js([q.lower() for q in queries])};
var fracsByN = {_js(all_fracs)};
var maxN = {len(fracs) if layout != "grid" else 9};
function norm(w) {{
  try {{ if (!w.normalWindow || w.minimized) return false; }} catch (e) {{ return false; }}
  try {{ if (w.skipTaskbar) return false; }} catch (e) {{}}
  return true;
}}
function text(w) {{
  return (String(w.caption || "") + " " + String(w.resourceClass || "")).toLowerCase();
}}
var all = workspace.windowList().filter(norm);
var picked = [];
if (queries.length) {{
  queries.forEach(function (q) {{
    for (var i = 0; i < all.length; i++) {{
      if (picked.indexOf(all[i]) < 0 && text(all[i]).indexOf(q) >= 0) {{ picked.push(all[i]); break; }}
    }}
  }});
}} else if (maxN == 1 || {_js(layout == "split")}) {{
  if (workspace.activeWindow && norm(workspace.activeWindow)) picked.push(workspace.activeWindow);
  if ({_js(layout == "split")}) {{
    for (var j = 0; j < all.length && picked.length < 2; j++) if (picked.indexOf(all[j]) < 0) picked.push(all[j]);
  }}
}} else {{
  picked = all.filter(function (w) {{
    try {{ return w.desktops.length == 0 || w.desktops.indexOf(workspace.currentDesktop) >= 0; }} catch (e) {{ return true; }}
  }});
}}
picked = picked.slice(0, maxN);
var snap = picked.map(function (w) {{
  var g = w.frameGeometry;
  return {{id: String(w.internalId), title: String(w.caption || ""), x: g.x, y: g.y, w: g.width, h: g.height,
          max: false}};
}});
callDBus({_js(name)}, {_js(path)}, {_js(iface)}, {_js(LAYOUT_MEMBER)}, JSON.stringify({{layout: {_js(layout)}, windows: snap}}));
var fr = fracsByN[String(Math.max(1, Math.min(9, picked.length)))] || [];
picked.forEach(function (w, i) {{
  var f = fr[i]; if (!f) return;
  var a = workspace.clientArea(KWin.MaximizeArea, w);
  try {{ w.setMaximize(false, false); }} catch (e) {{}}
  w.frameGeometry = {{x: Math.round(a.x + f[0] * a.width), y: Math.round(a.y + f[1] * a.height),
                      width: Math.round(f[2] * a.width), height: Math.round(f[3] * a.height)}};
}});
if (picked.length) workspace.activeWindow = picked[0];
"""


def restore_script(snapshot: dict) -> str:
    """KWin script putting windows back where a snapshot says. Pure."""
    return f"""
var snap = {_js(snapshot.get("windows") or [])};
var byId = {{}};
workspace.windowList().forEach(function (w) {{ byId[String(w.internalId)] = w; }});
snap.forEach(function (s) {{
  var w = byId[s.id]; if (!w) return;
  w.frameGeometry = {{x: s.x, y: s.y, width: s.w, height: s.h}};
}});
"""


# --- undo snapshots ---


def undo_dir() -> Path:
    h = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(h) if h else Path.home() / ".jarvis") / "layout_undo"


def note_snapshot(raw: str, now: float | None = None) -> bool:
    """Store one Layout push from the KWin script. Never raises."""
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or not data.get("windows"):
            return False
        undo_dir().mkdir(parents=True, exist_ok=True)
        stamp = int((time.time() if now is None else now) * 1000)
        (undo_dir() / f"{stamp}.json").write_text(json.dumps(data))
        for old in sorted(undo_dir().glob("*.json"))[:-UNDO_KEEP]:
            with contextlib.suppress(OSError):
                old.unlink()
        return True
    except (ValueError, OSError):
        return False


def latest_snapshot() -> tuple[Path, dict] | None:
    with contextlib.suppress(OSError, ValueError):
        files = sorted(undo_dir().glob("*.json"))
        if files:
            return files[-1], json.loads(files[-1].read_text())
    return None


# --- running scripts ---


def run_script(js: str, name: str = SCRIPT_NAME, run=subprocess.run) -> bool:
    """Load + start a one-shot KWin script, then unload it. Fail-soft."""
    h = os.environ.get("JARVIS_HOME", "").strip()
    path = (Path(h) if h else Path.home() / ".jarvis") / "kwin" / f"{name}.js"

    def q(*args: str) -> str:
        proc = run(
            ["qdbus", "org.kde.KWin", "/Scripting", *args],
            capture_output=True,
            text=True,
            timeout=8,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(js)
        q("org.kde.kwin.Scripting.unloadScript", name)
        sid = q("org.kde.kwin.Scripting.loadScript", str(path), name)
        q("org.kde.kwin.Scripting.start")
        time.sleep(0.4)
        q("org.kde.kwin.Scripting.unloadScript", name)
        return sid not in ("", "-1")
    except Exception:
        return False


def arrange(layout: str, queries: list[str], run=subprocess.run) -> bool:
    import active_window

    with contextlib.suppress(Exception):
        active_window.ensure_listener()
    bus = (active_window.BUS_NAME, active_window.BUS_PATH, active_window.BUS_IFACE)
    return run_script(arrange_script(layout, queries, bus), run=run)


def undo(run=subprocess.run) -> dict | None:
    """Replay the newest snapshot; consumes it. Returns it, or None."""
    found = latest_snapshot()
    if found is None:
        return None
    path, snap = found
    if not run_script(restore_script(snap), f"{SCRIPT_NAME}-undo", run=run):
        return None
    with contextlib.suppress(OSError):
        path.unlink()
    return snap
