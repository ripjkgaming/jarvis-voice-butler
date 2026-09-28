"""School-mode taskbar backend: windows, launchers, apps, quick settings, power.

All logic lives here; src/bridge.py only wires the HTTP routes. Every
helper is fail-soft (never raises), takes argv lists (never shell=True),
uses timeouts <= 5 s, and accepts injectable runners/paths so tests never
touch the live session:

- ``run``: called as ``run(argv, capture_output=True, text=True,
  timeout=5.0)``, returns an object with ``returncode``/``stdout``/
  ``stderr`` (i.e. ``subprocess.run``-shaped). Defaults to
  ``subprocess.run``.
- ``popen``: called as ``popen(argv, start_new_session=True, ...)`` for
  detached launches. Defaults to ``subprocess.Popen``.
- ``which``: defaults to ``shutil.which``.
- ``clock``: defaults to ``time.monotonic`` (caches).
- paths (appletsrc, desktop dirs, backlight root, KWin script file) are
  keyword-injectable.

Live command map (verified on this Plasma 6 machine where stated):

- window activate/minimize: ``dbus-send --session --print-reply
  --dest=org.kde.KWin /WindowsRunner org.kde.krunner1.Run
  string:<code>_<uuid> string:`` (code 0 activate, 2 minimize — same
  shape as src/system/kwin_windows.py). Verified live for Match; Run is
  the documented KRunner action form. Fallback: one-shot KWin script via
  ``qdbus org.kde.KWin /Scripting ...loadScript/start/unloadScript``
  (same mechanism as src/active_window.py ensure_script) that finds the
  window by internalId.
- launchers: pinned apps of the primary bottom panel's icontasks applet
  from ``~/.config/plasma-org.kde.plasma.desktop-appletsrc``.
- launch: ``kstart --application <id>`` (present on this machine),
  falling back to ``gtk-launch <id>`` then ``kioclient exec <path>``.
- quick wifi: ``nmcli radio wifi on|off`` / read ``nmcli radio wifi`` +
  active SSID from ``nmcli dev wifi``.
- quick bluetooth: ``bluetoothctl power on|off`` / read ``bluetoothctl
  show`` (``Powered: yes``).
- quick volume: ``wpctl set-volume -l 1.5 @DEFAULT_AUDIO_SINK@ <0..1.5>``
  (PipeWire overamplification to 150%; the -l flag raises wpctl's own
  cap) / ``wpctl set-mute @DEFAULT_AUDIO_SINK@ 1|0`` (pactl fallback
  ``pactl set-sink-volume @DEFAULT_SINK@ N%`` when wpctl is absent);
  read mirrors bridge._volume_status (wpctl, pactl fallback) and
  reports up to 150.
- quick brightness: read from ``/sys/class/backlight/*/`` (works here);
  set writes the sysfs ``brightness`` file (fails soft as non-root).
- quick dnd: read from ``org.freedesktop.Notifications`` ``Inhibited``
  property (verified live). There is NO reliable persistent setter from
  a one-shot bridge call: ``Inhibit``/``UnInhibit`` cookies are bound to
  the caller's D-Bus connection, so a one-shot ``dbus-send Inhibit``
  is revoked the moment dbus-send exits; and Plasma 6 honors no
  documented config key for this (current plasmanotifyrc carries no
  DoNotDisturb group). POST dnd therefore answers ``ok: false`` with an
  explanation instead of pretending.
- power: ``loginctl lock-session`` / ``systemctl suspend`` /
  ``dbus-send ... org.kde.Shutdown.logout`` (qdbus6 is not installed, so
  the dbus-send equivalent of ``qdbus6 org.kde.Shutdown /Shutdown
  logout``) / ``systemctl reboot`` / ``systemctl poweroff``. Strict
  allowlist; each request is logged via system.log_action.
"""

from __future__ import annotations

import contextlib
import re
import shutil
import subprocess
import time
from pathlib import Path

# Window id: KWin internalId UUID, with or without braces.
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_WINDOW_ID = re.compile(r"^\{?(" + _UUID + r")\}?$")
_WINDOW_ACTIONS = {"activate": "0", "minimize": "2"}

# Desktop id validation: bare filename only, no paths/commands.
_DESKTOP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@+-]*\.desktop$")

_LAUNCHERS_TTL_S = 30.0
_APPS_TTL_S = 60.0

_launchers_cache: dict = {}
_apps_cache: dict = {}


def _default_run(argv, **kw):
    return subprocess.run(argv, **kw)


def normalize_window_id(raw: object) -> str | None:
    """Bare UUID -> ``{uuid}``; braced UUID kept; anything else -> None. Pure."""
    if not isinstance(raw, str):
        return None
    match = _WINDOW_ID.match(raw.strip())
    if not match:
        return None
    return "{" + match.group(1) + "}"


def window_action(uuid: str, action: str, run=None) -> bool:
    """Activate/minimize a window via WindowsRunner Run. Fail-soft.

    Falls back to a one-shot KWin script when Run fails. Never raises.
    """
    runner = run or _default_run
    code = _WINDOW_ACTIONS.get(action)
    if code is None:
        return False
    try:
        proc = runner(
            [
                "dbus-send", "--session", "--print-reply",
                "--dest=org.kde.KWin", "/WindowsRunner",
                "org.kde.krunner1.Run",
                f"string:{code}_{uuid}", "string:",
            ],
            capture_output=True, text=True, timeout=5.0,
        )
        if proc.returncode == 0:
            return True
    except Exception:
        pass
    try:
        return kwin_script_action(uuid, action, run=runner)
    except Exception:
        return False


_KWIN_ACTION_NAME = "jarvis-window-action"


def kwin_script_path() -> Path:
    """Default file for the one-shot window-action script. Pure (env)."""
    home = Path.home() / ".jarvis"
    return home / "focus" / "kwin_window_action.js"


def kwin_script_source(uuid: str, action: str) -> str:
    """JS finding the window by internalId and acting on it. Pure."""
    if action == "activate":
        body = "workspace.activeWindow = w; try { w.minimized = false; } catch (e) {}"
    else:
        body = "try { w.minimized = true; } catch (e) {}"
    return (
        "var _wins = []; try { _wins = workspace.windowList(); } catch (e) {}\n"
        f"var _want = {uuid!r};\n"
        "for (var _i = 0; _i < _wins.length; _i++) {\n"
        "    try {\n"
        "        if (String(_wins[_i].internalId || '') === _want) {\n"
        "            var w = _wins[_i];\n"
        f"            {body}\n"
        "            break;\n"
        "        }\n"
        "    } catch (e) {}\n"
        "}\n"
    )


def kwin_script_action(uuid: str, action: str, run=None, path=None, sleep=None) -> bool:
    """One-shot KWin script fallback for activate/minimize. Fail-soft."""
    if action not in _WINDOW_ACTIONS:
        return False
    runner = run or _default_run
    nap = sleep or time.sleep
    try:
        dest = Path(path) if path is not None else kwin_script_path()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(kwin_script_source(uuid, action))
        base = ["qdbus", "org.kde.KWin", "/Scripting"]
        load = runner(
            [*base, "org.kde.kwin.Scripting.loadScript", str(dest), _KWIN_ACTION_NAME],
            capture_output=True, text=True, timeout=5.0,
        )
        if load.returncode != 0:
            return False
        start = runner(
            [*base, "org.kde.kwin.Scripting.start"],
            capture_output=True, text=True, timeout=5.0,
        )
        ok = start.returncode == 0
        with contextlib.suppress(Exception):
            nap(1.0)
        with contextlib.suppress(Exception):
            runner(
                [*base, "org.kde.kwin.Scripting.unloadScript", _KWIN_ACTION_NAME],
                capture_output=True, text=True, timeout=5.0,
            )
        return ok
    except Exception:
        return False


def handle_window(body: dict) -> tuple[int, dict]:
    """POST /window {"id", "action"}. Returns (http_code, payload). Pure-ish."""
    if not isinstance(body, dict):
        return 400, {"ok": False, "error": "invalid JSON body"}
    uuid = normalize_window_id(body.get("id"))
    if uuid is None:
        return 400, {"ok": False, "error": "id must be a window UUID"}
    if body.get("action") not in _WINDOW_ACTIONS:
        return 400, {"ok": False, "error": 'action must be "activate" or "minimize"'}
    return 200, {"ok": window_action(uuid, str(body["action"]))}


def applets_path() -> Path:
    """Plasma applets config. Pure (home dir)."""
    return Path.home() / ".config" / "plasma-org.kde.plasma.desktop-appletsrc"


def _section_parts(header: str) -> list[str]:
    return list(header.strip("[]").split("]["))


def parse_appletsrc(text: str) -> tuple[dict, dict, dict]:
    """Parse appletsrc into (containments, applets, configs). Pure.

    containments: {cid: {plugin, lastScreen, location}}; applets:
    {(cid, aid): plugin}; configs: {(cid, aid): {key: value}} for
    ``[Configuration][General]`` sections, in file order.
    """
    containments: dict[str, dict] = {}
    applets: dict[tuple[str, str], str] = {}
    configs: dict[tuple[str, str], dict] = {}
    current: list[str] = []
    try:
        lines = (text or "").splitlines()
    except Exception:
        return {}, {}, {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = _section_parts(line)
            continue
        if "=" not in line or len(current) < 2 or current[0] != "Containments":
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(current) == 2:
            containments.setdefault(current[1], {})[key] = value
        elif len(current) == 4 and current[2] == "Applets":
            if key == "plugin":
                applets[(current[1], current[3])] = value
        elif (
            len(current) == 6
            and current[2] == "Applets"
            and current[4] == "Configuration"
            and current[5] == "General"
        ):
            configs.setdefault((current[1], current[3]), {})[key] = value
    return containments, applets, configs


def select_launcher_entries(text: str) -> list[str]:
    """Raw launcher entries for the taskbar panel. Pure.

    The panel containment whose screen matches the primary bottom panel
    (lastScreen=0, location=4) wins; otherwise the first icontasks applet
    (file order) with a ``launchers=`` entry.
    """
    containments, applets, configs = parse_appletsrc(text)
    primary = ""
    for cid, cont in containments.items():
        try:
            screen = int(str(cont.get("lastScreen", "-1")).strip())
        except (TypeError, ValueError):
            screen = -1
        try:
            location = int(str(cont.get("location", "-1")).strip())
        except (TypeError, ValueError):
            location = -1
        if (
            cont.get("plugin", "") == "org.kde.panel"
            and location == 4
            and screen == 0
        ):
            primary = cid
            break
    ordered = list(configs)
    if primary:
        for key in ordered:
            if key[0] == primary and applets.get(key, "") == "org.kde.plasma.icontasks":
                raw = configs[key].get("launchers", "").strip()
                if raw:
                    return [e.strip() for e in raw.split(",") if e.strip()]
    for key in ordered:
        if applets.get(key, "") == "org.kde.plasma.icontasks":
            raw = configs[key].get("launchers", "").strip()
            if raw:
                return [e.strip() for e in raw.split(",") if e.strip()]
    return []


def resolve_preferred(scheme: str, run=None) -> str | None:
    """preferred://browser|filemanager -> desktop id. None when unknown. Fail-soft."""
    runner = run or _default_run
    try:
        if scheme == "preferred://browser":
            proc = runner(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True, text=True, timeout=5.0,
            )
            name = (proc.stdout or "").strip() if proc.returncode == 0 else ""
            return name if name.endswith(".desktop") else None
        if scheme == "preferred://filemanager":
            proc = runner(
                ["xdg-mime", "query", "default", "inode/directory"],
                capture_output=True, text=True, timeout=5.0,
            )
            name = (proc.stdout or "").strip() if proc.returncode == 0 else ""
            return name if name.endswith(".desktop") else None
    except Exception:
        return None
    return None


def launcher_entry_to_desktop(entry: str, run=None) -> str | None:
    """One raw launcher entry -> desktop id (or None). Pure-ish."""
    item = (entry or "").strip()
    if not item:
        return None
    if item.startswith("applications:"):
        name = item[len("applications:") :]
        return name if _DESKTOP_ID.match(name) else None
    if item in ("preferred://browser", "preferred://filemanager"):
        return resolve_preferred(item, run=run)
    if item.startswith("file://"):
        base = item.rsplit("/", 1)[-1]
        return base if _DESKTOP_ID.match(base) else None
    tail = item.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return tail if _DESKTOP_ID.match(tail) else None


def app_search_dirs() -> list[Path]:
    """Dirs holding .desktop files (same set as bridge /appicon). Pure."""
    home = Path.home()
    return [
        home / ".local/share/applications",
        Path("/usr/share/applications"),
        Path("/var/lib/flatpak/exports/share/applications"),
        home / ".local/share/flatpak/exports/share/applications",
    ]


def find_desktop(desktop: str, dirs=None) -> Path | None:
    """Locate a desktop id in the app dirs (user dir first). Never raises."""
    try:
        if not _DESKTOP_ID.match(desktop or ""):
            return None
        roots = [Path(d) for d in dirs] if dirs is not None else app_search_dirs()
        lowered = desktop.lower()
        for root in roots:
            try:
                hit = root / desktop
                if hit.is_file():
                    return hit
            except (OSError, ValueError):
                continue
        for root in roots:
            try:
                names = sorted(p.name for p in root.glob("*.desktop") if p.is_file())
            except (OSError, ValueError):
                continue
            for name in names:
                if name.lower() == lowered:
                    return root / name
    except Exception:
        pass
    return None


def desktop_name(path: Path) -> str:
    """Name[en] else Name from a .desktop file ("" when absent). Never raises."""
    generic, localized = "", ""
    try:
        section = ""
        with path.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1].strip()
                    continue
                if section != "Desktop Entry":
                    continue
                key, sep, value = line.partition("=")
                if not sep:
                    continue
                key, value = key.strip(), value.strip()
                if key == "Name[en]":
                    localized = value
                elif key == "Name" and not generic:
                    generic = value
    except (OSError, ValueError):
        pass
    except Exception:
        pass
    return localized or generic


def launchers(text=None, path=None, run=None, dirs=None, clock=None) -> list[dict]:
    """Pinned launchers as [{desktop, name}]. Cached 30 s. Never raises."""
    now_fn = clock or time.monotonic
    custom = (
        text is not None
        or path is not None
        or run is not None
        or dirs is not None
    )
    try:
        now = now_fn()
    except Exception:
        now = time.monotonic()
    try:
        if not custom and _launchers_cache and now - _launchers_cache["at"] < _LAUNCHERS_TTL_S:
            return [dict(item) for item in _launchers_cache["value"]]
    except (KeyError, TypeError):
        pass
    value: list[dict] = []
    try:
        if text is None:
            try:
                text = (Path(path) if path is not None else applets_path()).read_text()
            except (OSError, ValueError):
                text = ""
        entries = select_launcher_entries(text or "")
        runner = run or _default_run
        for entry in entries:
            try:
                desktop = launcher_entry_to_desktop(entry, run=runner)
            except Exception:
                desktop = None
            if not desktop:
                continue
            name = ""
            try:
                found = find_desktop(desktop, dirs=dirs)
                if found is not None:
                    name = desktop_name(found)
            except Exception:
                name = ""
            value.append({"desktop": desktop, "name": name or desktop[:-8]})
    except Exception:
        value = []
    if not custom:
        _launchers_cache.update(at=now, value=[dict(item) for item in value])
    return value


def validate_desktop_id(raw: object) -> str | None:
    """A launchable desktop id, or None. Pure (no slashes/paths/commands)."""
    if not isinstance(raw, str):
        return None
    item = raw.strip()
    if not _DESKTOP_ID.match(item) or "/" in item or item.startswith("."):
        return None
    return item


def launch_argv(desktop: str, path: Path, which=None) -> list[str] | None:
    """Detached launch argv: kstart > gtk-launch > kioclient. Pure-ish."""
    find = which or shutil.which
    stem = desktop[:-8] if desktop.endswith(".desktop") else desktop
    try:
        if find("kstart"):
            return ["kstart", "--application", stem]
        if find("gtk-launch"):
            return ["gtk-launch", stem]
        if find("kioclient"):
            return ["kioclient", "exec", str(path)]
    except Exception:
        return None
    return None


def launch_desktop(desktop: str, dirs=None, which=None, popen=None) -> bool:
    """Launch a validated desktop id detached. False on any failure."""
    valid = validate_desktop_id(desktop)
    if valid is None:
        return False
    try:
        path = find_desktop(valid, dirs=dirs)
        if path is None:
            return False
        argv = launch_argv(valid, path, which=which)
        if not argv:
            return False
        spawn = popen or subprocess.Popen
        spawn(
            argv,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def handle_launch(body: dict) -> tuple[int, dict]:
    """POST /launch {"desktop"}. Returns (http_code, payload). Pure-ish."""
    if not isinstance(body, dict):
        return 400, {"ok": False, "error": "invalid JSON body"}
    valid = validate_desktop_id(body.get("desktop"))
    if valid is None:
        return 400, {"ok": False, "error": "desktop must be a .desktop file name"}
    if find_desktop(valid) is None:
        return 400, {"ok": False, "error": f"unknown app {valid!r}"}
    return 200, {"ok": launch_desktop(valid)}


def parse_desktop_file(path: Path) -> dict | None:
    """One .desktop file -> app record, or None when hidden/skipped. Pure-ish."""
    try:
        section, fields = "", {}
        with path.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1].strip()
                    continue
                if section != "Desktop Entry":
                    continue
                key, sep, value = line.partition("=")
                if sep and key.strip() not in fields:
                    fields[key.strip()] = value.strip()
        if fields.get("NoDisplay", "").lower() == "true":
            return None
        if fields.get("Hidden", "").lower() == "true":
            return None
        only = fields.get("OnlyShowIn", "")
        if only and "KDE" not in [p.strip() for p in only.split(";") if p.strip()]:
            return None
        kind = fields.get("Type", "Application")
        if kind != "Application":
            return None
        name = fields.get("Name[en]", "") or fields.get("Name", "")
        if not name:
            return None
        cats = [c for c in (fields.get("Categories", "").split(";")) if c]
        return {
            "desktop": path.name,
            "name": name,
            "generic": fields.get("GenericName[en]", "") or fields.get("GenericName", ""),
            "categories": cats,
            "icon_key": path.name,
        }
    except (OSError, ValueError):
        return None
    except Exception:
        return None


def list_apps(dirs=None, clock=None) -> list[dict]:
    """All visible apps, deduped (user dir wins), sorted by name. Cached 60 s."""
    now_fn = clock or time.monotonic
    custom = dirs is not None
    try:
        now = now_fn()
    except Exception:
        now = time.monotonic()
    try:
        if not custom and _apps_cache and now - _apps_cache["at"] < _APPS_TTL_S:
            return [dict(item) for item in _apps_cache["value"]]
    except (KeyError, TypeError):
        pass
    apps: list[dict] = []
    try:
        roots = [Path(d) for d in dirs] if dirs is not None else app_search_dirs()
        seen: set[str] = set()
        for root in roots:
            try:
                files = sorted(p for p in root.glob("*.desktop") if p.is_file())
            except (OSError, ValueError):
                continue
            for path in files:
                if path.name in seen:
                    continue
                try:
                    rec = parse_desktop_file(path)
                except Exception:
                    rec = None
                if rec is None:
                    continue
                seen.add(path.name)
                apps.append(rec)
        apps.sort(key=lambda r: r["name"].casefold())
    except Exception:
        apps = []
    if not custom:
        _apps_cache.update(at=now, value=[dict(item) for item in apps])
    return apps


def _split_nmcli(value: str) -> list[str]:
    """Split nmcli -t output on unescaped colons. Pure."""
    parts, current, chars, i = [], [], value or "", 0
    while i < len(chars):
        c = chars[i]
        if c == "\\" and i + 1 < len(chars):
            current.append(chars[i + 1])
            i += 2
            continue
        if c == ":":
            parts.append("".join(current))
            current = []
        else:
            current.append(c)
        i += 1
    parts.append("".join(current))
    return parts


def wifi_state(run=None) -> dict | None:
    """{"on", "ssid"} or None when nmcli is unusable. Never raises."""
    runner = run or _default_run
    try:
        proc = runner(
            ["nmcli", "radio", "wifi"],
            capture_output=True, text=True, timeout=5.0,
        )
        if proc.returncode != 0:
            return None
        on = (proc.stdout or "").strip().lower() == "enabled"
        ssid = ""
        try:
            wproc = runner(
                ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi"],
                capture_output=True, text=True, timeout=5.0,
            )
            if wproc.returncode == 0:
                for line in (wproc.stdout or "").splitlines():
                    parts = _split_nmcli(line.strip())
                    if len(parts) >= 2 and parts[0].strip().lower() == "yes":
                        ssid = ":".join(parts[1:-1]).strip() if len(parts) > 2 else ""
                        break
        except Exception:
            pass
        return {"on": on, "ssid": ssid}
    except Exception:
        return None


def bluetooth_state(run=None) -> dict | None:
    """{"on"} or None when bluetoothctl is unusable. Never raises."""
    runner = run or _default_run
    try:
        proc = runner(
            ["bluetoothctl", "show"], capture_output=True, text=True, timeout=5.0
        )
        if proc.returncode != 0:
            return None
        match = re.search(r"^\s*Powered:\s*(yes|no)", proc.stdout or "", re.M | re.I)
        if not match:
            return None
        return {"on": match.group(1).lower() == "yes"}
    except Exception:
        return None


def volume_state(run=None) -> dict | None:
    """{"pct", "muted"} via wpctl (pactl fallback). None if none. Never raises."""
    runner = run or _default_run
    try:
        proc = runner(
            ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
            capture_output=True, text=True, timeout=5.0,
        )
        if proc.returncode == 0:
            match = re.search(r"Volume:\s*([0-9]*\.?[0-9]+)", proc.stdout or "")
            if match:
                try:
                    pct = round(float(match.group(1)) * 100)
                except ValueError:
                    pct = None
                if pct is not None:
                    return {
                        "pct": max(0, min(150, pct)),
                        "muted": "muted" in (proc.stdout or "").lower(),
                    }
    except Exception:
        pass
    try:
        vproc = runner(
            ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
            capture_output=True, text=True, timeout=5.0,
        )
        if vproc.returncode != 0:
            return None
        match = re.search(r"(\d{1,3})\s*%", vproc.stdout or "")
        if not match:
            return None
        muted = False
        try:
            mproc = runner(
                ["pactl", "get-sink-mute", "@DEFAULT_SINK@"],
                capture_output=True, text=True, timeout=5.0,
            )
            if mproc.returncode == 0:
                muted = "yes" in (mproc.stdout or "").lower()
        except Exception:
            muted = False
        return {"pct": max(0, min(150, int(match.group(1)))), "muted": muted}
    except Exception:
        return None


def backlight_root() -> Path:
    """Sysfs backlight dir. Pure."""
    return Path("/sys/class/backlight")


def brightness_state(root=None) -> dict | None:
    """{"pct"} from sysfs backlight, or None. Never raises."""
    try:
        base = Path(root) if root is not None else backlight_root()
        devs = sorted(p for p in base.iterdir() if (p / "brightness").is_file())
        if not devs:
            return None
        dev = devs[0]
        cur = int((dev / "brightness").read_text().strip())
        top = int((dev / "max_brightness").read_text().strip())
        if top <= 0:
            return None
        return {"pct": max(0, min(100, round(cur / top * 100)))}
    except (OSError, ValueError):
        return None
    except Exception:
        return None


def set_brightness(pct: int, root=None) -> bool:
    """Write sysfs brightness (needs root; fail-soft). Never raises."""
    try:
        level = int(pct)
        if not 1 <= level <= 100:
            return False
        base = Path(root) if root is not None else backlight_root()
        devs = sorted(p for p in base.iterdir() if (p / "brightness").is_file())
        if not devs:
            return False
        dev = devs[0]
        top = int((dev / "max_brightness").read_text().strip())
        if top <= 0:
            return False
        (dev / "brightness").write_text(str(max(1, round(top * level / 100))))
        return True
    except (OSError, ValueError):
        return False
    except Exception:
        return False


def dnd_state(run=None) -> bool | None:
    """Do-not-disturb from the Notifications Inhibited property. Never raises."""
    runner = run or _default_run
    try:
        proc = runner(
            [
                "dbus-send", "--session", "--print-reply",
                "--dest=org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.DBus.Properties.Get",
                "string:org.freedesktop.Notifications",
                "string:Inhibited",
            ],
            capture_output=True, text=True, timeout=5.0,
        )
        if proc.returncode != 0:
            return None
        match = re.search(r"boolean\s+(true|false)", proc.stdout or "", re.I)
        if not match:
            return None
        return match.group(1).lower() == "true"
    except Exception:
        return None


_AUTO = object()


def quick_state(run=None, volume=_AUTO, backlight=None) -> dict:
    """Snapshot for GET /quick. Fields are dicts or None. Never raises."""
    runner = run or _default_run
    try:
        wifi = wifi_state(run=runner)
    except Exception:
        wifi = None
    try:
        bluetooth = bluetooth_state(run=runner)
    except Exception:
        bluetooth = None
    if volume is _AUTO:
        try:
            vol: dict | None = volume_state(run=runner)
        except Exception:
            vol = None
    else:
        vol = volume if isinstance(volume, dict) else None
    try:
        brightness = brightness_state(root=backlight)
    except Exception:
        brightness = None
    try:
        dnd = dnd_state(run=runner)
    except Exception:
        dnd = None
    return {
        "wifi": wifi,
        "bluetooth": bluetooth,
        "volume": vol,
        "brightness": brightness,
        "dnd": dnd,
    }


_DND_WRITE_ERROR = (
    "dnd toggle not supported: notification Inhibit cookies are bound to "
    "the caller's D-Bus connection (a one-shot call is revoked on exit) "
    "and Plasma 6 exposes no persistent config key for this"
)


def handle_quick(body: dict, run=None, which=None, backlight=None) -> tuple[int, dict]:
    """POST /quick. Returns (http_code, payload). Pure-ish (injectable run)."""
    if not isinstance(body, dict):
        return 400, {"ok": False, "error": "invalid JSON body"}
    action = body.get("action")
    runner = run or _default_run
    find = which or shutil.which
    if action in ("wifi", "bluetooth", "dnd"):
        if not isinstance(body.get("on"), bool):
            return 400, {"ok": False, "error": '"on" must be bool'}
        want = body["on"]
        if action == "wifi":
            try:
                proc = runner(
                    ["nmcli", "radio", "wifi", "on" if want else "off"],
                    capture_output=True, text=True, timeout=5.0,
                )
                return 200, {"ok": proc.returncode == 0}
            except Exception:
                return 200, {"ok": False}
        if action == "bluetooth":
            try:
                proc = runner(
                    ["bluetoothctl", "power", "on" if want else "off"],
                    capture_output=True, text=True, timeout=5.0,
                )
                return 200, {"ok": proc.returncode == 0}
            except Exception:
                return 200, {"ok": False}
        return 200, {"ok": False, "error": _DND_WRITE_ERROR}
    if action == "volume":
        pct = body.get("pct")
        if isinstance(pct, bool) or not isinstance(pct, int) or not 0 <= pct <= 150:
            return 400, {"ok": False, "error": '"pct" must be 0..150'}
        level = max(0.0, min(1.5, pct / 100))
        try:
            if find("wpctl"):
                proc = runner(
                    ["wpctl", "set-volume", "-l", "1.5",
                     "@DEFAULT_AUDIO_SINK@", f"{level:.2f}"],
                    capture_output=True, text=True, timeout=5.0,
                )
            else:
                proc = runner(
                    ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"],
                    capture_output=True, text=True, timeout=5.0,
                )
            return 200, {"ok": proc.returncode == 0}
        except Exception:
            return 200, {"ok": False}
    if action == "mute":
        if not isinstance(body.get("on"), bool):
            return 400, {"ok": False, "error": '"on" must be bool'}
        flag = "1" if body["on"] else "0"
        try:
            if find("wpctl"):
                proc = runner(
                    ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", flag],
                    capture_output=True, text=True, timeout=5.0,
                )
            else:
                proc = runner(
                    ["pactl", "set-sink-mute", "@DEFAULT_SINK@", flag],
                    capture_output=True, text=True, timeout=5.0,
                )
            return 200, {"ok": proc.returncode == 0}
        except Exception:
            return 200, {"ok": False}
    if action == "brightness":
        pct = body.get("pct")
        if isinstance(pct, bool) or not isinstance(pct, int) or not 1 <= pct <= 100:
            return 400, {"ok": False, "error": '"pct" must be 1..100'}
        try:
            ok = set_brightness(pct, root=backlight)
        except Exception:
            ok = False
        if ok:
            return 200, {"ok": True}
        return 200, {"ok": False, "error": "brightness not writable (needs root?)"}
    return 400, {"ok": False, "error": "unknown action"}


_POWER_ACTIONS = ("lock", "sleep", "logout", "restart", "shutdown")


def power_argv(action: str) -> list[str] | None:
    """Validated power argv, or None. Pure."""
    if action == "lock":
        return ["loginctl", "lock-session"]
    if action == "sleep":
        return ["systemctl", "suspend"]
    if action == "logout":
        return [
            "dbus-send", "--session", "--print-reply",
            "--dest=org.kde.Shutdown", "/Shutdown", "org.kde.Shutdown.logout",
        ]
    if action == "restart":
        return ["systemctl", "reboot"]
    if action == "shutdown":
        return ["systemctl", "poweroff"]
    return None


def run_power(action: str, run=None) -> bool:
    """Execute one allowlisted power action. False unless it ran cleanly."""
    argv = power_argv(action)
    if argv is None:
        return False
    try:
        import contextlib

        with contextlib.suppress(Exception):
            from system import log_action

            log_action("power", action)
    except Exception:
        pass
    try:
        runner = run or _default_run
        proc = runner(argv, capture_output=True, text=True, timeout=5.0)
        return proc.returncode == 0
    except Exception:
        return False


def handle_power(body: dict) -> tuple[int, dict]:
    """POST /power {"action"}. Returns (http_code, payload). Pure-ish."""
    if not isinstance(body, dict):
        return 400, {"ok": False, "error": "invalid JSON body"}
    action = body.get("action")
    if action not in _POWER_ACTIONS:
        return 400, {"ok": False, "error": "action must be lock, sleep, logout, restart or shutdown"}
    return 200, {"ok": run_power(str(action))}
