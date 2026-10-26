"""Legitimate native exact-window renderer, requiring a fresh pinned-window grant.

The dedicated ELF has its own reviewed KDE desktop entitlement. It captures only
one trusted UUID; no foreground/desktop fallback or Spectacle identity is used.
"""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import uuid
from pathlib import Path

from screen_understanding import (
    MAX_BYTES,
    MAX_PIXELS,
    ForegroundBackend,
    ScreenError,
    _command,
)

MAX_METADATA = 8192


def normalize_target(target):
    if not isinstance(target, (tuple, list)) or len(target) != 3:
        raise ScreenError("An explicitly selected native window is required.")
    window, app, pid = target
    try:
        window = str(uuid.UUID(str(window).strip("{}")))
    except (ValueError, TypeError, AttributeError):
        raise ScreenError("Invalid selected native window identity.") from None
    if (
        uuid.UUID(window).int == 0
        or type(app) is not str
        or not app
        or len(app) > 200
        or any(ord(c) < 32 for c in app)
    ):
        raise ScreenError("Invalid selected native window identity.")
    if isinstance(pid, str) and pid.isdecimal():
        pid = int(pid)
    if type(pid) is not int or not 0 < pid <= 2**32 - 1:
        raise ScreenError("Invalid selected native process identity.")
    return window, app, pid


def helper_path(helper=None):
    value = helper or os.environ.get("JARVIS_WATCH_CAPTURE_HELPER", "")
    if not value:
        from watch_capture_package import installed_helper

        value = installed_helper() or ""
    path = Path(value)
    if (
        not value
        or not path.is_absolute()
        or path.name != "jarvis-watch-capture"
        or path.is_symlink()
    ):
        raise ScreenError(
            "Install and configure the dedicated Jarvis window capture helper."
        )
    try:
        for parent in path.parents:
            metadata = parent.lstat()
            sticky_root_temp = metadata.st_uid == 0 and bool(
                metadata.st_mode & stat.S_ISVTX
            )
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid not in {0, os.getuid()}
                or (metadata.st_mode & 0o022 and not sticky_root_temp)
            ):
                raise ScreenError("Native capture helper directory is not trusted.")
        info = path.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.getuid()}
            or info.st_mode & 0o022
            or not info.st_mode & 0o111
        ):
            raise ScreenError("Native capture helper identity is not trusted.")
        with path.open("rb") as file:
            if file.read(4) != b"\x7fELF":
                raise ScreenError(
                    "Native capture requires the dedicated ELF executable."
                )
    except OSError:
        raise ScreenError("Native capture helper unavailable.") from None
    return str(path.resolve())


def parse_metadata(raw, target):
    if not isinstance(raw, bytes) or len(raw) > MAX_METADATA:
        raise ScreenError("Invalid native window metadata.")
    try:
        data = json.loads(raw)
        selected = normalize_target((data["uuid"], data["app"], data["pid"]))
        if selected != target:
            raise ScreenError("Selected native window changed.")
        width, height = data["width"], data["height"]
        if (
            type(width) is not int
            or type(height) is not int
            or not 0 < width <= 16384
            or not 0 < height <= 16384
            or width * height > MAX_PIXELS
        ):
            raise ScreenError("Invalid native window geometry.")
        title = data.get("caption", "")
        if type(title) is not str:
            raise ScreenError("Invalid native window caption.")
        title = "".join(c for c in title[:300] if ord(c) >= 32)
    except ScreenError:
        raise
    except Exception:
        raise ScreenError("Invalid native window metadata.") from None
    return {
        "id": selected[0],
        "app": selected[1],
        "pid": selected[2],
        "title": title,
        "width": width,
        "height": height,
    }


def inspect_target(target, helper=None, *, runner=subprocess.run):
    """Fresh trusted metadata for disclosure; no screenshots or upload permission."""
    selected = normalize_target(target)
    command = (
        helper_path(helper),
        "--inspect",
        selected[0],
        str(selected[2]),
        selected[1],
    )
    try:
        result = runner(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=1.5,
            check=False,
            start_new_session=True,
        )
        if result.returncode != 0:
            raise ScreenError(
                "Selected window is unavailable, minimized, or native metadata cannot be verified."
            )
        return parse_metadata(result.stdout, selected)
    except ScreenError:
        raise
    except Exception:
        raise ScreenError(
            "Native window metadata timed out or is unavailable."
        ) from None


class NativeWindowBackend(ForegroundBackend):
    def __init__(self, target, *, helper=None, run=_command):
        super().__init__(run=run, workspace_root="/dev/shm")
        self.target = normalize_target(target)
        self.helper = helper_path(helper)

    async def select(self):
        window, app, pid = self.target
        raw = await self.run((self.helper, "--inspect", window, str(pid), app), 1.5)
        data = parse_metadata(raw, self.target)
        return (self.target, (data["width"], data["height"]))

    async def capture(self, selected, check):
        from PIL import Image

        check()
        if not isinstance(selected, tuple) or selected[0] != self.target:
            raise ScreenError("Selected native window changed.")
        window, app, pid = self.target
        # Helper validates UUID/process/class before and after CaptureWindow,
        # checks the returned windowId and never requests arbitrary desktop data.
        raw = await self.run((self.helper, "--capture", window, str(pid), app), 15)
        check()
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
            raise ScreenError("Invalid native selected-window image.")
        try:
            with Image.open(io.BytesIO(raw)) as frame:
                if frame.format != "PNG" or frame.width * frame.height > MAX_PIXELS:
                    raise ScreenError("Invalid native selected-window image.")
                frame.verify()
        except ScreenError:
            raise
        except Exception:
            raise ScreenError("Invalid native selected-window image.") from None
        check()
        return raw
