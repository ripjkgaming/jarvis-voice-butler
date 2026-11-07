"""Move an existing application window between KDE Plasma displays.

KWin owns Wayland windows. Unlike open_on_monitor, this explicitly moves
the matched *existing* window; for XWayland apps, verify the resulting
coordinates rather than accepting script-load as proof of success.

Window matching: Sir says the app family ("terminal", "browser"), not the
desktop id. Each window is matched on its caption, resourceName,
resourceClass and desktopFile (e.g. konsole = "org.kde.konsole"), with the
generic words expanded through GENERIC_APPS. Identity matches beat title
matches, and the active window wins among equals.
"""

from __future__ import annotations

import json
import re
import subprocess
import time

from system.desktop import parse_kscreen_outputs
from system import kwin_windows, window_ctl, window_layout

# Generic word -> app identities (resourceName / desktopFile / class).
# Extends window_ctl.ALIASES; "claude" -> konsole is dropped on purpose,
# because moving the terminal when Claude is not open is the wrong window.
GENERIC_APPS: dict[str, tuple[str, ...]] = {
    "terminal": (
        "konsole",
        "kitty",
        "alacritty",
        "foot",
        "wezterm",
        "ghostty",
        "xterm",
        "gnome-terminal",
        "yakuake",
        "terminal",
    ),
    "console": ("konsole", "yakuake", "kitty"),
    "browser": (
        "brave",
        "firefox",
        "chromium",
        "chrome",
        "google-chrome",
        "vivaldi",
        "zen",
        "opera",
        "microsoft-edge",
    ),
    "internet": ("brave", "firefox", "chromium", "chrome", "vivaldi", "zen"),
    "web": ("brave", "firefox", "chromium", "chrome", "vivaldi", "zen"),
    "editor": ("kate", "kwrite", "code", "vscodium", "cursor", "zed", "gedit"),
    "code": ("code", "vscodium", "cursor", "zed", "kate"),
    "files": ("dolphin", "nautilus", "thunar", "nemo", "files"),
    "file manager": ("dolphin", "nautilus", "thunar", "nemo", "files"),
    "music": ("spotify", "elisa", "youtube"),
    "video": ("vlc", "mpv", "haruna", "celluloid", "totem"),
    "player": ("vlc", "mpv", "haruna", "elisa", "spotify"),
    "notes": ("obsidian", "kate", "joplin", "logseq", "notes"),
    "mail": ("thunderbird", "kmail", "evolution", "mail"),
    "email": ("thunderbird", "kmail", "evolution", "mail"),
    "calculator": ("kcalc", "gnome-calculator", "calculator"),
    "settings": ("systemsettings", "gnome-control-center"),
    "roblox": ("sober", "roblox"),
    "resolve": ("resolve", "davinci"),
}
for _word, _apps in window_ctl.ALIASES.items():
    if _word != "claude":
        GENERIC_APPS[_word] = tuple(dict.fromkeys(GENERIC_APPS.get(_word, ()) + _apps))

_FILLER_LEAD = re.compile(r"^(?:the|my|a|an|that|this)\s+")
_FILLER_TAIL = re.compile(r"\s+(?:window|app|application|tab|please)$")

MAIN_WORDS = {"main", "primary", "main monitor", "primary monitor", "m1", "monitor 1"}
LAPTOP_WORDS = {
    "laptop",
    "laptop screen",
    "built-in",
    "built in",
    "internal",
    "m2",
    "monitor 2",
}
SECOND_WORDS = {
    "second",
    "secondary",
    "second external",
    "second monitor",
    "secondary monitor",
    "second screen",
    "2nd",
    "m3",
    "monitor 3",
}
OTHER_WORDS = {
    "other",
    "another",
    "other one",
    "the other",
    "other monitor",
    "other screen",
    "another monitor",
    "another screen",
}
OTHER = OTHER_WORDS  # kept for callers that import the old name


# --------------------------------------------------------------- text helpers


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _compact(text: str) -> str:
    return _norm(text).replace(" ", "")


def clean_query(text: str) -> str:
    q = " ".join((text or "").lower().split())
    while True:
        new = _FILLER_LEAD.sub("", q)
        new = _FILLER_TAIL.sub("", new)
        if new == q:
            return q
        q = new


def query_candidates(query: str) -> list[str]:
    """Probe strings, best first: the words as said, then their app aliases."""
    q = clean_query(query)
    if not q:
        return []
    out = [q]
    seen = {_norm(q)}
    # "kon-sole" also probes as "kon sole" and "konsole", the forms KRunner indexes.
    for form in (_norm(q), _compact(q)):
        if form and form not in seen:
            seen.add(form)
            out.append(form)
    for alias in GENERIC_APPS.get(_norm(q), ()) or GENERIC_APPS.get(q, ()):
        if _norm(alias) not in seen:
            seen.add(_norm(alias))
            out.append(alias)
    return out


def _match_tier(candidate: str, info: dict) -> int | None:
    """0 = app identity, 1 = phrase in title/class, 2 = loose substring."""
    cand = _norm(candidate)
    if not cand:
        return None
    cc = cand.replace(" ", "")
    identities: set[str] = set()
    for key in ("resourceName", "resourceClass", "desktopFile"):
        raw = str(info.get(key) or "")
        if raw:
            identities.add(_compact(raw))
            identities.add(_compact(raw.split(".")[-1]))
    if cc in identities:
        return 0
    fields = [
        str(info.get(k) or "")
        for k in ("caption", "resourceClass", "resourceName", "desktopFile")
    ]
    for field in fields:
        if f" {cand} " in f" {_norm(field)} ":
            return 1
    if len(cc) >= 4 and cc in "".join(_compact(f) for f in fields):
        return 2
    return None


def _label(info: dict | None, fallback: str) -> str:
    if not info:
        return fallback
    caption = str(info.get("caption") or "").strip() or fallback
    app = str(info.get("resourceName") or info.get("resourceClass") or "").strip()
    return (
        f"'{caption}' ({app})"
        if app and app.lower() not in caption.lower()
        else f"'{caption}'"
    )


# --------------------------------------------------------------- monitors


def _priorities(text: str) -> dict[str, int]:
    """Output name -> KDE priority (1 = primary). Pure."""
    ansi = re.compile(r"\x1b\[[0-9;]*m")
    prios: dict[str, int] = {}
    name = None
    for raw in (text or "").splitlines():
        line = ansi.sub("", raw)
        m = re.search(r"Output:\s*\d*\s*(\S+)", line)
        if m:
            name = m.group(1)
        p = re.search(r"priority\s+(\d+)", line)
        if p and name:
            prios[name] = int(p.group(1))
    return prios


def monitors() -> list[dict]:
    result = subprocess.run(
        ["kscreen-doctor", "-o"], capture_output=True, text=True, timeout=7
    )
    if result.returncode:
        return []
    screens = parse_kscreen_outputs(result.stdout)
    prios = _priorities(result.stdout)
    for s in screens:
        s["priority"] = prios.get(s["name"])
    if screens and all(s["priority"] is not None for s in screens):
        screens.sort(key=lambda s: s["priority"])
    return screens


def _external(screen: dict) -> bool:
    return not screen["name"].lower().startswith(("edp", "lvds"))


def _primary(screens: list[dict]) -> dict:
    with_prio = [s for s in screens if s.get("priority") is not None]
    if with_prio:
        return min(with_prio, key=lambda s: s["priority"])
    return screens[0]  # kscreen lists the primary first when priorities are absent


def _describe(screens: list[dict]) -> str:
    if not screens:
        return "No active monitors were detected."
    primary = _primary(screens)
    parts = []
    for s in sorted(screens, key=lambda s: s["x"]):
        role = (
            "main" if s is primary else ("laptop" if not _external(s) else "external")
        )
        parts.append(f"{s['name']} ({role}, {s['w']}x{s['h']} at {s['x']},{s['y']})")
    return "Monitors found: " + "; ".join(parts) + "."


def _spec_text(spec: str) -> str:
    text = " ".join((spec or "other").lower().replace("_", " ").split())
    text = re.sub(r"^(?:the|my)\s+", "", text)
    text = re.sub(r"\s+(?:monitor|screen|display)$", "", text)
    return text or "other"


def pick_destination(spec: str, screens: list[dict], current: str | None) -> dict:
    if not screens:
        raise ValueError("No active monitors were detected.")
    text = _spec_text(spec)
    key = re.sub(r"[^a-z0-9]", "", text)
    primary = _primary(screens)
    laptop = next((s for s in screens if not _external(s)), None)
    externals = sorted((s for s in screens if _external(s)), key=lambda s: s["x"])
    secondary = next((s for s in externals if s["name"] != primary["name"]), None)

    for s in screens:  # exact output name: DP-1, dp1, HDMI-A-2, hdmia2
        if key and key == re.sub(r"[^a-z0-9]", "", s["name"].lower()):
            return s
    if text in MAIN_WORDS:
        return primary
    if text in LAPTOP_WORDS:
        if laptop is None:
            raise ValueError("No laptop panel is connected. " + _describe(screens))
        return laptop
    if text in SECOND_WORDS:
        if secondary is None:
            raise ValueError("There is no second monitor. " + _describe(screens))
        return secondary
    if text in ("right", "rightmost", "right screen"):
        return max(screens, key=lambda s: s["x"])
    if text in ("left", "leftmost", "left screen"):
        return min(screens, key=lambda s: s["x"])
    if text in OTHER_WORDS:
        # On a laptop with two external screens, "other monitor" means the
        # other external display, not the laptop panel between them.
        alternatives = [s for s in externals if s["name"] != current]
        if alternatives and current is not None:
            return alternatives[0]
        return next((s for s in screens if s["name"] != current), primary)
    if text in ("external", "external monitor", "external screen"):
        if not externals:
            raise ValueError("No external monitor is connected. " + _describe(screens))
        if len(externals) > 1:
            names = ", ".join(s["name"] for s in externals)
            raise ValueError(
                f"More than one external monitor ({names}); say which one. "
                + _describe(screens)
            )
        return externals[0]
    raise ValueError(
        f"Unknown monitor '{spec}'. {_describe(screens)} "
        "Use main, laptop, second, left, right, other, or an output name like DP-1."
    )


# --------------------------------------------------------------- windows


def _info(uuid: str) -> dict | None:
    """KWin's getWindowInfo for one window, flattened, or None."""
    try:
        proc = subprocess.run(
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
                uuid,
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode:
            return None
        values = json.loads(proc.stdout)["data"][0]
        info = {k: v["data"] for k, v in values.items()}
        if "x" not in info or "width" not in info:
            return None  # unknown uuid: KWin answers with an empty record
        info["uuid"] = uuid
        return info
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        subprocess.TimeoutExpired,
    ):
        return None


def _probe(text: str) -> list[tuple[str, str]]:
    try:
        return kwin_windows.find(text)
    except Exception:
        return []


def _is_normal(info: dict) -> bool:
    try:
        return int(info.get("type", 0)) == 0
    except (TypeError, ValueError):
        return True


def _active_pid() -> int | None:
    try:
        import active_window

        active = active_window.kdotool_active() or {}
        return int(active.get("pid") or 0) or None
    except Exception:
        return None


def _ranked_windows(query: str) -> list[dict]:
    """Every open window matching query, best first. Read-only."""
    cands = query_candidates(query)
    if not cands:
        return []
    infos: dict[str, dict] = {}
    best: dict[str, tuple[int, int]] = {}
    family = _norm(clean_query(query))
    is_family = bool(GENERIC_APPS.get(family))
    for idx, cand in enumerate(cands):
        for uuid, _title in _probe(cand):
            if uuid not in infos:
                info = _info(uuid)
                if info is None:
                    continue
                infos[uuid] = info
            info = infos[uuid]
            if not _is_normal(info):
                continue
            tier = _match_tier(cand, info)
            if tier is None:
                continue
            # "terminal" is a family word: a browser tab titled "...terminal..."
            # must not count as the terminal. Only app identities do.
            if tier == 1 and is_family and _norm(cand) == family:
                continue
            key = (tier, idx)
            if uuid not in best or key < best[uuid]:
                best[uuid] = key
    active = _active_pid()
    ordered = sorted(
        best,
        key=lambda u: (
            best[u][0],
            not (active and infos[u].get("pid") == active),
            best[u][1],
        ),
    )
    out = []
    for uuid in ordered:
        row = dict(infos[uuid])
        row["uuid"] = uuid
        row["_active"] = bool(active and row.get("pid") == active)
        out.append(row)
    return out


def kwin_info(query: str) -> dict | None:
    """Compositor's logical geometry for the best window matching query.

    The result carries "_others": the other matching windows, so a move can
    say what it did not touch.
    """
    ranked = _ranked_windows(query)
    if not ranked:
        return None
    best = dict(ranked[0])
    best["_others"] = [
        {
            "uuid": r["uuid"],
            "caption": r.get("caption", ""),
            "app": r.get("resourceName") or r.get("resourceClass", ""),
        }
        for r in ranked[1:6]
    ]
    return best


def _verify_info(query: str, uuid: str | None) -> dict | None:
    """Re-read the exact window that was moved; fall back to the query."""
    if uuid:
        info = _info(uuid)
        if info is not None:
            return info
    return kwin_info(query)


def _open_window_names(limit: int = 12) -> list[str]:
    """Names of open windows we can reach, probed with app words."""
    probes: list[str] = []
    for apps in GENERIC_APPS.values():
        for app in apps:
            if app not in probes:
                probes.append(app)
    names: list[str] = []
    seen: set[str] = set()
    for probe in probes:
        for uuid, _title in _probe(probe):
            if uuid in seen:
                continue
            seen.add(uuid)
            info = _info(uuid)
            if info is None or not _is_normal(info):
                continue
            caption = str(info.get("caption") or "").strip()
            app = str(info.get("resourceName") or info.get("resourceClass") or "")
            if caption:
                names.append(f"{caption} ({app})" if app else caption)
            if len(names) >= limit:
                return names
    return names


def no_match_message(query: str) -> str:
    names = _open_window_names()
    if not names:
        return (
            f"No open window matches '{query}', and KWin listed no windows I "
            "could probe. Name the app, for example 'konsole' or 'brave'."
        )
    return (
        f"No open window matches '{query}'. Open windows I can see: "
        + "; ".join(names)
        + ". Retry with the app name or part of one of those titles."
    )


def window_geometry(query: str) -> tuple[int, int, int, int] | None:
    """XWayland/X11 geometry; native Wayland windows aren't exposed here."""
    try:
        proc = subprocess.run(
            ["wmctrl", "-lGx"], capture_output=True, text=True, timeout=5
        )
        if proc.returncode:
            return None
        q = query.lower().strip()
        for line in proc.stdout.splitlines():
            parts = line.split(None, 8)
            if len(parts) >= 9 and q in (parts[6] + " " + parts[8]).lower():
                return tuple(int(v) for v in parts[2:6])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def _screen_for(
    geometry: tuple[int, int, int, int] | None, screens: list[dict]
) -> str | None:
    if geometry is None:
        return None
    x, y, w, h = geometry
    cx, cy = x + w // 2, y + h // 2
    for s in screens:
        if s["x"] <= cx < s["x"] + s["w"] and s["y"] <= cy < s["y"] + s["h"]:
            return s["name"]
    return None


def window_exists(query: str) -> bool:
    try:
        if kwin_info(query) is not None:
            return True
    except Exception:
        pass
    return window_geometry(query) is not None


def info_screen(info: dict | None, screens: list[dict]) -> str | None:
    if not info:
        return None
    try:
        g = (int(info["x"]), int(info["y"]), int(info["width"]), int(info["height"]))
        return _screen_for(g, screens)
    except (KeyError, TypeError, ValueError):
        return None


def hud_screen(screens: list[dict]) -> str | None:
    """Discover the actual screen holding the Jarvis UI, not assumed primary."""
    info = kwin_info("Jarvis")
    if info and str(info.get("resourceClass", "")).lower() == "jarvis-shell":
        return info_screen(info, screens)
    # The shell defaults to primary; reserving it is safer if lookup fails.
    return _primary(screens)["name"] if screens else None


def select_destination(
    spec: str, screens: list[dict], current: str | None, protected: str | None
) -> dict:
    """Automatic destinations exclude both Jarvis's display and the source."""
    if _spec_text(spec) not in OTHER_WORDS:
        return pick_destination(spec, screens, current)
    eligible = [s for s in screens if s["name"] != current and s["name"] != protected]
    externals = [s for s in eligible if _external(s)]
    if not eligible:
        raise ValueError(
            "No other monitor is available without covering Jarvis. "
            "Specify a monitor explicitly to override."
        )
    return (externals or eligible)[0]


def target_rect(info: dict | None, dest: dict) -> dict:
    # Matches the KWin mover's target dimensions. Clamped to the live output.
    original_w = int(info.get("width", 1000)) if info else 1000
    original_h = int(info.get("height", 700)) if info else 700
    is_max = bool(info and int(info.get("maximizeHorizontal", 0)) > 0)
    width = min(dest["w"] if is_max else dest["w"] - 80, max(600, original_w))
    height = min(dest["h"] - 40 if is_max else dest["h"] - 90, max(400, original_h))
    return {
        "x": round(dest["x"] + (dest["w"] - width) / 2),
        "y": round(dest["y"] + (dest["h"] - height) / 2),
        "w": width,
        "h": height,
    }


def move_script(
    query: str,
    destination: str,
    screens: list[dict],
    automatic: bool = False,
    uuid: str | None = None,
) -> str:
    """Move the real window while the compositor holds it hidden.

    With a uuid the exact window is targeted (no ambiguous name match);
    without one, the query text is matched against caption and class.
    """
    target = next(s for s in screens if s["name"] == destination)
    name = json.dumps(query.strip().lower())
    pos = json.dumps(target)
    if uuid:
        want = json.dumps(uuid.strip().lower())
        select = f"""
var wantId = {want};
var matches = workspace.windowList().filter(function(w) {{
    try {{return w.normalWindow && String(w.internalId).toLowerCase()===wantId;}}
    catch(ignore) {{return String(w.internalId).toLowerCase()===wantId;}}
}});
"""
    else:
        select = f"""
var needle = {name};
var matches = workspace.windowList().filter(function(w) {{
    var label=(String(w.caption||"")+" "+String(w.resourceClass||"")).toLowerCase();
    try {{return w.normalWindow && label.indexOf(needle)>=0;}}
    catch(ignore) {{return label.indexOf(needle)>=0;}}
}});
matches.sort(function(a,b) {{
  return Number(String(b.caption||"").toLowerCase()===needle) -
         Number(String(a.caption||"").toLowerCase()===needle);
}});
"""
    return f"""
var dest = {pos};
{select}
if(matches.length) {{
  var w=matches[0], before=w.frameGeometry;
  var a=workspace.clientArea(KWin.MaximizeArea,w);
  var wasMax=Math.abs(before.width-a.width)<12 &&
             Math.abs(before.height-a.height)<12;
  try {{w.setMaximize(false,false);}} catch(ignore) {{}}
  var screens=workspace.screens||[];
  var output=screens.find(function(v){{return v.name===dest.name;}});
  if(output && w.output!==output) {{
    try {{workspace.sendClientToScreen(w,output);}} catch(ignore) {{}}
  }}
  var width=Math.min(Math.max(600,before.width),dest.w-80);
  var height=Math.min(Math.max(400,before.height),dest.h-90);
  w.frameGeometry={{x:Math.round(dest.x+(dest.w-width)/2),
                   y:Math.round(dest.y+(dest.h-height)/2),
                   width:Math.round(width),height:Math.round(height)}};
  if(wasMax) {{try{{w.setMaximize(true,true);}}catch(ignore){{}}}}
  workspace.activeWindow=w;
}}
"""


def run_move_script(js: str) -> bool:
    return window_layout.run_script(js, name="jarvis-move-monitor")


def portal_available() -> bool:
    """Only stage the delay if the compositor effect is actually installed."""
    try:
        proc = subprocess.run(
            [
                "qdbus",
                "org.kde.KWin",
                "/Effects",
                "org.kde.kwin.Effects.isEffectLoaded",
                "jarvis-energy",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return proc.returncode == 0 and proc.stdout.strip() == "true"
    except (OSError, subprocess.TimeoutExpired):
        return False


def _others_note(info: dict | None) -> str:
    others = (info or {}).get("_others") or []
    if not others:
        return ""
    names = "; ".join(
        f"'{o.get('caption', '')}' ({o.get('app', '')})" for o in others[:4]
    )
    return (
        f" Also matched, not moved: {names}. Say the exact title to move one of those."
    )


def move_window(query: str, monitor: str = "other") -> tuple[bool, str]:
    query = (query or "").strip()
    if not query:
        return False, "Which window should I move?"
    animated = False
    info = None
    try:
        screens = monitors()
        if not screens:
            return False, "No active screens were detected."
        info = kwin_info(query)
        if not info and window_geometry(query) is None:
            return False, no_match_message(query)
        current = info_screen(info, screens)
        if current is None:
            current = _screen_for(window_geometry(query), screens)
        dest = select_destination(monitor, screens, current, hud_screen(screens))
        target = dest["name"]
        label = _label(info, query)
        uuid = str(info["uuid"]) if info and info.get("uuid") else None
        if current == target:
            return True, f"{label} is already on {target}."
        # Hide the real window, commit and verify its geometry, then animate
        # from that final geometry. No wall-clock race at the shader endpoint.
        if info and uuid and portal_available():
            from system import cinematic

            animated = cinematic.mark_window("portal", uuid, to=target_rect(info, dest))
        if not run_move_script(move_script(query, target, screens, uuid=uuid)):
            if animated:
                cinematic.mark_window("portal-cancel", uuid)
            return False, "KWin did not accept the window-moving script."
        # Verify in the compositor's own coordinate space, not wmctrl's
        # XWayland coordinates, which can report a wrong display.
        for _ in range(12):
            after = _verify_info(query, uuid)
            if info_screen(after, screens) == target:
                if animated:
                    animated = cinematic.mark_window("portal-commit", uuid)
                    if not animated:
                        cinematic.mark_window("portal-cancel", uuid)
                return True, f"Moved {label} to {target}" + (
                    " with Particle Portal." if animated else "."
                ) + _others_note(info)
            time.sleep(0.10)
        if animated:
            cinematic.mark_window("portal-cancel", uuid)
        return False, f"Could not verify {label} on {target}; no success was assumed."
    except (OSError, subprocess.TimeoutExpired, ValueError, StopIteration) as exc:
        if animated and info and info.get("uuid"):
            from system import cinematic

            cinematic.mark_window("portal-cancel", str(info["uuid"]))
        return False, str(exc)
