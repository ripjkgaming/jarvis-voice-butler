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

#: KWin scripting plugin name for our watcher.
SCRIPT_NAME = "jarvis-focus-watcher"

#: The watcher script. Pushes the active window on load and on every
#: activation; arg order is (caption, resourceClass, pid, internalId).
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
try {
    workspace.windowActivated.connect(jarvisFocusPush);
} catch (e) {}
try {
    jarvisFocusPush(workspace.activeWindow);
} catch (e) {}
""".replace("BUSNAME", BUS_NAME)
    .replace("BUSPATH", BUS_PATH)
    .replace("BUSIFACE", BUS_IFACE)
)

_cache: dict = {"at": 0.0, "value": None}
_push: dict = {"at": 0.0, "value": None}
_push_lock = threading.Lock()
_listener_started = False


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


def _listener_loop(stop: threading.Event) -> None:
    # jeepney is imported here so importing this module never requires it.
    try:
        from jeepney import DBusAddress, MessageType, new_method_call, new_method_return
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
                body = list(msg.body or [])
                caption = str(body[0]) if len(body) > 0 else ""
                app = str(body[1]) if len(body) > 1 else ""
                try:
                    pid = int(body[2]) if len(body) > 2 else 0
                except (TypeError, ValueError):
                    pid = 0
                wid = str(body[3]) if len(body) > 3 else ""
                _note_push(caption, app, pid, wid)
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
