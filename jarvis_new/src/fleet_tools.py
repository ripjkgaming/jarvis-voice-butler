"""Local device tools for Fleet requests.

Runs on the device that receives a fleet call (never on the controller). Each
tool is one fixed action: argv lists only, never a shell, timeouts on every
subprocess, and no destructive power actions (no shutdown, restart, sleep or
logout) and no arbitrary command execution. Windows, Linux and macOS.

Stdlib only. ``run`` never raises; it always returns a dict with ``ok``.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

MAX_PNG_BYTES = 6 * 1024 * 1024
TIMEOUT = 15.0
_APP_RE = re.compile(r"[A-Za-z0-9 ._-]{1,64}")
_EMAIL_RE = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}")
_BAD_URL_CHARS = re.compile(r"[\s\"\x00-\x1f\x7f]")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_ALL_OS = ["windows", "linux", "macos"]
_VOLUME_ACTIONS = ("get", "up", "down", "mute", "unmute", "set")
_MEDIA_KEYS = {"play_pause": 179, "next": 176, "prev": 177}  # Windows virtual keys
_MEDIA_OSA = {"play_pause": "playpause", "next": "next track", "prev": "previous track"}
_MEDIA_PLAYERCTL = {"play_pause": "play-pause", "next": "next", "prev": "previous"}

TOOLS: dict[str, dict] = {
    "sysinfo": {
        "description": "Device facts: hostname, OS, Python, CPU, load, memory, disk, uptime, battery.",
        "args": {},
        "platforms": list(_ALL_OS),
    },
    "lock": {
        "description": "Lock the screen.",
        "args": {},
        "platforms": list(_ALL_OS),
    },
    "notify": {
        "description": "Show a desktop notification.",
        "args": {
            "title": "str (max 100 chars, optional)",
            "message": "str (max 500 chars, required)",
        },
        "platforms": list(_ALL_OS),
    },
    "open_url": {
        "description": "Open an http or https URL in the default browser.",
        "args": {"url": "str (http/https, no spaces)"},
        "platforms": list(_ALL_OS),
    },
    "open_app": {
        "description": "Launch an installed application by name.",
        "args": {
            "name": "str (1-64 chars: letters, digits, space, dot, underscore, dash)"
        },
        "platforms": list(_ALL_OS),
    },
    "volume": {
        "description": "Get or change output volume. Windows: keystroke based, mute/unmute both toggle.",
        "args": {
            "action": "get|up|down|mute|unmute|set",
            "level": "int 0-100 (set only)",
        },
        "platforms": list(_ALL_OS),
    },
    "media": {
        "description": "Media playback control.",
        "args": {"action": "play_pause|next|prev"},
        "platforms": list(_ALL_OS),
    },
    "screenshot": {
        "description": "Capture the primary screen as PNG (base64, capped at 6 MB).",
        "args": {},
        "platforms": list(_ALL_OS),
    },
}


class ToolArgError(ValueError):
    """Bad user-supplied argument. Message is safe to return to the caller."""


def _platform() -> str:
    return sys.platform


def _os() -> str | None:
    p = _platform()
    if p.startswith("win"):
        return "windows"
    if p.startswith("linux"):
        return "linux"
    if p == "darwin":
        return "macos"
    return None


def _unavailable(tool: str) -> dict:
    return {"ok": False, "error": f"{tool} unavailable on this system"}


def _exec(
    argv: list[str], env: dict | None = None
) -> subprocess.CompletedProcess | None:
    """Run argv without a shell. None when the binary is missing."""
    kwargs: dict = {}
    if _platform().startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
            check=False,
            env=env,
            **kwargs,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", "timed out")
    except OSError as exc:
        return subprocess.CompletedProcess(argv, 126, "", str(exc))


def _failure(tool: str, res: subprocess.CompletedProcess | None) -> dict | None:
    """None when the call succeeded; otherwise the error dict to return."""
    if res is None:
        return _unavailable(tool)
    if res.returncode != 0:
        lines = (res.stderr or res.stdout or "").strip().splitlines()
        tail = lines[-1][:200] if lines else ""
        return {
            "ok": False,
            "error": f"{tool} failed (exit {res.returncode})"
            + (f": {tail}" if tail else ""),
        }
    return None


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


def _ps_argv(script: str) -> list[str]:
    # Script text is a fixed constant. User values only travel in env vars.
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]


def _text(
    args: dict, key: str, limit: int, *, required: bool = False, default: str = ""
) -> str:
    value = args.get(key, default)
    if not isinstance(value, str):
        raise ToolArgError(f"{key} must be text")
    value = value.strip()
    if required and not value:
        raise ToolArgError(f"{key} is required")
    if len(value) > limit:
        raise ToolArgError(f"{key} is longer than {limit} characters")
    if "\x00" in value:
        raise ToolArgError(f"{key} contains a NUL byte")
    return value


def _valid_url(url: object) -> str:
    if not isinstance(url, str):
        raise ToolArgError("url must be text")
    url = url.strip()
    if not url or len(url) > 2048 or _BAD_URL_CHARS.search(url):
        raise ToolArgError(
            "url is empty, too long, or has spaces or control characters"
        )
    try:
        parsed = urlparse(url)
        host = parsed.hostname
    except ValueError:
        raise ToolArgError("url is malformed") from None
    if parsed.scheme not in ("http", "https") or not host:
        raise ToolArgError("url must be http or https with a host")
    return url


def _valid_app(name: object) -> str:
    if not isinstance(name, str):
        raise ToolArgError("name must be text")
    name = name.strip()
    if not _APP_RE.fullmatch(name) or name.startswith("-"):
        raise ToolArgError(
            "name must be 1-64 letters, digits, spaces, dots, dashes or underscores"
        )
    return name


def _level(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ToolArgError("level must be an integer 0-100")
    try:
        number = int(value)
    except ValueError:
        raise ToolArgError("level must be an integer 0-100") from None
    if not 0 <= number <= 100:
        raise ToolArgError("level must be 0-100")
    return number


def _parse_percent(text: str) -> int | None:
    m = re.search(r"(\d+)%", text)
    if m:
        return int(m.group(1))
    m = re.search(r"Volume:\s*(\d*\.?\d+)", text)  # wpctl prints 0.45
    if m:
        return round(float(m.group(1)) * 100)
    return None


# -------------------------------------------------------------------- sysinfo
_WIN_SYSINFO_PS = (
    "$o = Get-CimInstance Win32_OperatingSystem; "
    "$b = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1; "
    "[pscustomobject]@{ total_kb = $o.TotalVisibleMemorySize; free_kb = $o.FreePhysicalMemory; "
    "uptime_s = [int]((Get-Date) - $o.LastBootUpTime).TotalSeconds; "
    "battery = $b.EstimatedChargeRemaining } | ConvertTo-Json -Compress"
)


def _linux_probe() -> dict:
    out: dict = {}
    meminfo: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            meminfo[key] = int(parts[0]) * 1024
    if "MemTotal" in meminfo:
        out["memory"] = {
            "total_bytes": meminfo["MemTotal"],
            "available_bytes": meminfo.get("MemAvailable"),
        }
    out["uptime_s"] = int(float(Path("/proc/uptime").read_text().split()[0]))
    for cap in sorted(Path("/sys/class/power_supply").glob("BAT*/capacity")):
        try:
            out["battery_pct"] = int(cap.read_text().strip())
            break
        except (OSError, ValueError):
            continue
    return out


def _macos_probe() -> dict:
    out: dict = {}
    res = _exec(["sysctl", "-n", "hw.memsize"])
    if res is not None and res.returncode == 0 and res.stdout.strip().isdigit():
        out["memory"] = {"total_bytes": int(res.stdout.strip())}
    res = _exec(["sysctl", "-n", "kern.boottime"])
    m = (
        re.search(r"sec = (\d+)", res.stdout)
        if res is not None and res.returncode == 0
        else None
    )
    if m:
        out["uptime_s"] = int(time.time()) - int(m.group(1))
    res = _exec(["pmset", "-g", "batt"])
    m = (
        re.search(r"(\d+)%", res.stdout)
        if res is not None and res.returncode == 0
        else None
    )
    if m:
        out["battery_pct"] = int(m.group(1))
    return out


def _windows_probe() -> dict:
    res = _exec(_ps_argv(_WIN_SYSINFO_PS))
    if res is None or res.returncode != 0:
        return {}
    data = json.loads(res.stdout)
    out: dict = {"uptime_s": data.get("uptime_s")}
    if data.get("total_kb"):
        out["memory"] = {
            "total_bytes": int(data["total_kb"]) * 1024,
            "available_bytes": int(data.get("free_kb") or 0) * 1024,
        }
    if data.get("battery") is not None:
        out["battery_pct"] = data["battery"]
    return out


def _tool_sysinfo(args: dict) -> dict:
    os_name = _os()
    info: dict = {
        "hostname": socket.gethostname(),
        "platform": os_name or _platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "load_avg": _safe(os.getloadavg) if os_name in ("linux", "macos") else None,
        "memory": None,
        "uptime_s": None,
        "battery_pct": None,
        "disk": None,
    }
    probe = {
        "linux": _linux_probe,
        "macos": _macos_probe,
        "windows": _windows_probe,
    }.get(os_name)
    if probe is not None:
        info.update(_safe(probe) or {})
    usage = _safe(shutil.disk_usage, Path.home())
    if usage is not None:
        info["disk"] = {"total_bytes": usage.total, "free_bytes": usage.free}
    if isinstance(info.get("load_avg"), tuple):
        info["load_avg"] = list(info["load_avg"])
    return {"ok": True, **info}


# ----------------------------------------------------------------- lock, notify
_LOCK_ARGV = {
    "windows": ["rundll32.exe", "user32.dll,LockWorkStation"],
    "linux": ["loginctl", "lock-session"],
    "macos": ["pmset", "displaysleepnow"],
}


def _tool_lock(args: dict) -> dict:
    os_name = _os()
    if os_name is None:
        return _unavailable("lock")
    res = _exec(_LOCK_ARGV[os_name])
    return _failure("lock", res) or {"ok": True, "action": "lock"}


_WIN_NOTIFY_PS = (
    "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
    "$n = New-Object System.Windows.Forms.NotifyIcon; "
    "$n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true; "
    "$n.ShowBalloonTip(5000, $env:JF_TITLE, $env:JF_MESSAGE, [System.Windows.Forms.ToolTipIcon]::Info); "
    "Start-Sleep -Seconds 6; $n.Dispose()"
)
_OSA_NOTIFY = [
    "on run argv",
    "display notification (item 2 of argv) with title (item 1 of argv)",
    "end run",
]


def _tool_notify(args: dict) -> dict:
    title = _text(args, "title", 100, default="Jarvis")
    message = _text(args, "message", 500, required=True)
    os_name = _os()
    if os_name == "windows":
        res = _exec(
            _ps_argv(_WIN_NOTIFY_PS),
            env={**os.environ, "JF_TITLE": title, "JF_MESSAGE": message},
        )
    elif os_name == "linux":
        res = _exec(["notify-send", "--", title, message])
    elif os_name == "macos":
        argv = ["osascript"]
        for line in _OSA_NOTIFY:
            argv += ["-e", line]
        res = _exec([*argv, title, message])
    else:
        return _unavailable("notify")
    return _failure("notify", res) or {"ok": True, "title": title}


# ------------------------------------------------------------------ open_url/app
def _tool_open_url(args: dict) -> dict:
    url = _valid_url(args.get("url"))
    os_name = _os()
    if os_name == "windows":
        # rundll32 avoids cmd.exe, so & | ^ in the URL are inert.
        res = _exec(["rundll32.exe", "url.dll,FileProtocolHandler", url])
    elif os_name == "linux":
        res = _exec(["xdg-open", url])
    elif os_name == "macos":
        res = _exec(["open", url])
    else:
        return _unavailable("open_url")
    return _failure("open_url", res) or {"ok": True, "url": url}


def _tool_open_app(args: dict) -> dict:
    name = _valid_app(args.get("name"))
    os_name = _os()
    if os_name == "windows":
        res = _exec(["cmd", "/c", "start", "", name])
    elif os_name == "linux":
        res = _exec(["gtk-launch", name])
    elif os_name == "macos":
        res = _exec(["open", "-a", name])
    else:
        return _unavailable("open_app")
    return _failure("open_app", res) or {"ok": True, "name": name}


# ------------------------------------------------------------- volume and media
_WIN_KEY_PS = (
    "$ws = New-Object -ComObject WScript.Shell; "
    "for ($i = 0; $i -lt [int]$env:JF_COUNT; $i++) { $ws.SendKeys([string][char][int]$env:JF_KEY) }"
)
_WIN_VOLUME_UP, _WIN_VOLUME_DOWN, _WIN_VOLUME_MUTE = 175, 174, 173
_WIN_STEPS = 5  # each Windows volume key press is about 2 percent


def _win_keys(key: int, count: int) -> subprocess.CompletedProcess | None:
    return _exec(
        _ps_argv(_WIN_KEY_PS),
        env={**os.environ, "JF_KEY": str(key), "JF_COUNT": str(count)},
    )


def _volume_windows(action: str, level: int | None) -> dict:
    if action == "get":
        return {
            "ok": False,
            "error": "volume get is not available on Windows; use up, down, mute or set",
        }
    if action == "up":
        res = _win_keys(_WIN_VOLUME_UP, _WIN_STEPS)
    elif action == "down":
        res = _win_keys(_WIN_VOLUME_DOWN, _WIN_STEPS)
    elif action in ("mute", "unmute"):
        res = _win_keys(_WIN_VOLUME_MUTE, 1)  # mute key toggles; no state to read
    else:  # set: drop to 0 (50 presses of 2 percent), then climb to level
        res = _win_keys(_WIN_VOLUME_DOWN, 50)
        err = _failure("volume", res)
        if err is not None:
            return err
        res = _win_keys(_WIN_VOLUME_UP, (level or 0) // 2)
    return _failure("volume", res) or {"ok": True, "action": action, "level": level}


def _linux_volume_argv(action: str, level: int | None) -> tuple[list[str], list[str]]:
    """(wpctl argv, pactl argv) for one volume action."""
    wp = ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@"]
    pa = ["pactl", "set-sink-volume", "@DEFAULT_SINK@"]
    mute_wp = ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@"]
    mute_pa = ["pactl", "set-sink-mute", "@DEFAULT_SINK@"]
    table = {
        "get": (
            ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
            ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
        ),
        "up": ([*wp, "10%+"], [*pa, "+10%"]),
        "down": ([*wp, "10%-"], [*pa, "-10%"]),
        "mute": ([*mute_wp, "1"], [*mute_pa, "1"]),
        "unmute": ([*mute_wp, "0"], [*mute_pa, "0"]),
        "set": ([*wp, f"{level}%"], [*pa, f"{level}%"]),
    }
    return table[action]


def _volume_linux(action: str, level: int | None) -> dict:
    for argv in _linux_volume_argv(action, level):
        res = _exec(argv)
        if res is None:  # binary missing: try the other audio stack
            continue
        err = _failure("volume", res)
        if err is not None:
            return err
        if action == "get":
            return {
                "ok": True,
                "action": "get",
                "level": _parse_percent(res.stdout),
                "muted": "MUTED" in res.stdout,
            }
        return {"ok": True, "action": action, "level": level}
    return _unavailable("volume")


def _volume_macos(action: str, level: int | None) -> dict:
    base = ["osascript", "-e"]
    if action == "get":
        res = _exec([*base, "output volume of (get volume settings)"])
        err = _failure("volume", res)
        if err is not None:
            return err
        text = res.stdout.strip()
        return {
            "ok": True,
            "action": "get",
            "level": int(text) if text.isdigit() else None,
        }
    if action == "set":
        argv = [
            "osascript",
            "-e",
            "on run argv",
            "-e",
            "set volume output volume (item 1 of argv as integer)",
            "-e",
            "end run",
            str(level),
        ]
        res = _exec(argv)
    else:
        lines = {
            "up": "set volume output volume ((output volume of (get volume settings)) + 10)",
            "down": "set volume output volume ((output volume of (get volume settings)) - 10)",
            "mute": "set volume with output muted",
            "unmute": "set volume without output muted",
        }
        res = _exec([*base, lines[action]])
    return _failure("volume", res) or {"ok": True, "action": action, "level": level}


def _tool_volume(args: dict) -> dict:
    action = args.get("action")
    if action not in _VOLUME_ACTIONS:
        raise ToolArgError("action must be get, up, down, mute, unmute or set")
    level = _level(args.get("level")) if action == "set" else None
    os_name = _os()
    if os_name == "windows":
        return _volume_windows(action, level)
    if os_name == "linux":
        return _volume_linux(action, level)
    if os_name == "macos":
        return _volume_macos(action, level)
    return _unavailable("volume")


_OSA_MEDIA = [
    "on run argv",
    'set appName to "Music"',
    'if application "Spotify" is running then set appName to "Spotify"',
    r'run script ("tell application \"" & appName & "\" to " & (item 1 of argv))',
    "end run",
]


def _tool_media(args: dict) -> dict:
    action = args.get("action")
    if action not in _MEDIA_KEYS:
        raise ToolArgError("action must be play_pause, next or prev")
    os_name = _os()
    if os_name == "windows":
        res = _win_keys(_MEDIA_KEYS[action], 1)
    elif os_name == "linux":
        res = _exec(["playerctl", _MEDIA_PLAYERCTL[action]])
    elif os_name == "macos":
        argv = ["osascript"]
        for line in _OSA_MEDIA:
            argv += ["-e", line]
        res = _exec([*argv, _MEDIA_OSA[action]])
    else:
        return _unavailable("media")
    return _failure("media", res) or {"ok": True, "action": action}


# ------------------------------------------------------------------ screenshot
def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or not data.startswith(_PNG_MAGIC):
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


_WIN_SHOT_PS = (
    "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
    "$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
    "$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height; "
    "$g = [System.Drawing.Graphics]::FromImage($bmp); "
    "$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size); "
    "$bmp.Save($env:JF_OUT, [System.Drawing.Imaging.ImageFormat]::Png); "
    "$g.Dispose(); $bmp.Dispose()"
)


def _capture(os_name: str, path: str) -> subprocess.CompletedProcess | None:
    if os_name == "windows":
        return _exec(_ps_argv(_WIN_SHOT_PS), env={**os.environ, "JF_OUT": path})
    if os_name == "macos":
        return _exec(["screencapture", "-x", path])
    for argv in (
        ["grim", path],
        ["spectacle", "-b", "-n", "-o", path],
        ["scrot", "-o", path],
    ):
        res = _exec(argv)
        if res is not None:
            return res
    return None


def _tool_screenshot(args: dict) -> dict:
    os_name = _os()
    if os_name is None:
        return _unavailable("screenshot")
    fd, path = tempfile.mkstemp(prefix="jarvis-fleet-", suffix=".png")
    os.close(fd)
    try:
        err = _failure("screenshot", _capture(os_name, path))
        if err is not None:
            return err
        data = Path(path).read_bytes()
        if len(data) > MAX_PNG_BYTES:
            return {"ok": False, "error": "screenshot is larger than 6 MB"}
        size = _png_size(data)
        if size is None:
            return {"ok": False, "error": "screenshot produced no PNG"}
        return {
            "ok": True,
            "png_b64": base64.b64encode(data).decode("ascii"),
            "width": size[0],
            "height": size[1],
        }
    except OSError as exc:
        return {"ok": False, "error": f"screenshot failed: {exc.__class__.__name__}"}
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


# --------------------------------------------------------------- partner accounts
def _home() -> Path:
    return Path(os.environ.get("JARVIS_HOME", "").strip() or Path.home() / ".jarvis")


def partner_accounts() -> list[str]:
    """Partner Tailscale account emails, lowercased and deduplicated.

    Sources: ``partner_accounts`` in ``$JARVIS_HOME/fleet/config.json`` and the
    comma separated ``JARVIS_FLEET_PARTNERS`` env var. Emails are private: this
    repo is public, so never write real addresses into code, tests or docs.
    """
    try:
        data = json.loads((_home() / "fleet" / "config.json").read_text())
    except (OSError, ValueError):
        data = {}
    raw = data.get("partner_accounts") if isinstance(data, dict) else None
    items = list(raw) if isinstance(raw, list) else []
    items += os.environ.get("JARVIS_FLEET_PARTNERS", "").split(",")
    found: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        email = item.strip().lower()
        if _EMAIL_RE.fullmatch(email) and email not in found:
            found.append(email)
    return found


# -------------------------------------------------------------------- dispatch
_HANDLERS = {
    "sysinfo": _tool_sysinfo,
    "lock": _tool_lock,
    "notify": _tool_notify,
    "open_url": _tool_open_url,
    "open_app": _tool_open_app,
    "volume": _tool_volume,
    "media": _tool_media,
    "screenshot": _tool_screenshot,
}


def run(tool: str, args: dict | None) -> dict:
    """Run one fleet tool. Never raises; errors come back as ``ok: False``."""
    try:
        if not isinstance(tool, str) or tool not in _HANDLERS:
            return {"ok": False, "error": f"unknown tool: {tool}"}
        if args is not None and not isinstance(args, dict):
            return {"ok": False, "error": "args must be an object"}
        return _HANDLERS[tool](args or {})
    except ToolArgError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"{tool} failed: {exc.__class__.__name__}"}
