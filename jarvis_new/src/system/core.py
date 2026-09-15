"""Safe-core PC tools: date/time, math, clipboard, screenshots, volume,
media player, brightness, battery, disk, files, todos, aliases.

Ported from the proven laptop Jarvis logic (~/jarvis/src/new_jarvis/):
- skill_calc: safe arithmetic via ast (no eval of names/calls)
- skill_clipboard: wl-paste / xclip / xsel read
- skill_screenshot: spectacle / import fullscreen capture, crop via PIL
- skill_volume: pactl get/set/mute
- skill_media: playerctl status + play/pause/next/previous/seek/loop/shuffle
- skill_brightness: KDE PowerDevil qdbus, sysfs backlight fallback (read-only
  set via D-Bus; sysfs write may need root and reports so)
- skill_battery: upower percentage + state
- skill_files: find/recent/disk/list/read/write/mkdir/move/copy/delete,
  confined to /home/ripjk (+ /tmp), delete moves to trash
- skill_todo: persistent JSON list with fuzzy done-matching
- aliases/shortcuts: persistent JSON named commands (data/shortcuts.json)

Every tool refuses unless JARVIS_LOCAL=1 (see system/__init__.require_local).
"""

from __future__ import annotations

import ast
import asyncio
import datetime
import difflib
import json
import re
import shutil
import time
from pathlib import Path

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local, run_cmd

DATA_DIR = Path.home() / ".jarvis" / "voice-butler"
TODOS_PATH = DATA_DIR / "todos.json"
SHORTCUTS_PATH = DATA_DIR / "shortcuts.json"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
HOME_ROOT = Path("/home/ripjk").resolve()

_TODO_STOP = {
    "a",
    "an",
    "and",
    "do",
    "done",
    "finish",
    "finished",
    "get",
    "i",
    "it",
    "my",
    "on",
    "please",
    "task",
    "the",
    "this",
    "to",
    "was",
    "with",
}


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2))


def _todo_terms(text: str) -> set[str]:
    terms = set()
    for word in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if word in _TODO_STOP or len(word) < 2:
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if len(word) > len(suffix) + 3 and word.endswith(suffix):
                word = word[: -len(suffix)]
                break
        terms.add(word)
    return terms


def _todo_match_score(query: str, candidate: str) -> float:
    q = _todo_terms(query)
    c = _todo_terms(candidate)
    if not q or not c:
        return 0.0
    overlap = len(q & c) / max(1, min(len(q), len(c)))
    words = difflib.SequenceMatcher(
        None, " ".join(sorted(q)), " ".join(sorted(c))
    ).ratio()
    return max(overlap, words * 0.72)


def _resolve_user_path(p: str) -> Path | None:
    """Absolute-or-~/ path confined to /home/ripjk (+ /tmp). None = refused."""
    s = (p or "").strip()[:400]
    if not s:
        return None
    if s.startswith("~"):
        s = str(Path.home() / s[1:].lstrip("/"))
    base = Path(s) if s.startswith("/") else Path.cwd() / s
    try:
        rp = base.resolve()
    except Exception:
        rp = base.absolute()
    if (
        rp == HOME_ROOT
        or HOME_ROOT in rp.parents
        or rp == Path("/tmp")
        or str(rp).startswith("/tmp/")
    ):
        return rp
    return None


def _trash_path(p: Path) -> Path:
    d = Path("/home/ripjk/.local/share/Trash/files")
    d.mkdir(parents=True, exist_ok=True)
    dest = d / p.name
    i = 1
    while dest.exists():
        dest = d / f"{p.stem}_{i}{p.suffix}"
        i += 1
    return dest


def evaluate_math(expr: str) -> float | int:
    """Safe arithmetic: numbers + - * / % ** // () only. Raises ValueError."""
    expr = expr.strip()[:120]
    if not expr or not set(expr) <= set("0123456789+-*/%(). "):
        raise ValueError("calc takes arithmetic only")
    node = ast.parse(expr, mode="eval")
    for n in ast.walk(node):
        if not isinstance(
            n,
            (
                ast.Expression,
                ast.BinOp,
                ast.UnaryOp,
                ast.Constant,
                ast.Load,
                ast.Add,
                ast.Sub,
                ast.Mult,
                ast.Div,
                ast.Mod,
                ast.Pow,
                ast.USub,
                ast.UAdd,
                ast.FloorDiv,
            ),
        ):
            raise ValueError("unsupported expression")
    return eval(compile(node, "<calc>", "eval"), {"__builtins__": {}})


class SystemTools:
    """Local PC-control tools. Register via .tools on the SystemAgent."""

    def __init__(self) -> None:
        self._confirmed_power: str | None = None

    @property
    def tools(self) -> list:
        return [
            self.tell_time,
            self.do_math,
            self.read_clipboard,
            self.take_os_screenshot,
            self.read_screen_text,
            self.set_volume,
            self.media_control,
            self.now_playing,
            self.set_brightness,
            self.battery_status,
            self.disk_space,
            self.find_files,
            self.list_dir,
            self.read_file,
            self.write_file,
            self.move_file,
            self.copy_file,
            self.delete_file,
            self.recent_downloads,
            self.manage_todo,
            self.remember_alias,
            self.run_alias,
            self.list_aliases,
            self.open_app,
            self.window_action,
            self.confirm_power_action,
            self.power_control,
        ]

    # --- talk / core ---

    @function_tool()
    async def tell_time(self, context: RunContext) -> dict[str, str]:
        """Tell the current local time and date."""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        now = datetime.datetime.now().astimezone()
        log_action("datetime", now.isoformat())
        return {
            "datetime": now.strftime("%A %Y-%m-%d %H:%M %Z"),
            "say": now.strftime("It is %A, %H:%M."),
        }

    @function_tool()
    async def do_math(self, context: RunContext, expr: str) -> dict[str, str]:
        """Evaluate safe arithmetic ("12% of 85" -> "12*85/100").

        Args:
            expr: Numbers with + - * / % ** // and parentheses only.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        try:
            # Voice phrasing: "12% of 85" means 12*85/100.
            m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*of\s*(\d+(?:\.\d+)?)\s*", expr)
            if m:
                value = float(m.group(1)) * float(m.group(2)) / 100
            else:
                value = evaluate_math(expr)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            raise ToolError(f"calc failed: {exc}") from exc
        log_action("calc", f"{expr}={value}")
        return {"value": str(value), "say": f"{expr.strip()} equals {value}."}

    @function_tool()
    async def read_clipboard(self, context: RunContext) -> dict[str, str]:
        """Read the laptop clipboard text (Wayland wl-paste, xclip fallback)."""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        for cmd in (
            ("wl-paste",),
            ("xclip", "-o", "-selection", "clipboard"),
            ("xsel", "--clipboard", "--output"),
        ):
            _rc, out, _ = await run_cmd(*cmd, timeout=5.0)
            if _rc == 0 and out.strip():
                log_action("clipboard", f"read {len(out)} chars")
                return {"text": out[:2000], "say": out[:500]}
        raise ToolError("The clipboard is unreadable right now.")

    # --- screen ---

    @function_tool()
    async def take_os_screenshot(
        self, context: RunContext, monitor: str | None = None
    ) -> dict[str, str]:
        """Capture the real desktop screen (not the agent browser).

        Args:
            monitor: Optional "1"/"DP-1" to crop one output, or "all".
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        full = SCREENSHOTS_DIR / f"full-{ts}.png"
        grabbed = False
        for cmd in (
            ("spectacle", "-b", "-n", "-o", str(full)),
            ("import", "-window", "root", str(full)),
        ):
            rc, _, _ = await run_cmd(*cmd, timeout=20.0)
            if rc == 0 and full.exists() and full.stat().st_size > 1000:
                grabbed = True
                break
        if not grabbed:
            raise ToolError("Screenshot failed; I need spectacle or imagemagick.")
        mon = (monitor or "").strip().lower()
        if not mon:
            log_action("screenshot", str(full))
            return {"path": str(full), "say": f"Screenshot saved to {full.name}."}
        # Crop per kscreen-doctor geometry when a monitor is requested.
        _rc, out, _ = await run_cmd("kscreen-doctor", "-o", timeout=10.0)
        outputs: list[tuple[int, str, tuple[int, int, int, int]]] = []
        num = name = None
        ansi = re.compile(r"\x1b\[[0-9;]*m")
        for ln in (out or "").splitlines():
            ln = ansi.sub("", ln)
            m = re.search(r"Output:\s*(\d*)\s*(\S+)", ln)
            if m:
                num = int(m.group(1)) if m.group(1).isdigit() else None
                name = m.group(2)
            g = re.search(r"Geometry:\s*(\d+),(\d+)\s+(\d+)x(\d+)", ln)
            if g and name is not None and num is not None:
                outputs.append((num, name, tuple(int(x) for x in g.groups())))
                num, name = None, None
        if not outputs:
            return {"path": str(full), "say": f"Saved {full.name} (full desktop)."}
        if mon in ("all", "every", "each"):
            targets = outputs
        elif mon.lstrip("-").isdigit():
            targets = [o for o in outputs if o[0] == int(mon)]
        else:
            targets = [o for o in outputs if o[1].upper() == mon.upper()]
        if not targets:
            known = ", ".join(f"{n} ({nm})" for n, nm, _ in outputs)
            return {"path": str(full), "say": f"No monitor {monitor}. I see: {known}."}
        try:
            from PIL import Image

            img = Image.open(full)
            paths = []
            for n, nm, (x, y, w, h) in targets:
                p = SCREENSHOTS_DIR / f"monitor{n}-{nm}-{ts}.png"
                try:
                    img.crop((x, y, x + w, y + h)).save(p)
                    paths.append(str(p))
                except Exception:
                    continue
            with __import__("contextlib").suppress(Exception):
                full.unlink()
            if not paths:
                raise ToolError("Screenshot crop failed.")
            log_action("screenshot", ",".join(paths))
            return {"path": paths[0], "say": f"Saved {len(paths)} screenshots."}
        except ImportError:
            return {"path": str(full), "say": f"Saved full desktop to {full.name}."}

    @function_tool()
    async def read_screen_text(self, context: RunContext) -> dict[str, str]:
        """OCR the current screen text (screenshot + tesseract)."""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        if shutil.which("tesseract") is None:
            raise ToolError("Screen reading needs tesseract installed.")
        shot = await self.take_os_screenshot(context)
        _rc, out, _ = await run_cmd("tesseract", shot["path"], "stdout", timeout=30.0)
        if _rc != 0 or not out.strip():
            raise ToolError("I could not read any text on the screen.")
        text = re.sub(r"\s+", " ", out).strip()
        log_action("ocr", f"{len(text)} chars from {shot['path']}")
        return {"text": text[:4000], "say": text[:800]}

    # --- sound / media ---

    @function_tool()
    async def set_volume(
        self, context: RunContext, action: str, level: int = 50
    ) -> dict[str, str]:
        """Get/set/mute the system volume via PulseAudio.

        Args:
            action: One of status, up, down, set, mute, unmute.
            level: 0-150 for set.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        action = action.lower()

        async def pct() -> str:
            _rc, out, _ = await run_cmd(
                "pactl", "get-sink-volume", "@DEFAULT_SINK@", timeout=5.0
            )
            m = re.search(r"(\d+)%", out or "")
            return m.group(1) + "%" if m else "?"

        if action == "status":
            _rc, out, _ = await run_cmd(
                "pactl", "get-sink-mute", "@DEFAULT_SINK@", timeout=5.0
            )
            muted = "yes" in (out or "").lower()
            return {"say": f"Volume {await pct()}{' (muted)' if muted else ''}."}
        if action in ("mute", "unmute"):
            await run_cmd(
                "pactl",
                "set-sink-mute",
                "@DEFAULT_SINK@",
                "1" if action == "mute" else "0",
                timeout=5.0,
            )
            log_action("volume", action)
            return {"say": f"{'Muted' if action == 'mute' else 'Unmuted'}."}
        delta = None
        if action in ("up", "down"):
            delta = "+5%" if action == "up" else "-5%"
        elif action in ("set", "level"):
            try:
                delta = f"{max(0, min(150, int(level)))}%"
            except Exception:
                raise ToolError("Volume level must be 0 to 150.") from None
        else:
            raise ToolError(f"Unknown volume action {action}.")
        await run_cmd("pactl", "set-sink-volume", "@DEFAULT_SINK@", delta, timeout=5.0)
        log_action("volume", f"{action} {delta}")
        return {"say": f"Volume {await pct()}."}

    @function_tool()
    async def media_control(
        self, context: RunContext, action: str, value: str = ""
    ) -> dict[str, str]:
        """Control the local music player via playerctl.

        Args:
            action: play, pause, play-pause, next, previous, seek, loop,
                shuffle, speed, or stop.
            value: e.g. "+30"/"-10"/"1:30" for seek, Track/Playlist/None
                for loop, on/off for shuffle, 1.5 for speed.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        action = action.lower().replace("_", "-")
        cmd: list[str] = []
        if action in ("play", "pause", "play-pause", "next", "previous", "stop"):
            cmd = ["playerctl", action]
        elif action == "seek":
            v = value.strip()
            m = re.fullmatch(r"(\d+):(\d+)", v)
            if m:
                v = str(int(m.group(1)) * 60 + int(m.group(2)))
            elif re.fullmatch(r"[+-]?\d+", v):
                v = v if v.startswith(("+", "-")) else f"+{v}"
            else:
                raise ToolError("Seek takes seconds like +30, -10, or 1:30.")
            cmd = ["playerctl", "position", v]
        elif action == "loop":
            mode = value.strip().capitalize() or "None"
            if mode not in ("Track", "Playlist", "None"):
                raise ToolError("Loop mode is Track, Playlist, or None.")
            cmd = ["playerctl", "loop", mode]
        elif action == "shuffle":
            mode = value.strip().capitalize()
            if mode not in ("On", "Off"):
                # Toggle: read current then flip.
                _rc, out, _ = await run_cmd("playerctl", "shuffle", timeout=5.0)
                mode = "Off" if (out or "").strip().lower() == "on" else "On"
            cmd = ["playerctl", "shuffle", mode]
        elif action == "speed":
            try:
                rate = float(value or 1.0)
            except ValueError:
                raise ToolError("Speed takes a number like 1.5.") from None
            cmd = ["playerctl", "rate", str(rate)]
        else:
            raise ToolError(f"Unknown media action {action}.")
        rc, out, err = await run_cmd(*cmd, timeout=8.0)
        if rc != 0:
            raise ToolError(f"No music player responded ({(err or out)[:150]}).")
        log_action("media", f"{action} {value}")
        return {"say": f"Player: {action}."}

    @function_tool()
    async def now_playing(self, context: RunContext) -> dict[str, str]:
        """Report what the local music player is playing."""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        rc, players, _ = await run_cmd("playerctl", "-l", timeout=5.0)
        if rc != 0 or not players.strip():
            return {"say": "Nothing is playing right now."}
        first = players.splitlines()[0].strip()
        rc, status, _ = await run_cmd("playerctl", "-p", first, "status", timeout=5.0)
        rc, meta, _ = await run_cmd(
            "playerctl",
            "-p",
            first,
            "metadata",
            "--format",
            "{{artist}} - {{title}} ({{album}})",
            timeout=5.0,
        )
        log_action("media", f"now-playing {first}")
        return {
            "player": first,
            "status": (status or "").strip(),
            "track": (meta or "").strip(),
            "say": f"{(meta or 'Unknown track').strip()} is {(status or '').strip()}.",
        }

    # --- devices ---

    @function_tool()
    async def set_brightness(
        self, context: RunContext, action: str, level: int = 50
    ) -> dict[str, str]:
        """Get/set laptop panel brightness (KDE PowerDevil D-Bus first).

        Args:
            action: One of status, up, down, set.
            level: 0-100 for set.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        action = action.lower()
        svc = "org.kde.Solid.PowerManagement"
        path = "/org/kde/Solid/PowerManagement/Actions/BrightnessControl"
        iface = "org.kde.Solid.PowerManagement.Actions.BrightnessControl"
        _rc, cur_s, _ = await run_cmd("qdbus", svc, path, iface + ".brightness")
        _rc2, max_s, _ = await run_cmd("qdbus", svc, path, iface + ".brightnessMax")
        try:
            cur, mx = int((cur_s or "0").strip()), int((max_s or "0").strip())
            has_dbus = mx > 0
        except ValueError:
            has_dbus = False
            cur = mx = 0
        if has_dbus:
            pct_now = round(cur / mx * 100)
            if action == "status":
                return {"say": f"Brightness {pct_now}%."}
            if action == "up":
                new = min(mx, cur + mx * 10 // 100)
            elif action == "down":
                new = max(0, cur - mx * 10 // 100)
            elif action == "set":
                new = max(0, min(mx, mx * int(level) // 100))
            else:
                raise ToolError(f"Unknown brightness action {action}.")
            await run_cmd(
                "qdbus", svc, path, iface + ".setBrightness", str(new), timeout=5.0
            )
            log_action("brightness", f"{action} {round(new / mx * 100)}%")
            return {"say": f"Brightness {round(new / mx * 100)}%."}
        raise ToolError("Brightness control is unavailable on this display.")

    @function_tool()
    async def battery_status(self, context: RunContext) -> dict[str, str]:
        """Report battery percentage and charging state via upower."""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        _rc, out, _ = await run_cmd("upower", "-e", timeout=5.0)
        bat = next(
            (ln for ln in (out or "").splitlines() if "battery" in ln.lower()), ""
        )
        if not bat:
            return {"say": "No battery found; this may be a desktop."}
        _rc, info, _ = await run_cmd("upower", "-i", bat.strip(), timeout=5.0)
        pct = re.search(r"percentage:\s*(\S+)", info or "")
        state = re.search(r"state:\s*(\S+)", info or "")
        say = (
            f"Battery {pct.group(1) if pct else 'unknown'}"
            f"{', ' + state.group(1) if state else ''}."
        )
        log_action("battery", say)
        return {"say": say}

    # --- files ---

    @function_tool()
    async def disk_space(self, context: RunContext) -> dict[str, str]:
        """Report root disk usage."""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        _rc, out, _ = await run_cmd("df", "-h", "/", timeout=10.0)
        line = (out or "").splitlines()[-1] if out else ""
        log_action("disk", line)
        return {"say": f"Disk: {' '.join(line.split())[:150]}."}

    @function_tool()
    async def find_files(self, context: RunContext, query: str) -> dict[str, str]:
        """Search filenames under /home/ripjk.

        Args:
            query: Case-insensitive filename fragment.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        q = query.strip()[:120]
        if not q:
            raise ToolError("Which file? Give me a name to search.")
        if shutil.which("fdfind"):
            cmd = ["fdfind", "-i", q, "/home/ripjk", "--max-results", "10"]
        elif shutil.which("fd"):
            cmd = ["fd", "-i", q, "/home/ripjk", "--max-results", "10"]
        else:
            cmd = [
                "find",
                "/home/ripjk",
                "-maxdepth",
                "4",
                "-iname",
                f"*{q}*",
                "-not",
                "-path",
                "*/.*",
                "-not",
                "-path",
                "*/node_modules/*",
            ]
        _rc, out, _ = await run_cmd(*cmd, timeout=25.0)
        hits = [line for line in (out or "").splitlines() if line.strip()][:10]
        if not hits:
            return {"say": f"No file matching {q[:40]}."}
        log_action("files-find", q)
        return {"paths": "; ".join(hits), "say": f"Found: {'; '.join(hits[:4])[:350]}."}

    @function_tool()
    async def list_dir(self, context: RunContext, path: str = ".") -> dict[str, str]:
        """List a directory (confined to /home/ripjk and /tmp).

        Args:
            path: Directory path, absolute or ~/... .
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        rp = _resolve_user_path(path or ".")
        if rp is None:
            raise ToolError("That path is outside /home/ripjk.")
        if not rp.exists():
            raise ToolError(f"No such folder: {rp}.")
        if rp.is_file():
            return {"say": f"{rp} is a file ({rp.stat().st_size} bytes)."}
        rows = sorted(rp.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))[:40]
        bits = "; ".join((d.name + "/") if d.is_dir() else d.name for d in rows)[:500]
        log_action("files-list", str(rp))
        return {"say": f"{rp.name or '/'} ({len(rows)} shown): {bits or 'empty'}."}

    @function_tool()
    async def read_file(self, context: RunContext, path: str) -> dict[str, str]:
        """Read a text file up to ~4KB (confined to /home/ripjk and /tmp).

        Args:
            path: File path, absolute or ~/... .
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        rp = _resolve_user_path(path)
        if rp is None:
            raise ToolError("That path is outside /home/ripjk.")
        if not rp.exists() or not rp.is_file():
            raise ToolError(f"No such file: {rp}.")
        if rp.stat().st_size > 200_000:
            raise ToolError(f"{rp.name} is too big to read.")
        try:
            txt = rp.read_text(errors="replace")[:4000]
        except Exception:
            raise ToolError(f"{rp.name} is not readable text.") from None
        log_action("files-read", str(rp))
        return {"text": txt, "say": f"{rp.name}: {txt[:1500]}"}

    @function_tool()
    async def write_file(
        self, context: RunContext, path: str, text: str
    ) -> dict[str, str]:
        """Write text to a file (confined to /home/ripjk and /tmp).

        Args:
            path: Destination file path, absolute or ~/... .
            text: Content to write.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        rp = _resolve_user_path(path)
        if rp is None:
            raise ToolError("That path is outside /home/ripjk.")
        try:
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(text)
        except Exception as exc:
            raise ToolError(f"I could not write {rp}: {exc}") from exc
        log_action("files-write", str(rp))
        return {"say": f"Wrote {len(text)} characters to {rp.name}."}

    @function_tool()
    async def move_file(
        self, context: RunContext, source: str, dest: str
    ) -> dict[str, str]:
        """Move/rename a file (confined to /home/ripjk and /tmp).

        Args:
            source: Existing path. dest: New path.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        a = _resolve_user_path(source)
        b = _resolve_user_path(dest)
        if a is None or b is None:
            raise ToolError("Both paths must be inside /home/ripjk.")
        if not a.exists():
            raise ToolError(f"No such file: {a}.")
        try:
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(a), str(b))
        except Exception as exc:
            raise ToolError(f"Move failed: {exc}") from exc
        log_action("files-move", f"{a} -> {b}")
        return {"say": f"Moved {a.name} to {b}."}

    @function_tool()
    async def copy_file(
        self, context: RunContext, source: str, dest: str
    ) -> dict[str, str]:
        """Copy a file (confined to /home/ripjk and /tmp).

        Args:
            source: Existing path. dest: New path.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        a = _resolve_user_path(source)
        b = _resolve_user_path(dest)
        if a is None or b is None:
            raise ToolError("Both paths must be inside /home/ripjk.")
        if not a.exists() or not a.is_file():
            raise ToolError(f"No such file: {a}.")
        try:
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(a), str(b))
        except Exception as exc:
            raise ToolError(f"Copy failed: {exc}") from exc
        log_action("files-copy", f"{a} -> {b}")
        return {"say": f"Copied {a.name} to {b}."}

    @function_tool()
    async def delete_file(self, context: RunContext, path: str) -> dict[str, str]:
        """Delete a file by moving it to the trash (recoverable).

        Args:
            path: File path, absolute or ~/... .
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        rp = _resolve_user_path(path)
        if rp is None:
            raise ToolError("That path is outside /home/ripjk.")
        if not rp.exists():
            raise ToolError(f"No such file: {rp}.")
        try:
            dest = _trash_path(rp)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(rp), str(dest))
        except Exception as exc:
            raise ToolError(f"Delete failed: {exc}") from exc
        log_action("files-delete", f"{rp} -> trash")
        return {"say": f"Moved {rp.name} to the trash."}

    @function_tool()
    async def recent_downloads(self, context: RunContext) -> dict[str, str]:
        """List the newest files in ~/Downloads."""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        _rc, out, _ = await run_cmd("ls", "-lt", "/home/ripjk/Downloads", timeout=10.0)
        rows = [line for line in (out or "").splitlines()[1:6] if line.strip()]
        if not rows:
            return {"say": "Downloads is empty."}
        log_action("files-recent", "downloads")
        return {"say": "Recent in Downloads: " + "; ".join(r[-60:] for r in rows)[:350]}

    # --- todos ---

    @function_tool()
    async def manage_todo(
        self, context: RunContext, action: str, text: str = ""
    ) -> dict[str, str]:
        """Add/list/finish/clear todos. Fuzzy matches on done.

        Args:
            action: One of add, list, done, clear.
            text: Item text (or number for done).
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        action = action.lower()
        text = text.strip()[:200]
        try:
            items = _read_json(TODOS_PATH, [])
        except Exception:
            items = []
        if action == "add":
            if not text:
                raise ToolError("Add what to the list?")
            items.append({"text": text, "done": False, "ts": time.time()})
            _write_json(TODOS_PATH, items[-100:])
            log_action("todo-add", text)
            return {"say": f"Added: {text[:100]}."}
        if action == "list":
            open_items = [i for i in items if not i.get("done")]
            if not open_items:
                return {"say": "List is clear."}
            enumerated = "; ".join(
                f"{n + 1}. {i['text']}" for n, i in enumerate(open_items[-8:])
            )
            return {"say": ("Action queue: " + enumerated)[:500]}
        if action == "done":
            if not text:
                raise ToolError("Mark what done?")
            hit = None
            if text.isdigit():
                idx = int(text) - 1
                open_items = [i for i in items if not i.get("done")]
                if 0 <= idx < len(open_items):
                    hit = open_items[idx]
            else:
                cands = [
                    (i, _todo_match_score(text, str(i.get("text", ""))))
                    for i in items
                    if not i.get("done")
                ]
                if cands:
                    best, score = max(cands, key=lambda pair: pair[1])
                    if score >= 0.42:
                        hit = best
            if not hit:
                raise ToolError(f"Nothing matches {text[:60]}.")
            hit["done"] = True
            _write_json(TODOS_PATH, items[-100:])
            log_action("todo-done", hit["text"])
            return {"say": f"Done: {hit['text'][:100]}."}
        if action in ("clear", "clear-done"):
            items = [i for i in items if not i.get("done")]
            _write_json(TODOS_PATH, items[-100:])
            log_action("todo-clear", "")
            return {"say": "Cleared finished items."}
        raise ToolError(f"Unknown todo action {action}.")

    # --- aliases / voice shortcuts ---

    @function_tool()
    async def remember_alias(
        self, context: RunContext, name: str, command: str
    ) -> dict[str, str]:
        """Save a voice shortcut or remember a user preference/fact.

        Call this whenever the user says "remember that...", "remember
        an alias...", "my favourite X is...", "note that...", or "keep
        in mind...". Never just acknowledge in words: always call this
        tool so it persists. Preferences store as name/value pairs
        (e.g. name "favourite colour", command "blue"); shortcuts store
        as name/command pairs (e.g. name "study mode", command "open
        youtube and play lofi").

        Args:
            name: Shortcut name or preference name (e.g. "study mode").
            command: What it should do, or the remembered value.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        name = name.strip().lower()[:60]
        command = command.strip()[:500]
        if not name or not command:
            raise ToolError("Give me both a name and a command.")
        data = _read_json(SHORTCUTS_PATH, {"shortcuts": {}, "usage": {}})
        data.setdefault("shortcuts", {})[name] = command
        _write_json(
            SHORTCUTS_PATH,
            {"shortcuts": data["shortcuts"], "usage": data.get("usage", {})},
        )
        log_action("alias-save", f"{name}={command}")
        return {"say": f"Remembered: {name} means {command[:120]}."}

    @function_tool()
    async def run_alias(self, context: RunContext, name: str) -> dict[str, str]:
        """Recall a saved voice shortcut or remembered preference.

        Report remembered preferences back in words; the router then
        executes shortcut commands.

        Args:
            name: Shortcut or preference name.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        name = name.strip().lower()
        data = _read_json(SHORTCUTS_PATH, {"shortcuts": {}, "usage": {}})
        command = data.get("shortcuts", {}).get(name)
        if not command:
            raise ToolError(f"No shortcut named {name}.")
        usage = data.get("usage", {})
        usage[name] = usage.get(name, 0) + 1
        _write_json(SHORTCUTS_PATH, {"shortcuts": data["shortcuts"], "usage": usage})
        log_action("alias-run", name)
        return {"command": command, "say": f"Using shortcut: {command[:200]}."}

    @function_tool()
    async def list_aliases(self, context: RunContext) -> dict[str, str]:
        """List saved voice shortcuts."""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        data = _read_json(SHORTCUTS_PATH, {"shortcuts": {}, "usage": {}})
        shortcuts = data.get("shortcuts", {})
        if not shortcuts:
            return {"say": "No voice shortcuts saved yet."}
        bits = "; ".join(f"{k} means {v[:60]}" for k, v in shortcuts.items())[:600]
        return {"say": f"Shortcuts: {bits}."}

    # --- apps / windows ---

    @function_tool()
    async def open_app(self, context: RunContext, app: str) -> dict[str, str]:
        """Open an installed app by .desktop name or executable.

        Args:
            app: e.g. "brave", "code", "konsole", "dolphin".
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        app = app.strip().lower()[:60]
        if not app or len(app) < 2:
            raise ToolError("Which app should I open?")
        known = {
            "brave": "brave-browser",
            "chrome": "google-chrome",
            "code": "code",
            "vscode": "code",
            "konsole": "konsole",
            "terminal": "konsole",
            "dolphin": "dolphin",
            "files": "dolphin",
            "spotify": "spotify",
            "discord": "discord",
        }
        target = known.get(app, app)
        if shutil.which(target) is None and shutil.which(f"org.kde.{target}"):
            target = f"org.kde.{target}"
        try:
            proc = await asyncio.create_subprocess_exec(
                target,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError:
            raise ToolError(f"I could not find the app {app}.") from None
        log_action("launch", target)
        return {"say": f"Opening {app}.", "pid": str(proc.pid or 0)}

    @function_tool()
    async def window_action(
        self, context: RunContext, action: str, query: str = ""
    ) -> dict[str, str]:
        """Focus/minimize/maximize/close a window by title fragment (wmctrl).

        Args:
            action: One of focus, minimize, maximize, close, list.
            query: Title fragment, e.g. "youtube". Empty for list.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        action = action.lower()
        if action == "list":
            _rc, out, _ = await run_cmd("wmctrl", "-l", timeout=5.0)
            if _rc != 0:
                raise ToolError("Window listing needs wmctrl.")
            rows = [line for line in (out or "").splitlines()[:15] if line.strip()]
            return {"say": ("Windows: " + "; ".join(r[-60:] for r in rows))[:500]}
        if not query.strip():
            raise ToolError("Which window? Give me part of its title.")
        q = query.strip()
        if action == "focus":
            rc, _, _ = await run_cmd("wmctrl", "-a", q, timeout=5.0)
        elif action == "minimize":
            rc, _, _ = await run_cmd("wmctrl", "-r", q, "-b", "add,hidden", timeout=5.0)
        elif action == "maximize":
            rc, _, _ = await run_cmd(
                "wmctrl",
                "-r",
                q,
                "-b",
                "add,maximized_vert,maximized_horz",
                timeout=5.0,
            )
        elif action == "close":
            rc, _, _ = await run_cmd("wmctrl", "-c", q, timeout=5.0)
        else:
            raise ToolError(f"Unknown window action {action}.")
        if rc != 0:
            raise ToolError(f"No window matching {q[:60]}.")
        log_action("window", f"{action} {q}")
        return {"say": f"Window {action}: {q[:60]}."}

    # --- power (shutdown/reboot ONLY gate) ---

    @function_tool()
    async def confirm_power_action(self, context: RunContext, action: str) -> str:
        """Authorize one shutdown/reboot after the user explicitly confirms it.

        Call this only after the user clearly confirms the exact action.

        Args:
            action: Either "shutdown" or "reboot".
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        action = action.strip().lower()
        if action not in ("shutdown", "reboot", "restart"):
            raise ToolError("Power confirmation is only for shutdown or reboot.")
        normalized = "reboot" if action == "restart" else action
        self._confirmed_power = normalized
        log_action("power-confirm", normalized)
        return f"The user confirmed {normalized}."

    @function_tool()
    async def power_control(self, context: RunContext, action: str) -> dict[str, str]:
        """Shutdown, reboot, lock, or suspend this laptop.

        Shutdown/reboot REQUIRE a prior confirm_power_action in this session.
        Lock/suspend run immediately.

        Args:
            action: One of shutdown, reboot, lock, suspend.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        action = action.strip().lower()
        if action == "restart":
            action = "reboot"
        if action in ("shutdown", "reboot"):
            if self._confirmed_power != action:
                raise ToolError(
                    f"Shutdown or restart needs your voice confirmation first, "
                    f"sir. Say confirm {action} and I will obey."
                )
            self._confirmed_power = None
            log_action("power", action)
            if action == "shutdown":
                await run_cmd("systemctl", "poweroff", timeout=5.0)
            else:
                await run_cmd("systemctl", "reboot", timeout=5.0)
            return {"say": f"{action.capitalize()} initiated, sir."}
        if action == "lock":
            await run_cmd("loginctl", "lock-session", timeout=5.0)
            log_action("power", "lock")
            return {"say": "Locked."}
        if action == "suspend":
            await run_cmd("systemctl", "suspend", timeout=5.0)
            log_action("power", "suspend")
            return {"say": "Suspending."}
        raise ToolError(f"Unknown power action {action}.")
