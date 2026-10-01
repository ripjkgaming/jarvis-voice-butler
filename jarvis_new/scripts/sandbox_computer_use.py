"""Sandboxed computer-use skill test: the agent's real desktop tools, driven
against a nested Xephyr display with a private D-Bus -- never the real
session.

Isolation:
- Display: Xephyr :99 (its own X server; xdotool/XTEST input only reaches it).
- Input: JARVIS_DESKTOP_SANDBOX forces the xdotool backend; this script also
  poisons UInputMouse so any path that would open the global /dev/uinput
  device fails loudly instead of touching the real desktop.
- D-Bus: a private dbus-daemon with a scrubbed env, so single-instance KDE
  apps and auto-started portals stay on :99, never the real desktop.

Usage (from jarvis_new/):
    uv run python scripts/sandbox_computer_use.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

DISPLAY = ":99"
SIZE = "1280x800"


def _poison_uinput() -> None:
    import system.desktop as desktop

    class _Forbidden:
        def __init__(self, *a, **k):
            raise AssertionError("sandbox leak: real /dev/uinput was opened")

    desktop.UInputMouse = _Forbidden  # type: ignore[assignment,misc]


def _xdo(*args: str) -> str:
    from system.desktop import sandbox_env

    return subprocess.run(
        ["xdotool", *args],
        env=sandbox_env(DISPLAY),
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()


def _wait_window(name: str, timeout: float = 20.0) -> str | None:
    end = time.time() + timeout
    while time.time() < end:
        wid = _xdo("search", "--name", f"^{name}$")
        if wid:
            return wid.splitlines()[0]
        time.sleep(0.5)
    return None


async def _ocr(tools, ctx=None) -> str:
    from system import run_cmd

    shot = await tools.desktop_screenshot(ctx)
    big = shot["path"].replace(".png", "-big.png")
    await run_cmd("magick", shot["path"], "-colorspace", "Gray", "-resize", "200%", big)
    _, out, _ = await run_cmd("tesseract", big, "stdout", "--psm", "11", timeout=30.0)
    return out


async def main() -> int:
    for tool in ("Xephyr", "xdotool", "import", "tesseract"):
        if shutil.which(tool) is None:
            print(f"missing dependency: {tool}")
            return 2

    xephyr = subprocess.Popen(
        [
            "Xephyr",
            DISPLAY,
            "-screen",
            SIZE,
            "-ac",
            "-br",
            "-noreset",
            "-title",
            "JARVIS SANDBOX",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.5)
    # Private session bus with a scrubbed env: anything it auto-starts
    # (portals, ksecretd) lands on :99, never the real Wayland session,
    # and has no fd on our stdout. Killed with its whole group at the end.
    os.environ.pop("WAYLAND_DISPLAY", None)
    os.environ.update(DISPLAY=DISPLAY, QT_QPA_PLATFORM="xcb", XDG_SESSION_TYPE="x11")
    bus = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = bus.stdout.readline().strip()
    os.environ["JARVIS_LOCAL"] = "1"
    os.environ["JARVIS_DESKTOP_SANDBOX"] = DISPLAY
    os.environ["JARVIS_HOME"] = str(Path("/tmp/jarvis-sandbox-home"))
    _poison_uinput()
    # Keep test actions out of Sir's real ~/.jarvis/actions.log (the HUD
    # activity feed reads it).
    import system

    system.LOG_PATH = Path("/tmp/jarvis-sandbox-home/actions.log")

    from livekit.agents.llm import ToolError

    from system.core import SystemTools
    from system.desktop import DesktopTools

    system_tools, desk = SystemTools(), DesktopTools()
    results: list[tuple[str, bool, str]] = []

    async def check(name: str, coro) -> object:
        print(f"... {name}", flush=True)
        try:
            value = await asyncio.wait_for(coro, timeout=45)
            results.append((name, True, ""))
            return value
        except AssertionError as exc:
            results.append((name, False, str(exc)))
        except asyncio.TimeoutError:
            results.append((name, False, "timed out after 45s"))
        except Exception as exc:
            results.append((name, False, f"{type(exc).__name__}: {exc}"))
        return None

    try:
        # 1. Eyes: screenshot of the sandbox display at its true size.
        async def screenshot():
            shot = await desk.desktop_screenshot(None)
            assert (shot["width"], shot["height"]) == (1280, 800), shot

        await check("screenshot (sandbox size)", screenshot())

        # 2. Safety gate: acting without confirm must refuse.
        async def gate():
            try:
                await desk.desktop_click(None, 500, 500)
            except ToolError as exc:
                assert "confirm" in str(exc).lower()
                return
            raise AssertionError("click ran without confirm_desktop_action")

        await check("confirm gate blocks unarmed click", gate())

        # 3. Universal launcher inside the sandbox: "calculator" -> KCalc.
        async def launch():
            await system_tools.open_app(None, app="calculator")
            assert _wait_window("KCalc"), "KCalc window never appeared on :99"

        await check("open_app calculator -> KCalc window", launch())

        # 4. Full chain from the specialist prompt: focus, type, Return, read.
        async def calc_chain():
            wid = _wait_window("KCalc", 5)
            assert wid, "no KCalc window"
            geo = _xdo("getwindowgeometry", wid)
            _xdo("windowactivate", "--sync", wid)
            _xdo("windowfocus", "--sync", wid)
            await desk.confirm_desktop_action(None, summary="click KCalc display")
            # Centre of the window in 0-1000 grid coords.
            pos = next(ln for ln in geo.splitlines() if "Position" in ln)
            dim = next(ln for ln in geo.splitlines() if "Geometry" in ln)
            x, y = (int(v) for v in pos.split()[1].split(","))
            w, _h = (int(v) for v in dim.split()[1].split("x"))
            gx, gy = (x + w // 2) * 1000 // 1280, (y + 60) * 1000 // 800
            await desk.desktop_click(None, gx, gy)
            await desk.confirm_desktop_action(None, summary="type 25*4")
            await desk.desktop_type(None, "25*4")
            await desk.confirm_desktop_action(None, summary="press Return")
            await desk.desktop_key(None, "Return")
            await asyncio.sleep(0.8)
            text = await _ocr(desk)
            assert "100" in text, f"expected 100 on screen, OCR saw: {text[:200]!r}"

        await check("click -> type 25*4 -> Return -> OCR reads 100", calc_chain())

        # 5. Grounding: locate a visible label via OCR boxes.
        async def locate():
            hit = await desk.desktop_locate_text(None, label="100")
            assert 0 <= hit["x"] <= 1000 and 0 <= hit["y"] <= 1000, hit

        await check("desktop_locate_text finds '100'", locate())

        # 6. Scroll + a safe key round-trip without errors.
        async def scroll_key():
            await desk.confirm_desktop_action(None, summary="scroll down")
            await desk.desktop_scroll(None, "down", 2)
            await desk.confirm_desktop_action(None, summary="press Escape")
            await desk.desktop_key(None, "Escape")

        await check("scroll + Escape", scroll_key())

        # 7. Blocked key stays blocked.
        async def bad_key():
            await desk.confirm_desktop_action(None, summary="press Super")
            try:
                await desk.desktop_key(None, "Super_L")
            except ToolError:
                return
            raise AssertionError("unsafe key was accepted")

        await check("unsafe key refused", bad_key())
    finally:
        for wid in _xdo("search", "--name", ".").split():
            pid = _xdo("getwindowpid", wid)
            if pid.isdigit():
                subprocess.run(["kill", pid], check=False)
        # Everything the private bus activated shares its session group.
        with __import__("contextlib").suppress(ProcessLookupError):
            os.killpg(bus.pid, 15)
        xephyr.terminate()
        with __import__("contextlib").suppress(Exception):
            xephyr.wait(timeout=5)

    width = max(len(n) for n, _, _ in results)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
    failed = sum(1 for _, ok, _ in results if not ok)
    print(
        f"\n{len(results) - failed}/{len(results)} computer-use skills passed in sandbox"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
