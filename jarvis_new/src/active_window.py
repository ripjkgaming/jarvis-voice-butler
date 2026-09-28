"""What window is active right now on KDE Plasma 6 Wayland.

wmctrl/xdotool only see X11 windows, so on Plasma Wayland this module
tries, in order:

1. ``kdotool`` (packaged for Fedora/Nobara, xdotool-like for KWin) when
   installed: ``getactivewindow`` + ``getwindowpid/classname/name``.
2. A tiny KWin script (``workspace.windowActivated`` + the current
   ``workspace.activeWindow``) that pushes caption/resourceClass/pid/
   internalId over D-Bus to a listener thread in this process. Verified
   live on Plasma 6.7: ``internalId`` matches the WindowsRunner window
   UUID, so it doubles as a stable window id. Needs the ``jeepney``
   package (pure Python) for the listener.
3. KWin's ``supportInformation``: parsed only to confirm it carries no
   active-window data on this machine (418 lines, no client list), so
   this tier always yields None and is documented, not relied on.

``active()`` returns ``{"title", "app", "pid", "id"}`` or None. The
polled tier (kdotool) is cached for 0.5 s; the push tier is
event-driven, so its cache is always current. Everything is fail-soft:
every public function swallows its errors and returns None/False.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

#: Freshness for the polled (kdotool) tier.
CACHE_TTL_S = 0.5

#: D-Bus endpoint the KWin script pushes to.
BUS_NAME = "org.jarvis.Focus"
BUS_PATH = "/org/jarvis/Focus"
BUS_IFACE = "org.jarvis.Focus"

#: What KWin's Meta-alone shortcut calls in school mode (see
#: school.META_JARVIS_VALUE): no args, toggles the Jarvis start menu.
START_MENU_MEMBER = "StartMenu"

#: KWin scripting plugin name for our watcher.
SCRIPT_NAME = "jarvis-focus-watcher"

#: The watcher script. Pushes the active window on load and on every
#: activation; arg order is (caption, resourceClass, pid, internalId).
#: It also pushes the full taskbar window list as one JSON string via a
#: second D-Bus method ("Windows") on load, on windowAdded/windowRemoved/
#: windowActivated, and when any tracked window's caption/minimized state
#: changes. Per window: id/title/app/desktop/active/minimized/pid.
#: A third member ("StartMenu", no args) is KWin's Meta-alone shortcut in
#: school mode (see school.META_JARVIS_VALUE) and toggles the Jarvis
#: start menu via toggle_start_menu().
KWIN_SCRIPT = (
    """\
function jarvisFocusPush(w) {
    if (!w) {
        callDBus("BUSNAME", "BUSPATH", "BUSIFACE",
                 "Active", "", "", 0, "");
        return;
    }
    var caption = "", cls = "", pid = 0, idstr = "";
    try { caption = String(w.caption || ""); } catch (e) {}
    try { cls = String(w.resourceClass || ""); } catch (e) {}
    try { pid = Math.floor(Number(w.pid || 0)); } catch (e) {}
    try { idstr = String(w.internalId || ""); } catch (e) {}
    callDBus("BUSNAME", "BUSPATH", "BUSIFACE",
             "Active", caption, cls, pid, idstr);
}
function jarvisIsTaskbar(w) {
    try { if (!w.normalWindow) return false; } catch (e) { return false; }
    try { if (w.skipTaskbar) return false; } catch (e) {}
    try {
        var c = String(w.resourceClass || "").toLowerCase();
        if (c.indexOf("jarvis") !== -1) return false;
    } catch (e) {}
    return true;
}
function jarvisWindowsPush() {
    var out = [];
    var active = null;
    try { active = workspace.activeWindow; } catch (e) {}
    var list = [];
    try { list = workspace.windowList(); } catch (e) { list = []; }
    for (var i = 0; i < list.length; i++) {
        var w = list[i];
        try {
            if (!jarvisIsTaskbar(w)) continue;
            var caption = "", cls = "", desk = "", idstr = "";
            var pid = 0, mini = false;
            try { caption = String(w.caption || ""); } catch (e) {}
            try { cls = String(w.resourceClass || ""); } catch (e) {}
            try { desk = String(w.desktopFileName || ""); } catch (e) {}
            try { pid = Math.floor(Number(w.pid || 0)); } catch (e) {}
            try { mini = !!w.minimized; } catch (e) {}
            try { idstr = String(w.internalId || ""); } catch (e) {}
            out.push({id: idstr, title: caption, app: cls, desktop: desk,
                      active: (w === active), minimized: mini, pid: pid});
        } catch (e) {}
    }
    try {
        callDBus("BUSNAME", "BUSPATH", "BUSIFACE",
                 "Windows", JSON.stringify(out));
    } catch (e) {}
}
function jarvisHookWindow(w) {
    if (!w) return;
    try { w.captionChanged.connect(function() { jarvisWindowsPush(); }); } catch (e) {}
    try { w.minimizedChanged.connect(function() { jarvisWindowsPush(); }); } catch (e) {}
}
try {
    workspace.windowActivated.connect(jarvisFocusPush);
} catch (e) {}
try {
    workspace.windowActivated.connect(jarvisWindowsPush);
} catch (e) {}
try {
    workspace.windowAdded.connect(function(w) { jarvisHookWindow(w); jarvisWindowsPush(); });
} catch (e) {}
try {
    workspace.windowRemoved.connect(function(w) { jarvisWindowsPush(); });
} catch (e) {}
try {
    var _wl = workspace.windowList();
    for (var _i = 0; _i < _wl.length; _i++) { jarvisHookWindow(_wl[_i]); }
} catch (e) {}
try {
    jarvisFocusPush(workspace.activeWindow);
} catch (e) {}
try {
    jarvisWindowsPush();
} catch (e) {}
""".replace("BUSNAME", BUS_NAME)
    .replace("BUSPATH", BUS_PATH)
    .replace("BUSIFACE", BUS_IFACE)
)

_cache: dict = {"at": 0.0, "value": None}
_push: dict = {"at": 0.0, "value": None}
_push_lock = threading.Lock()
_listener_started = False

#: Last full window list pushed by the KWin watcher, in first-seen order.
_windows: list[dict] = []
_windows_lock = threading.Lock()
_windows_order: dict[str, int] = {}
_windows_seq = 0


def jarvis_home() -> Path:
    """Base dir honoring $JARVIS_HOME (default ~/.jarvis). Pure (env)."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def script_path() -> Path:
    """Where the watcher script file lives (written on ensure). Pure."""
    return jarvis_home() / "focus" / "kwin_active_window.js"


def kdotool_path() -> str | None:
    """kdotool binary, or None when not installed. Never raises."""
    try:
        return shutil.which("kdotool")
    except Exception:
        return None


def parse_kdotool_id(out: str) -> str:
    """``kdotool getactivewindow`` stdout -> window id ('' when none). Pure."""
    return " ".join((out or "").split())


def clean(info: dict | None) -> dict | None:
    """Normalize a raw window dict; None when it has no title/app. Pure."""
    if not isinstance(info, dict):
        return None
    title = " ".join(str(info.get("title") or "").split())[:300]
    app = " ".join(str(info.get("app") or "").split())[:120]
    if not title and not app:
        return None
    try:
        pid = int(info.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    wid = str(info.get("id") or "")[:120]
    return {"title": title, "app": app, "pid": pid, "id": wid}


def _run(argv: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def kdotool_active(binpath: str | None = None) -> dict | None:
    """One kdotool poll. None on any failure. Never raises."""
    try:
        exe = binpath or kdotool_path()
        if not exe:
            return None
        wid = parse_kdotool_id(_run([exe, "getactivewindow"]).stdout)
        if not wid:
            return None
        pid_out = _run([exe, "getwindowpid", wid]).stdout.strip()
        cls_out = _run([exe, "getwindowclassname", wid]).stdout.strip()
        name_out = _run([exe, "getwindowname", wid]).stdout.strip()
        return clean({"title": name_out, "app": cls_out, "pid": pid_out, "id": wid})
    except Exception:
        return None


def _qdbus(argv: list[str], timeout: float = 8.0) -> str:
    try:
        proc = _run(["qdbus", *argv], timeout=timeout)
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


def script_loaded(run=None) -> bool:
    """Is our KWin watcher script currently loaded? Never raises."""
    try:
        if run is not None:
            proc = run(
                [
                    "qdbus",
                    "org.kde.KWin",
                    "/Scripting",
                    "org.kde.kwin.Scripting.isScriptLoaded",
                    SCRIPT_NAME,
                ],
                capture_output=True,
                text=True,
                timeout=8,
            )
            return proc.stdout.strip() == "true"
        return (
            _qdbus(
                [
                    "org.kde.KWin",
                    "/Scripting",
                    "org.kde.kwin.Scripting.isScriptLoaded",
                    SCRIPT_NAME,
                ]
            )
            == "true"
        )
    except Exception:
        return False


def ensure_script() -> bool:
    """Write + load + start the KWin watcher. True when loaded. Fail-soft."""
    try:
        path = script_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text() != KWIN_SCRIPT:
            path.write_text(KWIN_SCRIPT)
        if not script_loaded():
            _qdbus(
                [
                    "org.kde.KWin",
                    "/Scripting",
                    "org.kde.kwin.Scripting.loadScript",
                    str(path),
                    SCRIPT_NAME,
                ]
            )
            _qdbus(["org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.start"])
        return script_loaded()
    except Exception:
        return False


def unload_script() -> bool:
    """Remove the KWin watcher. True when gone (or never there). Fail-soft."""
    try:
        _qdbus(
            [
                "org.kde.KWin",
                "/Scripting",
                "org.kde.kwin.Scripting.unloadScript",
                SCRIPT_NAME,
            ]
        )
        return not script_loaded()
    except Exception:
        return False


def _note_push(caption: str, app: str, pid: int, wid: str) -> None:
    with _push_lock:
        _push["at"] = time.time()
        _push["value"] = clean({"title": caption, "app": app, "pid": pid, "id": wid})


def parse_windows_payload(raw: str | list) -> list[dict]:
    """One "Windows" push (JSON string) -> normalized window dicts. Pure.

    Drops entries without an id and our own (resourceClass with
    "jarvis"); never raises (garbage yields []).
    """
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            wid = str(item.get("id") or "")[:120]
            if not wid:
                continue
            app = " ".join(str(item.get("app") or "").split())[:120]
            if "jarvis" in app.lower():
                continue
            title = " ".join(str(item.get("title") or "").split())[:300]
            desktop = " ".join(str(item.get("desktop") or "").split())[:200]
            try:
                pid = int(item.get("pid") or 0)
            except (TypeError, ValueError):
                pid = 0
            out.append(
                {
                    "id": wid,
                    "title": title,
                    "app": app,
                    "desktop": desktop,
                    "active": bool(item.get("active")),
                    "minimized": bool(item.get("minimized")),
                    "pid": pid,
                }
            )
        except Exception:
            continue
    return out


def _note_windows(items: list[dict]) -> None:
    """Store a pushed window list in stable first-seen order. Never raises."""
    global _windows, _windows_seq
    try:
        with _windows_lock:
            for item in items:
                wid = item.get("id", "")
                if wid and wid not in _windows_order:
                    _windows_order[wid] = _windows_seq
                    _windows_seq += 1
            live = {item.get("id", "") for item in items}
            for gone in [key for key in _windows_order if key not in live]:
                del _windows_order[gone]
            _windows = sorted(items, key=lambda w: _windows_order.get(w.get("id", ""), 0))
    except Exception:
        pass


def windows() -> list[dict]:
    """Last window list pushed by the KWin watcher (copy, [] if none).

    Stable first-seen order; never raises.
    """
    try:
        with _windows_lock:
            return [dict(item) for item in _windows]
    except Exception:
        return []


def _shell_bins() -> list[str]:
    """Jarvis shell binaries, same search order as projects.shell_verb."""
    repo = Path(__file__).resolve().parent.parent
    return [
        str(repo / "shell" / "src-tauri" / "target" / "debug" / "jarvis-shell"),
        "jarvis-shell",
        "jarvis",
    ]


def toggle_start_menu(run=None, bins: list[str] | None = None) -> bool:
    """Toggle the Jarvis start menu via the shell binary. Fail-soft.

    Runs ``<shell> schoolmenu launcher`` (which clicks the taskbar start
    button). Same binary search order as ``projects.shell_verb``; ``run``
    is subprocess.run-shaped, ``bins`` injects candidates (tests).
    """
    runner = run or subprocess.run
    for argv0 in bins if bins is not None else _shell_bins():
        try:
            if not (Path(argv0).exists() or shutil.which(argv0)):
                continue
        except Exception:
            continue
        try:
            runner([argv0, "schoolmenu", "launcher"], capture_output=True, timeout=10)
            return True
        except Exception:
            continue
    return False


#: The shell's school-transition measure script (school.rs measure_script)
#: pushes one JSON geometry report here; the page reads it at /school/geom.
SCHOOL_GEOM_MEMBER = "SchoolGeom"
_SCHOOL_GEOM: dict = {"geom": None, "at": 0.0}


def note_school_geom(raw: str) -> None:
    """Keep the latest geometry report (bad JSON is dropped). Never raises."""
    try:
        geom = json.loads(raw)
    except (TypeError, ValueError):
        return
    if isinstance(geom, dict):
        _SCHOOL_GEOM["geom"] = geom
        _SCHOOL_GEOM["at"] = time.time()


def school_geom(max_age_s: float = 30.0) -> dict | None:
    """Latest geometry report, or None when none arrived recently."""
    if time.time() - float(_SCHOOL_GEOM["at"]) > max_age_s:
        return None
    return _SCHOOL_GEOM["geom"]


def handle_dbus_message(member: str | None, body: list | tuple) -> str:
    """Route one D-Bus push to the Active, Windows or StartMenu handler.

    Returns "active", "windows" or "startmenu" (never raises); the
    listener replies to the caller afterwards. A missing/unknown member
    keeps the old behavior and lands in the Active store.
    """
    try:
        name = str(member or "")
    except Exception:
        name = ""
    try:
        args = list(body or [])
    except Exception:
        args = []
    try:
        if name == "Windows":
            raw = str(args[0]) if args else ""
            _note_windows(parse_windows_payload(raw))
            return "windows"
        if name == SCHOOL_GEOM_MEMBER:
            note_school_geom(str(args[0]) if args else "")
            return "schoolgeom"
        if name == START_MENU_MEMBER:
            # KWin calls this synchronously on every Meta press: never
            # let a shell failure propagate (the reply must go out).
            with contextlib.suppress(Exception):
                toggle_start_menu()
            return "startmenu"
        caption = str(args[0]) if len(args) > 0 else ""
        app = str(args[1]) if len(args) > 1 else ""
        try:
            pid = int(args[2]) if len(args) > 2 else 0
        except (TypeError, ValueError):
            pid = 0
        wid = str(args[3]) if len(args) > 3 else ""
        _note_push(caption, app, pid, wid)
        return "active"
    except Exception:
        return name.lower() if name else "active"


def _listener_loop(stop: threading.Event) -> None:
    # jeepney is imported here so importing this module never requires it.
    try:
        from jeepney import (
            DBusAddress,
            HeaderFields,
            MessageType,
            new_method_call,
            new_method_return,
        )
        from jeepney.io.blocking import open_dbus_connection
    except Exception:
        return
    try:
        conn = open_dbus_connection(bus="SESSION")
        bus = DBusAddress(
            "/org/freedesktop/DBus", "org.freedesktop.DBus", "org.freedesktop.DBus"
        )
        conn.send_and_get_reply(
            new_method_call(bus, "RequestName", "su", (BUS_NAME, 0))
        )
        conn.send(
            new_method_call(
                bus,
                "AddMatch",
                "s",
                (f"type='method_call',interface='{BUS_IFACE}',path='{BUS_PATH}'",),
            )
        )
        while not stop.is_set():
            try:
                msg = conn.receive(timeout=1.0)
            except TimeoutError:
                continue
            except Exception:
                continue
            try:
                if msg.header.message_type != MessageType.method_call:
                    continue
                member = None
                try:
                    member = msg.header.fields.get(HeaderFields.member)
                except Exception:
                    member = None
                handle_dbus_message(member, list(msg.body or []))
                conn.send(new_method_return(msg))
            except Exception:
                continue
    except Exception:
        pass


def ensure_listener() -> bool:
    """Start the D-Bus push listener thread (once). True when running."""
    global _listener_started
    try:
        if _listener_started:
            return True
        stop = threading.Event()
        thread = threading.Thread(
            target=_listener_loop, args=(stop,), name="focus-kwin-push", daemon=True
        )
        thread.start()
        _listener_started = True
        # A script left loaded by a previous process (bridge restart) never
        # re-pushes, so this process would see None until Sir switches
        # windows (the Brave orb never appeared). Reload it: its load-time
        # push reaches our listener once the bus name is ours.
        time.sleep(0.5)
        if script_loaded():
            unload_script()
        return ensure_script()
    except Exception:
        return False


def push_active() -> dict | None:
    """Last window pushed by the KWin watcher, or None. Never raises."""
    try:
        with _push_lock:
            value = _push["value"]
        return dict(value) if value else None
    except Exception:
        return None


def active() -> dict | None:
    """Active window now: push tier first, kdotool fallback (0.5 s cache).

    Ensures the watcher script + listener on a Plasma session; returns
    None when nothing answers. Never raises.
    """
    try:
        now = time.time()
        if _listener_started or ensure_listener():
            got = push_active()
            if got:
                return got
        # Polled fallback (also the whole story off-Plasma).
        if now - _cache["at"] <= CACHE_TTL_S:
            value = _cache["value"]
            return dict(value) if value else None
        got = kdotool_active()
        _cache.update(at=now, value=dict(got) if got else None)
        return got
    except Exception:
        return None
