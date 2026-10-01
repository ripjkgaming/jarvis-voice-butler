"""Fresh foreground identity and geometry, without Jarvis's shared watcher.

KDE uses a private DBus connection and an owned, read-only one-shot KWin script.
X11 is allowed only on an explicitly pinned sandbox display. No helper activates
windows, registers shortcuts, starts the global scripting service or guesses
missing focus. Callers bracket screenshot capture with snapshots and compare
again immediately before input; foreground identity alone is not visual proof.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from jeepney import (
    DBusAddress,
    MatchRule,
    MessageType,
    new_method_call,
    new_method_return,
)
from jeepney.bus import get_bus
from jeepney.io.blocking import DBusConnection, prep_socket

PROBE_TIMEOUT_S = 2.0
CLEANUP_TIMEOUT_S = 0.4
_PATH = "/org/jarvis/ComputerFocus"
_INTERFACE = "org.jarvis.ComputerFocus"
_KW_INTERFACE = "org.kde.kwin.Scripting"
_SUPPORTED_XDOTOOL_VERSION = "xdotool version 3.20211022.1"


class ComputerFocusError(Exception):
    """Safe failure message; window titles and input text are never included."""


@dataclass(frozen=True)
class FocusSnapshot:
    window_id: str
    app: str
    pid: int
    focus_id: str
    frame: tuple[int, int, int, int]
    desktop: tuple[int, int, int, int]
    overlays: tuple[tuple[str, int, int, int, int], ...]
    observed_at: float

    @property
    def identity(self) -> tuple[str, str, int]:
        return self.window_id, self.app, self.pid

    @property
    def signature(self) -> tuple:
        return self.identity, self.focus_id, self.frame, self.desktop, self.overlays


def _text(value: object, maximum: int = 200) -> str:
    if (
        type(value) is not str
        or not value.strip()
        or len(value) > maximum
        or any(ord(ch) < 32 for ch in value)
    ):
        raise ComputerFocusError("Foreground window metadata is missing or invalid.")
    return value


def _rect(value: object) -> tuple[int, int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ComputerFocusError("Foreground geometry is unavailable.")
    if any(
        type(n) not in (int, float) or not math.isfinite(n) or int(n) != n
        for n in value
    ):
        raise ComputerFocusError(
            "Fractional or invalid window geometry is unsupported."
        )
    x, y, width, height = (int(n) for n in value)
    if (
        abs(x) > 100_000
        or abs(y) > 100_000
        or not 0 < width <= 32_768
        or not 0 < height <= 32_768
    ):
        raise ComputerFocusError("Foreground geometry is outside supported bounds.")
    return x, y, width, height


def parse_snapshot(raw: object, nonce: str) -> FocusSnapshot:
    if type(raw) is not str or len(raw) > 64_000:
        raise ComputerFocusError("Foreground snapshot is malformed.")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ComputerFocusError("Foreground snapshot is malformed.") from exc
    if not isinstance(data, dict) or data.get("nonce") != nonce:
        raise ComputerFocusError("Foreground snapshot does not match this request.")
    if (
        data.get("active") is not True
        or data.get("minimized") is not False
        or data.get("deleted") is not False
    ):
        raise ComputerFocusError(
            "No unambiguous visible foreground window is available."
        )
    wid, focused = _text(data.get("window_id")), _text(data.get("focus_id"))
    if wid != focused:
        raise ComputerFocusError("Foreground identity and focus disagree.")
    pid = data.get("pid")
    if type(pid) is not int or not 0 < pid <= 2**31 - 1:
        raise ComputerFocusError("Foreground process identity is unavailable.")
    overlays = data.get("overlays")
    if not isinstance(overlays, list) or len(overlays) > 128:
        raise ComputerFocusError("Foreground occlusion information is unavailable.")
    parsed_overlays = []
    for item in overlays:
        if not isinstance(item, list) or len(item) != 5:
            raise ComputerFocusError("Foreground occlusion information is invalid.")
        parsed_overlays.append((_text(item[0]), *_rect(item[1:])))
    if len({item[0] for item in parsed_overlays}) != len(parsed_overlays):
        raise ComputerFocusError("Foreground occlusion information is ambiguous.")
    return FocusSnapshot(
        wid,
        _text(data.get("app")),
        pid,
        focused,
        _rect(data.get("frame")),
        _rect(data.get("desktop")),
        tuple(parsed_overlays),
        time.monotonic(),
    )


def snapshot_script(destination: str, nonce: str) -> str:
    """Only constants are interpolated; all compositor properties are read-only."""
    return f"""const nonce = {json.dumps(nonce)};
function rect(g) {{ return [g.x, g.y, g.width, g.height]; }}
let result = {{nonce: nonce, error: true}};
try {{
    const w = workspace.activeWindow;
    if (!w || !w.active || w.minimized || w.deleted) throw new Error('no foreground');
    const windows = workspace.stackingOrder;
    const at = windows.indexOf(w);
    if (at < 0) throw new Error('unknown stacking');
    const overlays = [];
    for (let i = at + 1; i < windows.length; i++) {{
        const other = windows[i];
        if (other.minimized || other.deleted || other.hidden) continue;
        if (other.desktops.length && other.desktops.indexOf(workspace.currentDesktop) < 0) continue;
        if (other.activities.length && other.activities.indexOf(workspace.currentActivity) < 0) continue;
        overlays.push([String(other.internalId)].concat(rect(other.frameGeometry)));
    }}
    result = {{nonce: nonce, window_id: String(w.internalId),
        focus_id: String(workspace.activeWindow.internalId),
        app: String(w.resourceClass), pid: Number(w.pid), active: !!w.active,
        minimized: !!w.minimized, deleted: !!w.deleted,
        frame: rect(w.frameGeometry), desktop: rect(workspace.virtualScreenGeometry),
        overlays: overlays}};
}} catch (e) {{}}
callDBus({json.dumps(destination)}, "{_PATH}", "{_INTERFACE}",
         "Snapshot", JSON.stringify(result));
"""


def open_dbus_connection(*, bus: str, auth_timeout: float):
    """Bound both authentication and Jeepney's initial Hello request.

    Jeepney's public opener bounds socket authentication but its constructor's
    Hello call otherwise waits indefinitely. This subclass supplies a default
    request deadline, using the same installed connection/socket implementation.
    """

    class BoundedConnection(DBusConnection):
        def send_and_get_reply(self, message, *, timeout=None):
            return super().send_and_get_reply(
                message, timeout=0.5 if timeout is None else min(timeout, 2.0)
            )

    socket = prep_socket(get_bus(bus), False, timeout=auth_timeout)
    try:
        return BoundedConnection(socket)
    except BaseException:
        socket.close()
        raise


class FocusProbe:
    def __init__(self, workspace: Path, display: str | None, environment: dict) -> None:
        if display is not None and (
            type(display) is not str or not re.fullmatch(r":\d{1,3}", display)
        ):
            raise ComputerFocusError("The sandbox display is invalid.")
        configured = str(environment.get("JARVIS_DESKTOP_SANDBOX", "")).strip()
        if configured and configured != display:
            raise ComputerFocusError("The foreground probe target is inconsistent.")
        self._workspace = Path(workspace).resolve()
        self._display = display
        self._environment = dict(environment)
        if display is not None:
            self._environment["DISPLAY"] = display
            self._environment.pop("WAYLAND_DISPLAY", None)
            self._environment.pop("SESSION_MANAGER", None)
            self._environment["DBUS_SESSION_BUS_ADDRESS"] = (
                environment.get("JARVIS_SANDBOX_BUS_ADDRESS")
                or "unix:path=/nonexistent/jarvis-sandbox-session-bus"
            )
        self._connection = None
        self._x11_verified_executable: str | None = None
        self._owned: dict[str, Path] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ComputerFocusError("Foreground snapshot timed out.")
        return min(2.0, remaining)

    @staticmethod
    async def _thread(function):
        task = asyncio.create_task(asyncio.to_thread(function))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            with contextlib.suppress(Exception):
                task.result()
            raise

    async def snapshot(self) -> FocusSnapshot:
        async with self._lock:
            if self._closed:
                raise ComputerFocusError("This foreground probe is closed.")
            return await self._thread(self._snapshot_sync)

    def _snapshot_sync(self) -> FocusSnapshot:
        try:
            deadline = time.monotonic() + PROBE_TIMEOUT_S
            return (
                self._snapshot_x11(deadline)
                if self._display
                else self._snapshot_kwin(deadline)
            )
        except ComputerFocusError:
            raise
        except Exception as exc:
            raise ComputerFocusError(
                "The foreground window could not be verified."
            ) from exc

    def _call(self, path, interface, member, signature="", body=(), *, timeout):
        reply = self._connection.send_and_get_reply(
            new_method_call(
                DBusAddress(path, "org.kde.KWin", interface), member, signature, body
            ),
            timeout=timeout,
        )
        if reply.header.message_type != MessageType.method_return:
            raise ComputerFocusError("KWin rejected the foreground probe.")
        return reply.body

    def _snapshot_kwin(self, deadline: float) -> FocusSnapshot:
        if self._connection is None:
            address = self._environment.get("DBUS_SESSION_BUS_ADDRESS", "")
            if not isinstance(address, str) or not address.startswith("unix:"):
                raise ComputerFocusError("A local compositor bus is unavailable.")
            self._connection = open_dbus_connection(
                bus=address, auth_timeout=min(1.0, self._remaining(deadline))
            )
        connection = self._connection
        owner_reply = connection.send_and_get_reply(
            new_method_call(
                DBusAddress(
                    "/org/freedesktop/DBus",
                    "org.freedesktop.DBus",
                    "org.freedesktop.DBus",
                ),
                "GetNameOwner",
                "s",
                ("org.kde.KWin",),
            ),
            timeout=self._remaining(deadline),
        )
        if (
            owner_reply.header.message_type != MessageType.method_return
            or len(owner_reply.body) != 1
        ):
            raise ComputerFocusError("KWin is unavailable on the configured bus.")
        sender = _text(owner_reply.body[0])
        if not sender.startswith(":"):
            raise ComputerFocusError(
                "The compositor sender could not be authenticated."
            )
        nonce = uuid.uuid4().hex
        plugin = f"jarvis-computer-focus-{nonce}"
        self._workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(
            prefix="computer-focus-", suffix=".js", dir=self._workspace
        )
        path = Path(name)
        self._owned[plugin] = path
        try:
            with os.fdopen(fd, "w") as file:
                file.write(snapshot_script(connection.unique_name, nonce))
            rule = MatchRule(
                type="method_call",
                sender=sender,
                destination=connection.unique_name,
                path=_PATH,
                interface=_INTERFACE,
                member="Snapshot",
            )
            with connection.filter(rule) as messages:
                result = self._call(
                    "/Scripting",
                    _KW_INTERFACE,
                    "loadScript",
                    "ss",
                    (str(path), plugin),
                    timeout=self._remaining(deadline),
                )
                if len(result) != 1 or type(result[0]) is not int or result[0] < 0:
                    raise ComputerFocusError("KWin did not load the foreground probe.")
                self._call(
                    f"/Scripting/Script{result[0]}",
                    "org.kde.kwin.Script",
                    "run",
                    timeout=self._remaining(deadline),
                )
                message = connection.recv_until_filtered(
                    messages, timeout=self._remaining(deadline)
                )
                # Filter applies even when the callback arrived before run replied.
                if not rule.matches(message) or len(message.body) != 1:
                    raise ComputerFocusError(
                        "The foreground response could not be authenticated."
                    )
                connection.send(new_method_return(message))
                return parse_snapshot(message.body[0], nonce)
        finally:
            if not self._cleanup_script(plugin):
                raise ComputerFocusError(
                    "The foreground probe cleanup did not complete."
                )

    def _cleanup_script(self, plugin: str) -> bool:
        path = self._owned.get(plugin)
        if path is None:
            return True
        unloaded = False
        try:
            self._call(
                "/Scripting",
                _KW_INTERFACE,
                "unloadScript",
                "s",
                (plugin,),
                timeout=CLEANUP_TIMEOUT_S,
            )
            unloaded = True
        except Exception:
            pass  # Retain ownership so close() retries this exact plugin only.
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        if unloaded:
            self._owned.pop(plugin, None)
        return unloaded

    def _snapshot_x11(self, deadline: float) -> FocusSnapshot:
        candidate = shutil.which(
            "xdotool", path=self._environment.get("PATH", os.defpath)
        )
        if (
            not candidate
            or Path(candidate).name != "xdotool"
            or not Path(candidate).is_absolute()
            or Path(candidate).resolve().name != "xdotool"
        ):
            raise ComputerFocusError("The sandbox focus tool is unavailable.")

        def query(*args, optional=False):
            completed = subprocess.run(
                [candidate, *args],
                env=self._environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self._remaining(deadline),
            )
            if completed.returncode != 0:
                if optional:
                    return ""
                raise ComputerFocusError("The sandbox foreground query failed.")
            if len(completed.stdout) > 64_000:
                raise ComputerFocusError(
                    "The sandbox foreground response is too large."
                )
            return completed.stdout.strip()

        if self._x11_verified_executable != candidate:
            # Window-stack ordering below is verified against this exact source
            # version. Refuse other versions before any window metadata query.
            if query("--version") != _SUPPORTED_XDOTOOL_VERSION:
                raise ComputerFocusError(
                    "The sandbox needs supported xdotool version 3.20211022.1."
                )
            self._x11_verified_executable = candidate

        def xid(value):
            if not re.fullmatch(r"[1-9]\d{0,19}", value) or int(value) <= 1:
                raise ComputerFocusError(
                    "The sandbox focus is ambiguous or unavailable."
                )
            return value

        def geometry(wid):
            raw = query("getwindowgeometry", "--shell", wid)
            data = {}
            for line in raw.splitlines():
                pair = line.split("=", 1)
                if (
                    len(pair) != 2
                    or pair[0] in data
                    or not re.fullmatch(r"-?\d+", pair[1])
                ):
                    raise ComputerFocusError(
                        "The sandbox window geometry is malformed."
                    )
                data[pair[0]] = int(pair[1])
            if str(data.get("WINDOW")) != wid or data.get("SCREEN") != 0:
                raise ComputerFocusError("The sandbox window geometry is ambiguous.")
            return _rect([data.get(key) for key in ("X", "Y", "WIDTH", "HEIGHT")])

        focus_id = xid(query("getwindowfocus", "-f"))
        window_id = xid(query("getwindowfocus"))
        active = query("getactivewindow", optional=True)
        if active and xid(active) != window_id:
            raise ComputerFocusError(
                "The sandbox active window and keyboard focus disagree."
            )
        frame = geometry(window_id)
        pid_raw = query("getwindowpid", window_id)
        if not re.fullmatch(r"[1-9]\d{0,9}", pid_raw) or int(pid_raw) > 2**31 - 1:
            raise ComputerFocusError("The sandbox foreground process is unavailable.")
        app = _text(query("getwindowclassname", window_id))
        size = query("getdisplaygeometry").split()
        if len(size) != 2 or not all(
            re.fullmatch(r"[1-9]\d{0,4}", value) for value in size
        ):
            raise ComputerFocusError("The sandbox display geometry is unavailable.")
        desktop = _rect([0, 0, int(size[0]), int(size[1])])
        visible = query(
            "search",
            "--screen",
            "0",
            "--onlyvisible",
            "--maxdepth",
            "1",
            "--name",
            ".*",
        ).splitlines()
        if (
            not visible
            or len(visible) > 128
            or len(visible) != len(set(visible))
            or window_id not in visible
        ):
            raise ComputerFocusError("The sandbox visible window list is ambiguous.")
        # xdotool 3.20211022.1 xdo_search.c returns root first, then its children
        # in XQueryTree order (bottom to top). Restricting depth and screen makes
        # entries after this root-child target genuine potential occluders, not
        # ordinary windows behind it. Unnamed popups are included by '.*'. A
        # reparenting WM hides the client below depth 1, so fail closed above.
        # Verified source: github.com/jordansissel/xdotool/tree/v3.20211022.1
        overlays = tuple(
            (xid(wid), *geometry(wid))
            for wid in visible[visible.index(window_id) + 1 :]
        )
        if (
            window_id not in visible
            or xid(query("getwindowfocus", "-f")) != focus_id
            or xid(query("getwindowfocus")) != window_id
            or geometry(window_id) != frame
        ):
            raise ComputerFocusError(
                "The sandbox foreground changed during verification."
            )
        return FocusSnapshot(
            window_id,
            app,
            int(pid_raw),
            focus_id,
            frame,
            desktop,
            overlays,
            time.monotonic(),
        )

    def _close_sync(self):
        for plugin in tuple(self._owned):
            self._cleanup_script(plugin)
        if self._connection is not None:
            with contextlib.suppress(Exception):
                self._connection.close()
            self._connection = None

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            await self._thread(self._close_sync)
