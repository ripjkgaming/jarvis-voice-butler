"""Desktop-control skill: screenshot -> coordinates -> inject (Playwright for Linux).

Eyes: spectacle fullscreen capture (+ tesseract TSV word boxes for grounding).
Hands: one kernel uinput device (stdlib ctypes, no daemon, no root:
/dev/uinput is user-writable here) driving an absolute mouse plus a US-layout
keyboard; wtype stays as a text/keys fallback where the compositor allows it.

Coordinate contract (vision-ready): every position is a 0-1000 grid with the
origin at the top-left of the full desktop. Today the agent grounds labels
via desktop_locate_text (deterministic OCR boxes); a future multimodal turn
can supply grid coords straight from looking at the screenshot PNG — the
contract is identical either way.

Safety mirrors the power/pentest tiers: read-only tools run immediately,
every acting tool (click/type/key/scroll) needs a prior
confirm_desktop_action in the session, consumed on use. Typed text is never
logged or spoken (passwords flow through here).
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import os
import re
import struct
import time

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local, run_cmd

GRID_MAX = 1000
TYPE_MAX_CHARS = 500
BUTTONS = ("left", "right", "middle")
SCROLL_DIRECTIONS = ("up", "down", "left", "right")

# Safe key names (no Super/Caps/Power: they flip machine state), mapped to
# evdev codes. Shared by the uinput keyboard and the wtype fallback.
KEY_CODES: dict[str, int] = {
    "Return": 28,
    "Escape": 1,
    "Tab": 15,
    "BackSpace": 14,
    "Delete": 111,
    "Insert": 110,
    "Home": 102,
    "End": 107,
    "Page_Up": 104,
    "Page_Down": 109,
    "Up": 103,
    "Down": 108,
    "Left": 105,
    "Right": 106,
    "space": 57,
    **{f"F{i}": 59 + i - 1 for i in range(1, 11)},
    "F11": 87,
    "F12": 88,
}
DESKTOP_KEYS = frozenset(KEY_CODES)
_KEY_LEFTSHIFT = 42


def _us_char_map() -> dict[str, tuple[int, bool]]:
    """US-layout char -> (evdev code, needs-shift). Pure."""
    out: dict[str, tuple[int, bool]] = {}
    rows = [
        ("1234567890", [2, 3, 4, 5, 6, 7, 8, 9, 10, 11], "!@#$%^&*()"),
        ("qwertyuiop", [16, 17, 18, 19, 20, 21, 22, 23, 24, 25], "QWERTYUIOP"),
        ("asdfghjkl", [30, 31, 32, 33, 34, 35, 36, 37, 38], "ASDFGHJKL"),
        ("zxcvbnm", [44, 45, 46, 47, 48, 49, 50], "ZXCVBNM"),
    ]
    for lower, codes, upper in rows:
        for ch, code, up in zip(lower, codes, upper):
            out[ch] = (code, False)
            out[up] = (code, True)
    for plain, code, shifted in [
        ("-", 12, "_"),
        ("=", 13, "+"),
        ("[", 26, "{"),
        ("]", 27, "}"),
        (";", 39, ":"),
        ("'", 40, '"'),
        ("`", 41, "~"),
        ("\\", 43, "|"),
        (",", 51, "<"),
        (".", 52, ">"),
        ("/", 53, "?"),
    ]:
        out[plain] = (code, False)
        out[shifted] = (code, True)
    out[" "] = (57, False)
    out["\n"] = (28, False)
    out["\t"] = (15, False)
    return out


_CHAR_KEYS = _us_char_map()


def char_to_key(ch: str) -> tuple[int, bool] | None:
    """One char -> (evdev code, needs-shift) under a US layout. Pure."""
    if not ch:
        return None
    return _CHAR_KEYS.get(ch)


class DesktopError(Exception):
    """A user-facing desktop-control failure."""


def clamp1000(value: float) -> int:
    """Clamp a grid coordinate to 0-1000. Pure."""
    return max(0, min(GRID_MAX, round(value)))


def rel_to_abs(x: float, y: float, width: int, height: int) -> tuple[int, int]:
    """Map 0-1000 grid coords onto pixel coords. Pure."""
    px = clamp1000(x) * max(1, width - 1) // GRID_MAX
    py = clamp1000(y) * max(1, height - 1) // GRID_MAX
    return px, py


def parse_tsv_boxes(tsv: str) -> list[dict]:
    """Word-level boxes from `tesseract ... tsv` output. Pure."""
    boxes: list[dict] = []
    lines = (tsv or "").splitlines()
    for line in lines[1:]:  # skip header
        parts = line.split("\t")
        if len(parts) < 12:
            continue
        try:
            level = int(parts[0])
        except ValueError:
            continue
        if level != 5:  # word level only
            continue
        text = parts[11].strip()
        try:
            conf = int(float(parts[10]))
        except ValueError:
            continue
        if not text or conf < 0:
            continue
        try:
            left, top, w, h = (int(float(parts[i])) for i in (6, 7, 8, 9))
        except ValueError:
            continue
        boxes.append(
            {
                "text": text,
                "conf": conf,
                "x0": left,
                "y0": top,
                "x1": left + w,
                "y1": top + h,
            }
        )
    return boxes


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().casefold())


def find_label(boxes: list[dict], label: str, img_w: int, img_h: int) -> dict | None:
    """Center of the first box matching label, in 0-1000 grid coords. Pure."""
    want = _norm(label)
    if not want or img_w <= 0 or img_h <= 0:
        return None
    words = want.split(" ")
    for box in boxes:
        hay = _norm(str(box.get("text", "")))
        if not hay:
            continue
        if want in hay or all(w in hay for w in words):
            cx = (box["x0"] + box["x1"]) * (GRID_MAX // 2) // img_w
            cy = (box["y0"] + box["y1"]) * (GRID_MAX // 2) // img_h
            return {"x": cx, "y": cy, "text": str(box.get("text", ""))}
    return None


def parse_kscreen_outputs(text: str) -> list[dict]:
    """Output geometries from `kscreen-doctor -o` (ANSI-stripped). Pure."""
    ansi = re.compile(r"\x1b\[[0-9;]*m")
    outputs: list[dict] = []
    num = name = None
    for raw in (text or "").splitlines():
        line = ansi.sub("", raw)
        m = re.search(r"Output:\s*(\d*)\s*(\S+)", line)
        if m:
            num = int(m.group(1)) if m.group(1).isdigit() else None
            name = m.group(2)
        g = re.search(r"Geometry:\s*(\d+),(\d+)\s+(\d+)x(\d+)", line)
        if g and name is not None and num is not None:
            x, y, w, h = (int(v) for v in g.groups())
            outputs.append({"num": num, "name": name, "x": x, "y": y, "w": w, "h": h})
            num, name = None, None
    return outputs


def desktop_size_from_outputs(outputs: list[dict]) -> tuple[int, int] | None:
    """Total desktop pixel span covering every output. Pure."""
    if not outputs:
        return None
    try:
        right = max(o["x"] + o["w"] for o in outputs)
        bottom = max(o["y"] + o["h"] for o in outputs)
        left = min(o["x"] for o in outputs)
        top = min(o["y"] for o in outputs)
    except (KeyError, TypeError):
        return None
    if right - left <= 0 or bottom - top <= 0:
        return None
    return right - left, bottom - top


async def _desktop_size() -> tuple[int, int]:
    """Live desktop pixel size via kscreen-doctor. Raises DesktopError."""
    rc, out, err = await run_cmd("kscreen-doctor", "-o", timeout=10.0)
    if rc != 0:
        raise DesktopError(
            f"I can't read the screen layout: {err or 'kscreen-doctor failed'}."
        )
    size = desktop_size_from_outputs(parse_kscreen_outputs(out))
    if size is None:
        raise DesktopError("I can't read the screen layout from kscreen-doctor.")
    return size


# --- uinput mouse (stdlib ctypes; no third-party dep, no daemon) ---

_UINPUT_PATH = "/dev/uinput"
_EV_SYN, _EV_KEY, _EV_REL, _EV_ABS = 0, 1, 2, 3
_SYN_REPORT = 0
_BTN = {"left": 0x110, "right": 0x111, "middle": 0x112}
_REL_WHEEL, _REL_HWHEEL = 8, 6
_ABS_X, _ABS_Y = 0, 1


_UI_SET_EVBIT = (1 << 30) | (4 << 16) | (0x55 << 8) | 100
_UI_SET_KEYBIT = (1 << 30) | (4 << 16) | (0x55 << 8) | 101
_UI_SET_RELBIT = (1 << 30) | (4 << 16) | (0x55 << 8) | 102
_UI_SET_ABSBIT = (1 << 30) | (4 << 16) | (0x55 << 8) | 103
_UI_DEV_SETUP = (1 << 30) | (92 << 16) | (0x55 << 8) | 3
_UI_ABS_SETUP = (1 << 30) | (28 << 16) | (0x55 << 8) | 4
_UI_DEV_CREATE = 0x5501
_UI_DEV_DESTROY = 0x5502


class UInputMouse:
    """Absolute mouse + US keyboard over /dev/uinput. Sync, tiny, closable."""

    def __init__(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise DesktopError("The screen size is invalid.")
        try:
            self._fd = os.open(_UINPUT_PATH, os.O_WRONLY | os.O_NONBLOCK)
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise DesktopError(
                "The virtual mouse is unavailable: /dev/uinput is not "
                f"writable ({exc}). It normally just works for the logged-in "
                "user; otherwise ask the owner to check uinput permissions."
            ) from exc
        try:
            lib = ctypes.CDLL(None, use_errno=True)
            raw = lib.ioctl
            raw.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong]
            raw.restype = ctypes.c_int

            def _io(req: int, num: int, what: str) -> None:
                if raw(self._fd, req, num) != 0:
                    raise OSError(f"{what} failed")

            def _io_buf(req: int, data: bytes, what: str) -> None:
                buf = ctypes.create_string_buffer(data)
                if raw(self._fd, req, ctypes.addressof(buf)) != 0:
                    raise OSError(f"{what} failed")

            for bit in (_EV_SYN, _EV_KEY, _EV_REL, _EV_ABS):
                _io(_UI_SET_EVBIT, bit, "UI_SET_EVBIT")
            key_codes = (
                set(_BTN.values())
                | set(KEY_CODES.values())
                | {code for code, _ in _CHAR_KEYS.values()}
                | {_KEY_LEFTSHIFT}
            )
            for code in sorted(key_codes):
                _io(_UI_SET_KEYBIT, code, "UI_SET_KEYBIT")
            for code in (0, 1, _REL_WHEEL, _REL_HWHEEL):  # REL_X/Y/WHEEL/HWHEEL
                _io(_UI_SET_RELBIT, code, "UI_SET_RELBIT")
            for code in (_ABS_X, _ABS_Y):
                _io(_UI_SET_ABSBIT, code, "UI_SET_ABSBIT")
            self._max_x, self._max_y = width - 1, height - 1
            for code, maximum in ((_ABS_X, width - 1), (_ABS_Y, height - 1)):
                setup = (
                    struct.pack("H", code)
                    + b"\x00\x00"
                    + struct.pack("6i", 0, 0, maximum, 0, 0, 0)
                )
                _io_buf(_UI_ABS_SETUP, setup, "UI_ABS_SETUP")
            dev_setup = struct.pack(
                "4H80sI", 0x03, 0xCAFE, 0xCAFE, 1, b"jarvis-desktop", 0
            )
            _io_buf(_UI_DEV_SETUP, dev_setup, "UI_DEV_SETUP")
            _io(_UI_DEV_CREATE, 0, "UI_DEV_CREATE")
            time.sleep(0.5)
        except OSError as exc:
            with __import__("contextlib").suppress(Exception):
                os.close(self._fd)
            raise DesktopError(f"The virtual mouse failed to start: {exc}.") from exc

    def _emit(self, etype: int, code: int, value: int) -> None:
        os.write(self._fd, struct.pack("llHHi", 0, 0, etype, code, value))

    def _syn(self) -> None:
        self._emit(_EV_SYN, _SYN_REPORT, 0)

    def move(self, x: int, y: int) -> None:
        """Warp to absolute pixel coords."""
        self._emit(_EV_ABS, _ABS_X, max(0, min(self._max_x, x)))
        self._emit(_EV_ABS, _ABS_Y, max(0, min(self._max_y, y)))
        self._syn()

    def click(self, button: str = "left") -> None:
        """Single click at the current position."""
        try:
            code = _BTN[button]
        except KeyError:
            raise DesktopError(
                f"Unknown mouse button {button!r}; use left, right, or middle."
            ) from None
        self._emit(_EV_KEY, code, 1)
        self._syn()
        time.sleep(0.05)
        self._emit(_EV_KEY, code, 0)
        self._syn()

    def scroll(self, dx: int, dy: int) -> None:
        """One wheel notch: dy +1 up / -1 down, dx +1 right / -1 left."""
        if dx:
            self._emit(_EV_REL, _REL_HWHEEL, dx)
        if dy:
            self._emit(_EV_REL, _REL_WHEEL, dy)
        self._syn()

    def tap(self, code: int) -> None:
        """Press+release one evdev key code."""
        self._emit(_EV_KEY, code, 1)
        self._syn()
        time.sleep(0.01)
        self._emit(_EV_KEY, code, 0)
        self._syn()

    def type_text(self, text: str) -> None:
        """Type ASCII text via positional keycodes (US layout)."""
        for ch in text:
            mapped = char_to_key(ch)
            if mapped is None:
                raise DesktopError(
                    "A character in that text is not typable with a US layout."
                )
            code, shift = mapped
            if shift:
                self._emit(_EV_KEY, _KEY_LEFTSHIFT, 1)
                self._syn()
            self.tap(code)
            if shift:
                self._emit(_EV_KEY, _KEY_LEFTSHIFT, 0)
                self._syn()
            time.sleep(0.002)

    def close(self) -> None:
        try:
            lib = ctypes.CDLL(None, use_errno=True)
            raw = lib.ioctl
            raw.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong]
            raw.restype = ctypes.c_int
            raw(self._fd, _UI_DEV_DESTROY, 0)
        except Exception:
            pass
        with contextlib.suppress(Exception):
            os.close(self._fd)


class DesktopTools:
    """Screenshot-grounded desktop control. SystemAgent only (rare ids).

    Register via .tools; add every id to RARE_SYSTEM_TOOL_IDS so the main
    router reaches them only through the transfer_to_system_control handoff.
    """

    def __init__(self) -> None:
        self._confirmed: str | None = None

    @property
    def tools(self) -> list:
        return [
            self.desktop_screenshot,
            self.desktop_locate_text,
            self.confirm_desktop_action,
            self.desktop_click,
            self.desktop_type,
            self.desktop_key,
            self.desktop_scroll,
        ]

    def _need_confirm(self) -> None:
        if not self._confirmed:
            raise ToolError(
                "Desktop control needs confirmation first: call "
                "confirm_desktop_action for this exact action. "
                "Confirmations are single-use."
            )
        self._confirmed = None

    @function_tool()
    async def desktop_screenshot(
        self, context: RunContext, monitor: str | None = None
    ) -> dict[str, object]:
        """Capture the real desktop screen (the eyes of desktop control).

        Returns the PNG path plus pixel size. Positions everywhere else use
        the 0-1000 grid over this full-desktop image (origin top-left), so a
        screenshot always precedes grounded clicks. Read-only: no confirm.

        Args:
            monitor: Optional "1"/"DP-1" to crop one output, or "all".
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        from system.core import SystemTools

        try:
            shot = await SystemTools().take_os_screenshot(context, monitor=monitor)  # type: ignore[arg-type]
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"Screenshot failed: {exc}.") from exc
        path = str(shot.get("path", ""))
        try:
            from PIL import Image

            with Image.open(path) as img:
                width, height = img.size
        except Exception as exc:
            raise ToolError(f"Screenshot unreadable: {exc}.") from exc
        return {
            "path": path,
            "width": width,
            "height": height,
            "grid": "0-1000, origin top-left",
            "say": f"Screen captured ({width}x{height}).",
        }

    @function_tool()
    async def desktop_locate_text(
        self, context: RunContext, label: str
    ) -> dict[str, object]:
        """Ground a visible label ("Send", "Address bar") to 0-1000 coords.

        Fresh screenshot + tesseract word boxes, first case-insensitive
        match wins. Feed the returned x/y straight into desktop_click.
        Read-only: no confirm.

        Args:
            label: Text visible on screen.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        if not (label or "").strip():
            raise ToolError("The label cannot be empty.")
        shot = await self.desktop_screenshot(context)
        path = str(shot["path"])
        width, height = int(shot["width"]), int(shot["height"])
        rc, out, err = await run_cmd("tesseract", path, "stdout", "tsv", timeout=30.0)
        if rc != 0:
            raise ToolError(
                f"I could not read the screen: {(err or 'tesseract failed')[:200]}."
            )
        boxes = parse_tsv_boxes(out)
        hit = find_label(boxes, label, width, height)
        log_action("desktop_locate", f"{label[:60]} -> {hit}")
        if hit is None:
            raise ToolError(
                f"I can't see {label.strip()[:60]!r} on the screen right now."
            )
        return {
            "x": hit["x"],
            "y": hit["y"],
            "matched": hit["text"],
            "boxes": len(boxes),
            "say": f"Found {hit['text']!r} at {hit['x']}, {hit['y']}.",
        }

    @function_tool()
    async def confirm_desktop_action(
        self, context: RunContext, summary: str
    ) -> dict[str, object]:
        """Arm ONE desktop action (click/type/key/scroll). Single-use.

        Args:
            summary: Exact action, e.g. "left-click Send at 812, 440".
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        if not (summary or "").strip():
            raise ToolError("The confirmation summary cannot be empty.")
        self._confirmed = summary.strip()[:200]
        log_action("desktop_confirm", self._confirmed)
        return {
            "confirmed": self._confirmed,
            "say": f"Confirmed: {self._confirmed}. Acting now.",
        }

    @function_tool()
    async def desktop_click(
        self, context: RunContext, x: int, y: int, button: str = "left"
    ) -> dict[str, object]:
        """Left/right/middle click at 0-1000 grid coords. Needs confirm first.

        Ground x/y via desktop_locate_text (or the latest screenshot) —
        never guess coordinates.

        Args:
            x: 0-1000, origin top-left of the full desktop.
            y: 0-1000, origin top-left of the full desktop.
            button: left, right, or middle.
        """
        try:
            require_local()
            if button not in BUTTONS:
                raise ToolError(
                    f"Unknown mouse button {button!r}; use left, right, or middle."
                )
            self._need_confirm()
            width, height = await _desktop_size()
            px, py = rel_to_abs(x, y, width, height)
            mouse = UInputMouse(width, height)
            try:
                await asyncio.to_thread(mouse.move, px, py)
                await asyncio.to_thread(mouse.click, button)
            finally:
                mouse.close()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        except ToolError:
            raise
        except DesktopError as exc:
            raise ToolError(str(exc)) from exc
        log_action("desktop_click", f"{button} {clamp1000(x)},{clamp1000(y)}")
        return {
            "x": clamp1000(x),
            "y": clamp1000(y),
            "button": button,
            "say": f"Clicked {button} at {clamp1000(x)}, {clamp1000(y)}.",
        }

    @function_tool()
    async def desktop_type(self, context: RunContext, text: str) -> dict[str, object]:
        """Type text into the focused window (uinput US keyboard).

        Click the target field first so focus is right. The text itself is
        never logged or spoken. Needs confirm first.

        Args:
            text: 1-500 characters to type.
        """
        try:
            require_local()
            if not (text or "").strip() or len(text) > TYPE_MAX_CHARS:
                raise ToolError(f"Text must be 1-{TYPE_MAX_CHARS} characters.")
            self._need_confirm()
            width, height = await _desktop_size()
            try:
                mouse = UInputMouse(width, height)
            except DesktopError:
                mouse = None
            if mouse is not None:
                try:
                    await asyncio.to_thread(mouse.type_text, text)
                finally:
                    mouse.close()
            else:  # wtype fallback where the compositor allows it.
                rc, _, err = await run_cmd("wtype", "--", text, timeout=10.0)
                if rc != 0:
                    raise ToolError(f"Typing failed: {(err or 'input failed')[:200]}.")
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        except ToolError:
            raise
        except DesktopError as exc:
            raise ToolError(str(exc)) from exc
        log_action("desktop_type", f"{len(text)} chars")
        return {"typed": len(text), "say": f"Typed {len(text)} characters."}

    @function_tool()
    async def desktop_key(self, context: RunContext, key: str) -> dict[str, object]:
        """Press one safe key (Return, Escape, Tab, arrows, F1-F12...).

        Needs confirm first.

        Args:
            key: Key name, e.g. Return, Escape, Tab, Up, F5.
        """
        try:
            require_local()
            if key not in DESKTOP_KEYS:
                raise ToolError(
                    f"Key {key!r} is not allowed. Safe keys: "
                    "Return, Escape, Tab, BackSpace, Delete, arrows, "
                    "Home/End, Page_Up/Page_Down, F1-F12."
                )
            self._need_confirm()
            width, height = await _desktop_size()
            try:
                mouse = UInputMouse(width, height)
            except DesktopError:
                mouse = None
            if mouse is not None:
                try:
                    await asyncio.to_thread(mouse.tap, KEY_CODES[key])
                finally:
                    mouse.close()
            else:  # wtype fallback where the compositor allows it.
                rc, _, err = await run_cmd("wtype", "-k", key, timeout=10.0)
                if rc != 0:
                    raise ToolError(
                        f"Key press failed: {(err or 'input failed')[:200]}."
                    )
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        except ToolError:
            raise
        except DesktopError as exc:
            raise ToolError(str(exc)) from exc
        log_action("desktop_key", key)
        return {"key": key, "say": f"Pressed {key}."}

    @function_tool()
    async def desktop_scroll(
        self, context: RunContext, direction: str, times: int = 1
    ) -> dict[str, object]:
        """Scroll wheel up/down/left/right under the cursor. Needs confirm.

        Args:
            direction: up, down, left, or right.
            times: 1-5 wheel notches.
        """
        try:
            require_local()
            if direction not in SCROLL_DIRECTIONS:
                raise ToolError(
                    f"Unknown direction {direction!r}; use up, down, left, or right."
                )
            count = max(1, min(5, int(times)))
            self._need_confirm()
            width, height = await _desktop_size()
            dx, dy = 0, 0
            if direction == "up":
                dy = 1
            elif direction == "down":
                dy = -1
            elif direction == "right":
                dx = 1
            else:
                dx = -1
            mouse = UInputMouse(width, height)
            try:
                for _ in range(count):
                    await asyncio.to_thread(mouse.scroll, dx, dy)
            finally:
                mouse.close()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        except ToolError:
            raise
        except DesktopError as exc:
            raise ToolError(str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise ToolError(f"Invalid scroll count: {exc}.") from exc
        log_action("desktop_scroll", f"{direction}x{count}")
        return {
            "direction": direction,
            "times": count,
            "say": f"Scrolled {direction} {count} time(s).",
        }
