"""Grounded desktop adapter for an already authorized computer-use task.

The controller owns user scope and exclusive task ownership. This adapter owns
one fixed display, a reusable input device and private screenshot evidence.
Page text cannot grant authorization. Only the bounded action schema is input;
there is no shell, launcher, URL executor, key chord or ambient wtype fallback.

Existing desktop input primitives and the silent, single-use desktop confirmation
gate are reused. Capture commands write only unique private temporary files.
Raw DesktopTools type/key helpers are deliberately not called: their fallback
may escape a failed sandbox. A desktop target covers
the entire user's desktop; only an explicitly configured private X display is
isolated. Screenshots are read as untrusted data, never instructions.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import math
import os
import signal
import stat
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from PIL import Image

from system import LocalSystemError, desktop, log_action, require_local
from system.computer_focus import ComputerFocusError, FocusProbe
from system.computer_freshness import (
    FreshnessError,
    assess_change,
    protected_region,
    validate_region,
)

MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 32 * 1024 * 1024
CAPTURE_TIMEOUT_S = 15.0
CARET_VERIFY_SECONDS = 0.8
CARET_SAMPLE_SECONDS = 0.12
MAX_INPUT_EVIDENCE_AGE_S = 3.0
_TARGET_ENV = (
    "JARVIS_DESKTOP_SANDBOX",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "JARVIS_SANDBOX_BUS_ADDRESS",
)


class ComputerSurfaceError(Exception):
    """An input or visual-evidence failure safe to show without typed content."""


SurfaceError = ComputerSurfaceError


class SceneChangedError(ComputerSurfaceError):
    """Pre-input pixels changed; re-observe and replan rather than retry input."""


@dataclass(frozen=True)
class Observation:
    """Verified full-desktop PNG; created_at uses monotonic seconds."""

    id: str
    path: str
    width: int
    height: int
    sha256: str
    created_at: float
    target: str
    pixels_sha256: str = ""
    foreground: dict | None = None


@dataclass(frozen=True)
class _Capture:
    data: bytes
    width: int
    height: int
    pixels_sha256: str
    focus: Any = None
    captured_at: float = 0.0


def _integer(value: object, minimum: int, maximum: int, name: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise SurfaceError(f"{name} must be an integer from {minimum} to {maximum}.")
    return value


def validate_action(action: dict) -> dict:
    """Return a strict normalized copy; reject ambiguous or extra operations."""
    if not isinstance(action, dict) or type(action.get("type")) is not str:
        raise SurfaceError("An action needs one supported type.")
    kind = action["type"]
    fields = {
        "click": ({"type", "x", "y", "region"}, {"button"}),
        "move": ({"type", "x", "y", "region"}, set()),
        "type": ({"type", "text", "region"}, set()),
        "press": ({"type", "key", "region"}, set()),
        "scroll": ({"type", "direction", "x", "y", "region"}, {"times"}),
    }
    if kind not in fields:
        raise SurfaceError("Unsupported computer action type.")
    required, optional = fields[kind]
    if not required <= action.keys() or action.keys() - required - optional:
        raise SurfaceError("Action fields do not match the supported schema.")
    normalized = dict(action)
    try:
        normalized["region"] = validate_region(action["region"])
    except FreshnessError as exc:
        raise SurfaceError(str(exc)) from exc
    if kind in {"click", "move", "scroll"}:
        for name in ("x", "y"):
            normalized[name] = _integer(action[name], 0, 1000, name)
    if kind == "click":
        button = action.get("button", "left")
        if type(button) is not str or button not in desktop.BUTTONS:
            raise SurfaceError("Mouse button must be left, right or middle.")
        normalized["button"] = button
    elif kind == "type":
        value = action["text"]
        if (
            type(value) is not str
            or not value.strip()
            or len(value) > desktop.TYPE_MAX_CHARS
            or any(not 32 <= ord(ch) <= 126 for ch in value)
        ):
            raise SurfaceError("Typed text must be 1-500 printable ASCII characters.")
    elif kind == "press":
        key = action["key"]
        if type(key) is not str or key not in desktop.DESKTOP_KEYS:
            raise SurfaceError("Use one of the supported safe key names.")
    elif kind == "scroll":
        direction = action["direction"]
        if type(direction) is not str or direction not in desktop.SCROLL_DIRECTIONS:
            raise SurfaceError("Scroll direction must be up, down, left or right.")
        normalized["times"] = _integer(action.get("times", 1), 1, 5, "times")
    return normalized


def _read_regular_file(path: Path) -> bytes:
    """Read a bounded regular file without following a final symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_IMAGE_BYTES:
            raise SurfaceError("Screenshot evidence is not a bounded regular file.")
        data = file.read(MAX_IMAGE_BYTES + 1)
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise SurfaceError("Screenshot evidence is empty or too large.")
    return data


def _decode_capture(data: bytes) -> _Capture:
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
            raise SurfaceError("Screenshot dimensions exceed the supported bounds.")
        if image.format != "PNG":
            raise SurfaceError("Screenshot evidence must be PNG.")
        image.load()
        pixels = image.convert("RGB").tobytes()
    digest = hashlib.sha256(f"{width}x{height}:".encode())
    digest.update(pixels)
    return _Capture(data, width, height, digest.hexdigest())


class ComputerSurface:
    """One task's visual surface; callers must close it in a finally block.

    A current observation is one-use and every action compares a fresh capture
    before injection. Scene changes conservatively require another observation.
    Cancellation drains an in-flight input call before releasing local ownership.
    The controller must serialize all ComputerSurface instances and other manual
    desktop tools. No application/window containment is claimed on the desktop.
    """

    def __init__(self, workspace: Path, *, max_observation_age_s: float = 90.0) -> None:
        if (
            isinstance(max_observation_age_s, bool)
            or not isinstance(max_observation_age_s, (int, float))
            or not math.isfinite(max_observation_age_s)
            or not 0 < max_observation_age_s <= 120
        ):
            raise SurfaceError("Maximum observation age must be within 0-120 seconds.")
        raw_display = os.environ.get(desktop.SANDBOX_ENV, "").strip()
        self._display = desktop.sandbox_display()
        if raw_display and self._display is None:
            raise SurfaceError("The configured sandbox display is invalid.")
        self._environment = self._target_environment()
        self._capture_environment = (
            desktop.sandbox_env(self._display)
            if self._display is not None
            else dict(os.environ)
        )
        self._workspace = Path(workspace).resolve()
        self._max_age = float(max_observation_age_s)
        self._target = f"sandbox:{self._display}" if self._display else "desktop"
        self._tools = desktop.DesktopTools()
        self._lock = asyncio.Lock()
        self._device: Any = None
        self._device_size: tuple[int, int] | None = None
        self._latest: Observation | None = None
        self._latest_focus: Any = None
        self._focus_probe: FocusProbe | None = None
        self._anchor: dict | None = None
        self._pixels_sha256: str | None = None
        self._owned_paths: set[Path] = set()
        self._closed = False

    @staticmethod
    def _target_environment() -> tuple[str | None, ...]:
        return tuple(os.environ.get(key) for key in _TARGET_ENV)

    @property
    def capabilities(self) -> dict:
        return {
            "target": self._target,
            "isolated": self._display is not None,
            "scope": "entire configured display; no per-window isolation",
            "actions": ["click", "move", "type", "press", "scroll"],
            "coordinates": "0-1000, origin top-left of full desktop",
            "region": "Every action needs the visible control bounds in the same grid; at least 8x8 pixels, at most half the foreground window",
            "keyboard": "Requires our own prior left click; reuse keyboard_anchor.region exactly",
            "keys": sorted(desktop.DESKTOP_KEYS),
            "max_text_chars": desktop.TYPE_MAX_CHARS,
            "text_encoding": "printable ASCII; no control characters",
            "max_observation_age_s": self._max_age,
        }

    @property
    def keyboard_anchor(self) -> dict | None:
        return {"region": dict(self._anchor["region"])} if self._anchor else None

    async def _focus_snapshot(self):
        if self._focus_probe is None:
            self._focus_probe = FocusProbe(
                self._workspace, self._display, self._capture_environment
            )
        try:
            return await self._focus_probe.snapshot()
        except ComputerFocusError as exc:
            raise SurfaceError(str(exc)) from exc

    @staticmethod
    def _same_focus(before, after) -> None:
        if before is None or after is None or before.signature != after.signature:
            raise SurfaceError("The foreground window, focus or geometry changed.")
        age = time.monotonic() - after.observed_at
        if not 0 <= age <= 3:
            raise SurfaceError("Fresh foreground window metadata is unavailable.")

    @staticmethod
    def _frame(focus) -> tuple:
        x, y, w, h = focus.frame
        return x - focus.desktop[0], y - focus.desktop[1], w, h

    @staticmethod
    def _check_overlays(focus, protected) -> None:
        x0, y0, x1, y1 = protected
        for _, x, y, w, h in focus.overlays:
            x, y = x - focus.desktop[0], y - focus.desktop[1]
            if x < x1 and x + w > x0 and y < y1 and y + h > y0:
                raise SurfaceError("Another visible window covers the action region.")

    def _check_target(self) -> None:
        if self._closed:
            raise SurfaceError("This computer surface is closed.")
        try:
            require_local()
        except LocalSystemError as exc:
            raise SurfaceError(str(exc)) from exc
        if self._environment != self._target_environment():
            raise SurfaceError("The desktop target changed; start a new task.")

    @staticmethod
    async def _drain_task(task: asyncio.Task):
        """Finish lifecycle cleanup despite additional cancellation requests."""
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        return task.result()

    async def _run_capture_command(self, argv: tuple[str, ...]) -> int:
        """Capture with a pinned environment; own and reap the subprocess group."""
        self._check_target()
        spawning = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *argv,
                env=self._capture_environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        )
        process = None
        waiting = None
        try:
            try:
                process = await asyncio.shield(spawning)
            except asyncio.CancelledError:
                # Creation can be cancelled after the OS child already exists.
                # Obtain its handle before cleanup instead of orphaning it.
                with contextlib.suppress(Exception):
                    process = await self._drain_task(spawning)
                raise
            waiting = asyncio.create_task(process.wait())
            try:
                return await asyncio.wait_for(
                    asyncio.shield(waiting), timeout=CAPTURE_TIMEOUT_S
                )
            except asyncio.TimeoutError as exc:
                raise SurfaceError("Screenshot capture timed out.") from exc
        finally:
            if process is not None:
                # Signal only the new process group created above. Even a leader
                # that exited can have descendants keeping a screenshot alive.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                if process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                if waiting is None:
                    waiting = asyncio.create_task(process.wait())
                try:
                    await asyncio.shield(waiting)
                except asyncio.CancelledError:
                    await self._drain_task(waiting)
                    raise

    async def _capture(self) -> _Capture:
        self._check_target()
        try:
            focus_before = await self._focus_snapshot()
            self._workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
            commands = (
                [("import", "-window", "root")]
                if self._display is not None
                else [("spectacle", "-b", "-n", "-o"), ("import", "-window", "root")]
            )
            for command in commands:
                live_size = await desktop._desktop_size()
                # A distinct file for each attempt cannot accidentally accept a
                # prior command's partial output or upstream second-based paths.
                fd, name = tempfile.mkstemp(
                    prefix="computer-capture-", suffix=".png", dir=self._workspace
                )
                os.close(fd)
                path = Path(name)
                self._owned_paths.add(path)
                try:
                    try:
                        captured_at = time.monotonic()
                        rc = await self._run_capture_command((*command, str(path)))
                    except FileNotFoundError:
                        continue
                    self._check_target()
                    if rc != 0:
                        continue
                    capture = _decode_capture(_read_regular_file(path))
                    focus_after = await self._focus_snapshot()
                    self._same_focus(focus_before, focus_after)
                    self._check_target()
                    if (
                        capture.width,
                        capture.height,
                    ) != live_size or live_size != focus_after.desktop[2:]:
                        raise SurfaceError(
                            "Screenshot geometry does not match the configured display."
                        )
                    return replace(capture, focus=focus_after, captured_at=captured_at)
                finally:
                    with contextlib.suppress(OSError):
                        path.unlink()
                        self._owned_paths.discard(path)
            raise SurfaceError("Screenshot capture failed on the configured target.")
        except SurfaceError:
            raise
        except Exception as exc:
            raise SurfaceError(
                "Screenshot evidence could not be captured or read."
            ) from exc

    def _publish(self, capture: _Capture) -> Observation:
        self._workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, raw_path = tempfile.mkstemp(
            prefix="computer-observation-", suffix=".png", dir=self._workspace
        )
        path = Path(raw_path)
        self._owned_paths.add(path)
        with os.fdopen(fd, "wb") as file:
            file.write(capture.data)
        observation = Observation(
            id=uuid.uuid4().hex,
            path=str(path),
            width=capture.width,
            height=capture.height,
            sha256=hashlib.sha256(capture.data).hexdigest(),
            created_at=time.monotonic(),
            target=self._target,
            pixels_sha256=capture.pixels_sha256,
            foreground={
                "window_id": capture.focus.window_id,
                "app": capture.focus.app,
                "frame_pixels": self._frame(capture.focus),
            },
        )
        self._latest = observation
        self._pixels_sha256 = capture.pixels_sha256
        self._latest_focus = capture.focus
        return observation

    async def observe(self) -> Observation:
        async with self._lock:
            self._latest = None
            try:
                capture = await self._capture()
                if (
                    self._anchor
                    and self._anchor["signature"] != capture.focus.signature
                ):
                    self._anchor = None
            except BaseException:
                self._anchor = None
                await self._close_device()
                raise
            if self._device_size and self._device_size != (
                capture.width,
                capture.height,
            ):
                await self._close_device()
            return self._publish(capture)

    async def _close_device(self) -> None:
        device, self._device = self._device, None
        self._device_size = None
        if device is not None:
            with contextlib.suppress(Exception):
                await self._settled_thread(device.close)

    @staticmethod
    async def _settled_thread(function, cancelled: threading.Event | None = None):
        """Never release input ownership while an uncancellable thread is active."""
        task = asyncio.create_task(asyncio.to_thread(function))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if cancelled is not None:
                cancelled.set()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            # Retrieve failures without replacing the caller's cancellation.
            with contextlib.suppress(Exception):
                task.result()
            raise

    def _inject(
        self,
        action: dict,
        width: int,
        height: int,
        cancelled: threading.Event,
        captured_at: float,
    ):
        self._check_target()
        if cancelled.is_set():
            return

        def check_age():
            if time.monotonic() - captured_at > MAX_INPUT_EVIDENCE_AGE_S:
                raise FreshnessError(
                    "Screenshot evidence aged before input; observe again."
                )

        check_age()
        device = self._device
        kind = action["type"]
        if kind in {"click", "move"}:
            px, py = desktop.rel_to_abs(action["x"], action["y"], width, height)
            device.move(px, py)
            if kind == "click" and not cancelled.is_set():
                self._check_target()
                check_age()
                device.click(action["button"])
        elif kind == "type":
            device.type_text(action["text"])
        elif kind == "press":
            device.tap(desktop.KEY_CODES[action["key"]])
        elif kind == "scroll":
            device.move(*desktop.rel_to_abs(action["x"], action["y"], width, height))
            dx, dy = {"up": (0, 1), "down": (0, -1), "left": (-1, 0), "right": (1, 0)}[
                action["direction"]
            ]
            for _ in range(action["times"]):
                self._check_target()
                if cancelled.is_set():
                    break
                check_age()
                device.scroll(dx, dy)

    def _prepare_device(self, width, height):
        self._check_target()
        if self._device is None:
            self._device = (
                desktop.XdoInput(self._display)
                if self._display is not None
                else desktop.UInputMouse(width, height)
            )
            self._device_size = (width, height)

    async def _verify_pixels(self, data, capture, protected, frame, keyboard):
        candidate = assess_change(
            data, capture.data, protected, frame, keyboard=keyboard
        )
        if candidate is None:
            return capture
        if not keyboard:
            raise FreshnessError("The protected action target changed; observe again.")

        def crop(data):
            with Image.open(io.BytesIO(data)) as image:
                return image.convert("RGB").crop(candidate).tobytes()

        pattern = crop(capture.data)
        deadline = time.monotonic() + CARET_VERIFY_SECONDS
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            await asyncio.sleep(min(CARET_SAMPLE_SECONDS, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                sample = await asyncio.wait_for(self._capture(), remaining)
            except asyncio.TimeoutError:
                break
            self._same_focus(capture.focus, sample.focus)
            result = assess_change(
                data, sample.data, protected, frame, keyboard=keyboard
            )
            if result is None:
                return sample
            if result != candidate or crop(sample.data) != pattern:
                raise FreshnessError("The possible caret moved or changed pattern.")
        raise FreshnessError(
            "The caret-shaped change did not blink back; observe again."
        )

    async def act(self, action: dict, observation_id: str) -> Observation:
        action = validate_action(action)
        async with self._lock:
            self._check_target()
            previous = self._latest
            previous_focus = self._latest_focus
            if (
                previous is None
                or type(observation_id) is not str
                or observation_id != previous.id
            ):
                raise SurfaceError("A current, unused observation is required.")
            self._latest = None  # Attempts consume evidence, including failed attempts.
            if time.monotonic() - previous.created_at > self._max_age:
                raise SurfaceError("The observation is stale; observe again first.")
            try:
                data = _read_regular_file(Path(previous.path))
                if hashlib.sha256(data).hexdigest() != previous.sha256:
                    raise SurfaceError(
                        "Observation evidence changed; observe again first."
                    )
            except (OSError, ValueError) as exc:
                raise SurfaceError(
                    "Observation evidence is no longer readable."
                ) from exc
            try:
                kind = action["type"]
                keyboard = kind in {"type", "press"}
                target_action = action
                if keyboard:
                    if (
                        not self._anchor
                        or self._anchor["signature"] != previous_focus.signature
                    ):
                        raise SurfaceError(
                            "Keyboard input requires our own verified left-click anchor."
                        )
                    if action["region"] != self._anchor["region"]:
                        raise SurfaceError(
                            "Keyboard region must equal the current click anchor region."
                        )
                    target_action = {
                        "type": "click",
                        "region": action["region"],
                        "x": self._anchor["x"],
                        "y": self._anchor["y"],
                    }
                frame = self._frame(previous_focus)
                protected = protected_region(
                    target_action, previous.width, previous.height, frame
                )
                self._check_overlays(previous_focus, protected)
                capture = await self._capture()
                self._same_focus(previous_focus, capture.focus)
                dimensions = (capture.width, capture.height)
                if dimensions != (previous.width, previous.height):
                    await self._close_device()
                    raise SurfaceError(
                        "The display geometry changed; observe again first."
                    )
                capture = await self._verify_pixels(
                    data, capture, protected, frame, keyboard
                )
                # Device initialization can settle slowly. Refresh after creating
                # it, so that startup delay cannot weaken the pixel check.
                if self._device is None:
                    await self._settled_thread(
                        lambda: self._prepare_device(*dimensions)
                    )
                    capture = await self._capture()
                    self._same_focus(previous_focus, capture.focus)
                    capture = await self._verify_pixels(
                        data, capture, protected, frame, keyboard
                    )
                # The task was authorized by the caller. Arm exactly this action;
                # never include typed text or page-derived prose in its log.
                summary = f"computer task {kind}"
                if kind in {"click", "move"}:
                    summary += f" at {action['x']},{action['y']}"
                    if kind == "click":
                        summary += f" button {action['button']}"
                elif kind == "type":
                    summary += f" {len(action['text'])} characters"
                elif kind == "press":
                    summary += f" {action['key']}"
                elif kind == "scroll":
                    summary += f" {action['direction']} {action['times']} notches"
                await self._tools.confirm_desktop_action(None, summary=summary)
                self._tools._need_confirm()
                focus = await self._focus_snapshot()
                self._same_focus(capture.focus, focus)
                self._check_overlays(focus, protected)
                if time.monotonic() - capture.captured_at > MAX_INPUT_EVIDENCE_AGE_S:
                    raise FreshnessError(
                        "Screenshot evidence aged before input; observe again."
                    )
                cancelled = threading.Event()
                await self._settled_thread(
                    lambda: self._inject(
                        action, *dimensions, cancelled, capture.captured_at
                    ),
                    cancelled,
                )
                log_action("computer_input", summary)
                result = await self._capture()
                if (
                    kind == "click"
                    and action["button"] == "left"
                    and result.focus.signature == focus.signature
                ):
                    self._anchor = {
                        "region": dict(action["region"]),
                        "x": action["x"],
                        "y": action["y"],
                        "signature": focus.signature,
                    }
                else:
                    self._anchor = None
                return self._publish(result)
            except FreshnessError as exc:
                self._anchor = None
                raise SceneChangedError(str(exc)) from exc
            except asyncio.CancelledError:
                self._anchor = None
                await self._close_device()
                raise
            except SceneChangedError:
                raise
            except SurfaceError:
                self._anchor = None
                await self._close_device()
                raise
            except Exception as exc:
                self._anchor = None
                await self._close_device()
                raise SurfaceError(
                    "Computer input failed; completion is unverified."
                ) from exc

    async def close(self) -> None:
        """Close only this adapter's device and remove only its owned copies."""
        async with self._lock:
            self._closed = True
            self._latest = None
            self._anchor = None
            try:
                await self._close_device()
            finally:
                try:
                    if self._focus_probe is not None:
                        await self._focus_probe.close()
                finally:
                    for path in self._owned_paths:
                        with contextlib.suppress(OSError):
                            path.unlink()
                    self._owned_paths.clear()
