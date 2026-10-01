"""Sandboxed computer-use skill test: the agent's real desktop tools, driven
against a headless Xvfb display with a private D-Bus -- never the real
session.

Isolation:
- Display: private Xvfb server (no window on the real desktop).
- Input: JARVIS_DESKTOP_SANDBOX forces the xdotool backend; this script also
  poisons UInputMouse so any path that would open the global /dev/uinput
  device fails loudly instead of touching the real desktop.
- D-Bus: a private dbus-daemon with a scrubbed env, so single-instance KDE
  apps and auto-started portals stay on the private display, never the real desktop.

Usage (from jarvis_new/):
    uv run python scripts/sandbox_computer_use.py
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
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


def _wait_window(app_class: str, timeout: float = 20.0) -> str | None:
    end = time.time() + timeout
    while time.time() < end:
        # Qt also creates hidden helper windows with the app's title. Their
        # geometry/focus is unrelated to the visible calculator. Match the
        # mapped application class before sending input to its real window.
        wid = _xdo("search", "--onlyvisible", "--class", f"^{app_class}$")
        if wid:
            return wid.splitlines()[0]
        time.sleep(0.5)
    return None


async def _ocr(tools, ctx=None) -> str:
    from system import run_cmd

    shot = await tools.desktop_screenshot(ctx)
    big = shot["path"].replace(".png", "-big.png")
    await run_cmd("magick", shot["path"], "-colorspace", "Gray", "-resize", "200%", big)
    _, out, _ = await run_cmd("tesseract", big, "stdout", "--psm", "6", timeout=30.0)
    return out


def _stop_process(proc: subprocess.Popen) -> None:
    """Stop only a process group created by this harness, then reap its leader."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=3)
    # The leader can exit while activated children stay alive. Sweep the
    # owned group even when wait() has already reaped its leader.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=3)
    if proc.stdout is not None:
        proc.stdout.close()


@contextlib.contextmanager
def _sandbox_session():
    """Clean up partial setup as well as normal runs; restore caller env."""
    global DISPLAY
    previous_display = DISPLAY
    previous_env = os.environ.copy()
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-desktop-check-") as root:
            home = Path(root)
            for name in ("config", "cache", "data", "runtime", "state", "tmp"):
                (home / name).mkdir(mode=0o700)
            for key in (
                "DISPLAY",
                "JARVIS_DESKTOP_SANDBOX",
                "WAYLAND_DISPLAY",
                "DBUS_SESSION_BUS_ADDRESS",
                "JARVIS_SANDBOX_BUS_ADDRESS",
                "SESSION_MANAGER",
            ):
                os.environ.pop(key, None)
            os.environ.update(
                QT_QPA_PLATFORM="xcb",
                GDK_BACKEND="x11",
                XDG_SESSION_TYPE="x11",
                QT_QPA_PLATFORMTHEME="generic",
                QT_NO_XDG_DESKTOP_PORTAL="1",
                GTK_USE_PORTAL="0",
                XDG_CONFIG_HOME=str(home / "config"),
                XDG_CACHE_HOME=str(home / "cache"),
                XDG_DATA_HOME=str(home / "data"),
                XDG_RUNTIME_DIR=str(home / "runtime"),
                XDG_STATE_HOME=str(home / "state"),
                TMPDIR=str(home / "tmp"),
                JARVIS_LOCAL="1",
                JARVIS_HOME=str(home / "state"),
                JARVIS_ACTIONS_LOG=str(home / "state" / "actions.log"),
            )
            with contextlib.ExitStack() as cleanup:
                display_server = subprocess.Popen(
                    [
                        "Xvfb",
                        "-displayfd",
                        "1",
                        "-screen",
                        "0",
                        f"{SIZE}x24",
                        "-nolisten",
                        "tcp",
                        "-ac",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    start_new_session=True,
                )
                cleanup.callback(_stop_process, display_server)
                # Xvfb allocates and reports its own display atomically. A
                # probe-then-start race could attach two parallel checks to
                # the same display before one failed server finishes exiting.
                if not select.select([display_server.stdout], [], [], 15)[0]:
                    raise RuntimeError("Xvfb startup timed out")
                number = display_server.stdout.readline().strip()
                if not number.isdigit() or not 0 <= int(number) <= 999:
                    raise RuntimeError("Xvfb did not report a usable private display")
                DISPLAY = f":{int(number)}"
                os.environ.update(DISPLAY=DISPLAY, JARVIS_DESKTOP_SANDBOX=DISPLAY)
                geo = subprocess.run(
                    ["xdotool", "getdisplaygeometry"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if (
                    display_server.poll() is not None
                    or geo.returncode != 0
                    or geo.stdout.strip().replace(" ", "x") != SIZE
                ):
                    raise RuntimeError(f"Xvfb {DISPLAY} did not serve {SIZE}")
                bus = subprocess.Popen(
                    ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    start_new_session=True,
                )
                cleanup.callback(_stop_process, bus)
                # readline on a failed daemon used to block setup forever.
                if not select.select([bus.stdout], [], [], 5)[0]:
                    raise RuntimeError("private D-Bus startup timed out")
                address = bus.stdout.readline().strip()
                if not address.startswith("unix:"):
                    raise RuntimeError(
                        "private D-Bus did not provide a session address"
                    )
                os.environ["DBUS_SESSION_BUS_ADDRESS"] = address
                os.environ["JARVIS_SANDBOX_BUS_ADDRESS"] = address
                import system
                import system.desktop as desktop

                cleanup.callback(setattr, system, "LOG_PATH", system.LOG_PATH)
                cleanup.callback(setattr, desktop, "UInputMouse", desktop.UInputMouse)
                system.LOG_PATH = home / "state" / "actions.log"
                _poison_uinput()
                yield
    finally:
        os.environ.clear()
        os.environ.update(previous_env)
        DISPLAY = previous_display


async def main() -> int:
    for tool in (
        "Xvfb",
        "xdotool",
        "import",
        "magick",
        "tesseract",
        "dbus-daemon",
        "kcalc",
    ):
        if shutil.which(tool) is None:
            print(f"missing dependency: {tool}")
            return 2
    try:
        with _sandbox_session():
            print(
                f"Sandbox: headless Xvfb {DISPLAY}, private D-Bus and state", flush=True
            )
            return await _run_checks()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"sandbox setup failed: {exc}")
        return 2


async def _run_checks() -> int:
    from livekit.agents.llm import ToolError

    from system.core import SystemTools
    from system.desktop import DesktopTools

    system_tools, desk = SystemTools(), DesktopTools()
    results: list[tuple[str, bool, str]] = []
    app_pids: set[int] = set()

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
            opened = await system_tools.open_app(None, app="calculator")
            app_pids.add(int(opened["pid"]))
            assert _wait_window("KCalc"), f"KCalc window never appeared on {DISPLAY}"

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
            # KCalc's paint may lag under load; wait for the visible result.
            deadline = time.monotonic() + 15
            text = ""
            while time.monotonic() < deadline:
                text = await _ocr(desk)
                if "100" in text:
                    break
                await asyncio.sleep(0.3)
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
        # Only launch results identify owned processes. Window properties are
        # untrusted and must never choose which user process gets signaled.
        for pid in app_pids:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGTERM)
        tasks = list(system_tools._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0.1)
        for pid in app_pids:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)

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
