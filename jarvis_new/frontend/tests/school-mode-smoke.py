"""Mocked desktop regression: uv run --no-sync python frontend/tests/school-mode-smoke.py.

Requires a fresh frontend/out build and Playwright's Chromium. Every native IPC and
bridge request is mocked; this cannot resize or operate the real desktop. Optional
--url reuses an existing export server; --screenshots saves review images.
"""

import argparse
import asyncio
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

from playwright.async_api import async_playwright

FRONTEND = Path(__file__).resolve().parents[1]
NORMAL = {"width": 1600, "height": 900}


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class Desktop:
    def __init__(self, page, bar=64, *, cross=False, blocked_move=False):
        self.page = page
        self.bar = bar
        self.mode = "normal"
        self.nonce = ""
        self.muted = False
        self.requests = []
        self.calls = []
        self.errors = []
        self.output_x = NORMAL["width"] if cross else 0
        self.cross = cross
        self.blocked_move = blocked_move
        self.geom = None
        self.moves = []
        self.pending_moves = []

    def geometry(self, nonce):
        rect = {"x": 0, "y": 0, "w": NORMAL["width"], "h": NORMAL["height"]}
        return {
            "nonce": nonce,
            "hud": rect,
            "out": {**rect, "x": self.output_x},
            "primary": rect,
            "same": self.output_x == 0,
            "dir": "left" if self.output_x else None,
            "panel": self.bar,
            "inset": 0,
        }

    async def delayed_move(self, stage, target_x):
        # Native stage dispatch returns before KWin moves the window. Both
        # displays have exactly the same dimensions, so resize cannot tell
        # the frontend whether this deliberately slow hop has completed.
        await asyncio.sleep(0.65)
        painted_early = await self.page.evaluate("""() => {
            const canvas = document.querySelector('.stx-canvas');
            if (!canvas) return false;
            const pixels = canvas.getContext('2d').getImageData(
                0, 0, canvas.width, canvas.height).data;
            for (let i = 3; i < pixels.length; i += 4) if (pixels[i]) return true;
            return false;
        }""")
        self.moves.append({"stage": stage, "painted_before_move": painted_early})
        await asyncio.sleep(0.25)
        if not self.blocked_move:
            self.output_x = target_x
            await self.page.set_viewport_size(NORMAL)

    async def install(self):
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        await self.page.expose_binding("mockNative", self.invoke)
        await self.page.add_init_script("""window.__TAURI_INTERNALS__ = {
            invoke: (cmd, args) => window.mockNative(cmd, args || {})
        };
        window.frameProbe = (() => {
            let raf = 0, last = 0, intervals = [];
            const frame = now => {
                if (last) intervals.push(now - last);
                last = now;
                raf = requestAnimationFrame(frame);
            };
            return {
                start() { cancelAnimationFrame(raf); last = 0; intervals = [];
                    raf = requestAnimationFrame(frame); },
                stop() { cancelAnimationFrame(raf); return intervals; }
            };
        })();""")
        await self.page.route("http://bridge.invalid/**", self.bridge)
        # Do not fall back to a real local bridge if a regression breaks mock IPC.
        await self.page.route("http://127.0.0.1:4317/**", lambda route: route.abort())

    async def invoke(self, _source, command, args):
        self.calls.append((command, args))
        if command == "bridge_info":
            return {"url": "http://bridge.invalid"}
        if command == "mic_status":
            return {"ok": True, "muted": self.muted}
        if command == "set_mic_muted":
            self.muted = args["muted"]
            return self.muted
        if command == "app_config":
            return {}
        if command == "school_menu":
            await self.page.set_viewport_size(
                {"width": 1600, "height": self.bar + args["extra"]}
            )
        if command == "school_stage":
            stage = args["stage"]
            if stage == "measure":
                self.nonce = args["nonce"]
                # Each nonce freezes the actual location at measurement;
                # rereading the old nonce cannot discover a later move.
                self.geom = self.geometry(self.nonce)
            elif stage in ("cover", "primary", "cover-at"):
                target_x = (
                    0
                    if stage == "primary"
                    else args["rect"][0]
                    if stage == "cover-at"
                    else self.output_x
                )
                if self.cross and target_x != self.output_x:
                    self.pending_moves.append(
                        asyncio.create_task(self.delayed_move(stage, target_x))
                    )
                else:
                    self.output_x = target_x
                    await self.page.set_viewport_size(NORMAL)
            elif stage == "dock":
                self.output_x = 0
                await self.page.set_viewport_size({"width": 1600, "height": self.bar})
            elif stage == "restore":
                rect = args.get("rect")
                self.output_x = rect[0] if rect else 0
                await self.page.set_viewport_size(
                    {"width": rect[2], "height": rect[3]} if rect else NORMAL
                )
        return None

    async def bridge(self, route):
        path = urlparse(route.request.url).path
        self.requests.append(path)
        response = {
            "/sys": {
                "ok": True,
                "mode": self.mode,
                "windows": [],
                "load_1_5_15": ["0.6", "0.5", "0.4"],
                "cpu_count": 8,
                "mem_bytes": {"MemTotal": 16000000000, "MemAvailable": 10000000000},
                "net": {"kind": "wifi", "name": "School Wi-Fi", "signal": 80},
                "volume": {"pct": 45, "muted": False},
                "cpu_temp_c": 49,
                "laptop_power": {"battery": 81, "status": "Discharging", "ac": False},
                "launchers": [
                    {"desktop": "org.kde.konsole.desktop", "name": "Terminal"}
                ],
            },
            "/school/geom": {"geom": self.geom},
            "/room": {"ok": True, "room": None, "boot": None},
            "/activity": {"ok": True, "items": []},
            "/actions": {"ok": True, "actions": []},
            "/captions": {"ok": True, "captions": []},
            "/caption/live": {"ok": True, "live": None},
            "/apps": {
                "ok": True,
                "apps": [
                    {
                        "desktop": "org.kde.konsole.desktop",
                        "name": "Terminal",
                        "categories": ["System"],
                    }
                ],
            },
            "/quick": {"ok": True, "volume": {"pct": 45, "muted": False}},
            "/appicon": {"ok": False},
        }.get(path, {"ok": True})
        await route.fulfill(json=response, headers={"Access-Control-Allow-Origin": "*"})

    async def signal(self, phase):
        self.mode = "school" if phase in ("collapse", "arrive") else "normal"
        await self.page.evaluate(
            "phase => window.dispatchEvent(new CustomEvent('jarvis-school', {detail: phase}))",
            phase,
        )

    async def ready(self):
        # The exported HTML already contains .hud before React hydrates.
        # MIC ON appears only after the native mic-status effect answers,
        # so initial signals cannot race the school event subscriptions.
        await self.page.get_by_text("MIC ON", exact=True).wait_for(timeout=10000)
        assert not self.errors, self.errors

    async def settled(self, school):
        await self.page.wait_for_function(
            """school => !document.querySelector('.stx') &&
              !document.documentElement.classList.contains('school-tx') &&
              !!document.querySelector(school ? '.sbar' : '.hud') &&
              !document.querySelector(school ? '.hud' : '.sbar')""",
            arg=school,
            timeout=25000,
        )
        assert not self.errors, self.errors

    def stages(self):
        return [
            args["stage"] for command, args in self.calls if command == "school_stage"
        ]


async def idle_metrics(page):
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Performance.enable")

    async def sample():
        return {
            x["name"]: x["value"]
            for x in (await cdp.send("Performance.getMetrics"))["metrics"]
        }

    start = await sample()
    await page.wait_for_timeout(1500)
    end = await sample()
    await cdp.detach()
    return {
        "elapsed_ms": round(1000 * (end["Timestamp"] - start["Timestamp"])),
        "task_ms": round(1000 * (end["TaskDuration"] - start["TaskDuration"])),
        "script_ms": round(1000 * (end["ScriptDuration"] - start["ScriptDuration"])),
        "layout_count": end["LayoutCount"] - start["LayoutCount"],
        "recalc_style_count": end["RecalcStyleCount"] - start["RecalcStyleCount"],
        "heap_mb": round(end["JSHeapUsedSize"] / 1000000, 1),
        "active_animations": await page.evaluate("document.getAnimations().length"),
    }


async def frame_metrics(page):
    intervals = sorted(await page.evaluate("frameProbe.stop()"))
    if not intervals:
        return {"frames": 0}
    return {
        "frames": len(intervals),
        "median_ms": round(intervals[len(intervals) // 2], 2),
        "p95_ms": round(intervals[int((len(intervals) - 1) * 0.95)], 2),
        "max_ms": round(intervals[-1], 2),
        "gaps_over_34ms": sum(interval > 34 for interval in intervals),
        "estimated_dropped_60hz": sum(
            max(0, round(interval / (1000 / 60)) - 1) for interval in intervals
        ),
    }


async def check_mute_and_visibility(desktop):
    page = desktop.page
    await page.keyboard.press("m")
    await page.locator('.hud[data-muted="true"]').wait_for()
    await page.get_by_text("MIC OFF", exact=True).wait_for()
    await (
        page.get_by_role("region", name="Voice core")
        .get_by_text("MUTED", exact=True)
        .wait_for()
    )
    assert desktop.muted is True
    await page.keyboard.press("m")
    await page.get_by_text("MIC ON", exact=True).wait_for()
    assert await page.locator('.hud[data-muted="true"]').count() == 0
    assert desktop.muted is False

    # Synthetic visibility exercises the same listeners without OS focus changes.
    await page.evaluate("""() => {
        Object.defineProperty(document, 'hidden', {configurable: true, value: true});
        document.dispatchEvent(new Event('visibilitychange'));
    }""")
    await page.wait_for_timeout(100)  # Allow already-in-flight requests to finish.
    count = len(desktop.requests)
    await page.wait_for_timeout(1500)
    assert len(desktop.requests) == count, "Bridge polling continued while hidden"
    assert await page.locator("html.hud-hidden").count() == 1
    await page.evaluate("""() => {
        delete document.hidden;
        document.dispatchEvent(new Event('visibilitychange'));
    }""")
    await page.wait_for_timeout(200)
    assert len(desktop.requests) > count, "Bridge polling did not resume when shown"
    assert await page.locator("html.hud-hidden").count() == 0


async def check_menu_reopen(page, bar):
    await page.get_by_role("button", name="Applications", exact=True).click()
    await page.get_by_role("menu", name="Apps", exact=True).wait_for()
    # Reopen inside the 170ms closing animation; its stale timer must be cancelled.
    await page.evaluate("""async () => {
        document.querySelector('[aria-label="Applications"]').click();
        await new Promise(resolve => setTimeout(resolve, 50));
        document.querySelector('[aria-label="Calendar"]').click();
    }""")
    await page.wait_for_timeout(250)
    await page.get_by_role("menu", name="Calendar", exact=True).wait_for()
    assert page.viewport_size["height"] == bar + 480
    await page.get_by_role("button", name="Calendar", exact=True).click()
    await page.wait_for_function("height => innerHeight === height", arg=bar)


async def roundtrip(browser, url, bar, reduced, shots):
    page = await browser.new_page(
        viewport=NORMAL,
        screen=NORMAL,
        reduced_motion="reduce" if reduced else "no-preference",
    )
    page.set_default_timeout(5000)
    desktop = Desktop(page, bar)
    await desktop.install()
    await page.goto(url, wait_until="domcontentloaded")
    await desktop.settled(False)
    await desktop.ready()
    if not reduced:
        await check_mute_and_visibility(desktop)
    await page.evaluate("frameProbe.start()")
    await desktop.signal("collapse")
    await desktop.settled(True)
    entry_frames = await frame_metrics(page)
    assert page.viewport_size["height"] == bar
    assert "dock" in desktop.stages(), desktop.stages()
    school_idle = await idle_metrics(page) if not reduced else None
    if shots:
        await page.screenshot(path=str(shots / f"school-docked-{bar}.png"))
    if not reduced:
        await check_menu_reopen(page, bar)
    for label, menu in [
        ("Applications", "Apps"),
        ("Quick settings", "Quick settings"),
        ("Calendar", "Calendar"),
    ]:
        await page.get_by_role("button", name=label, exact=True).click()
        await page.wait_for_function(
            """() => !!document.querySelector('[role="menu"]') ||
                !!document.querySelector('.hud')"""
        )
        assert await page.locator(".hud").count() == 0, (
            f"Opening {menu} at {bar + 480}px replaced the school bar with normal HUD"
        )
        await page.get_by_role("menu", name=menu, exact=True).wait_for(state="visible")
        assert page.viewport_size["height"] == bar + 480
        assert await page.locator(".hud").count() == 0, (
            "Opening a school menu replaced the bar with normal HUD"
        )
        if shots:
            await page.wait_for_timeout(300)  # Capture the settled menu entrance.
            await page.screenshot(
                path=str(shots / f"school-{bar}-{menu.replace(' ', '-')}.png")
            )
        if label != "Calendar":
            await page.get_by_role("button", name=label, exact=True).click()
            await page.wait_for_function("height => innerHeight === height", arg=bar)
    # Return with a menu still open: it must sink only the strip and leave no menu.
    await page.evaluate("frameProbe.start()")
    await desktop.signal("return")
    await desktop.settled(False)
    return_frames = await frame_metrics(page)
    assert "restore" in desktop.stages(), desktop.stages()
    assert await page.locator(".smenu").count() == 0
    assert await page.locator(".stx-half").count() == 0
    if shots:
        await page.screenshot(path=str(shots / f"normal-return-{bar}.png"))
    result = {
        "bar": bar,
        "reduced_motion": reduced,
        "stages": desktop.stages(),
        "entry_frames": entry_frames,
        "return_frames": return_frames,
    }
    if not reduced:
        result["school_idle_1_5s"] = school_idle
        result["normal_idle_1_5s"] = await idle_metrics(page)
    await page.close()
    return result


async def reversal(browser, url, late=False):
    page = await browser.new_page(viewport=NORMAL, screen=NORMAL)
    desktop = Desktop(page)
    await desktop.install()
    await page.goto(url, wait_until="domcontentloaded")
    await desktop.ready()
    # Cancel an entry, then replace its return with another entry. The late
    # case reverses during the actual pool reveal, then during the HUD scan.
    await desktop.signal("collapse")
    if late:
        await page.locator('.stx-bar[data-pooling="true"]').wait_for(
            state="attached", timeout=25000
        )
    else:
        await page.wait_for_timeout(180)
    await desktop.signal("return")
    if late:
        await page.wait_for_function(
            "document.documentElement.classList.contains('stx-scan')", timeout=25000
        )
    else:
        await page.wait_for_timeout(100)
    await desktop.signal("collapse")
    await desktop.settled(True)
    count = len(desktop.stages())
    await page.wait_for_timeout(750)
    assert len(desktop.stages()) == count, (
        "Cancelled transition issued a late native stage"
    )
    assert page.viewport_size["height"] == 64
    await desktop.signal("return")
    await desktop.settled(False)
    assert await page.locator(".stx-half").count() == 0
    assert await page.locator(".stx").count() == 0
    result = {"rapid_reversal": "late" if late else "early", "stages": desktop.stages()}
    await page.close()
    return result


async def capture_transitions(browser, url, shots):
    """Separate capture run keeps screenshot overhead out of frame benchmarks."""
    page = await browser.new_page(viewport=NORMAL, screen=NORMAL)
    desktop = Desktop(page)
    await desktop.install()
    await page.goto(url, wait_until="domcontentloaded")
    await desktop.settled(False)
    await desktop.ready()
    await desktop.signal("collapse")
    await page.wait_for_timeout(1900)
    await page.screenshot(path=str(shots / "transition-entry-flow.png"))
    await desktop.settled(True)
    await desktop.signal("return")
    await page.wait_for_function(
        "document.documentElement.classList.contains('stx-scan')", timeout=25000
    )
    await page.screenshot(path=str(shots / "transition-return-blueprint.png"))
    await desktop.settled(False)
    await page.close()


async def cross_monitor_roundtrip(browser, url, blocked=False):
    page = await browser.new_page(viewport=NORMAL, screen=NORMAL)
    desktop = Desktop(page, cross=True, blocked_move=blocked)
    await desktop.install()
    await page.goto(url, wait_until="domcontentloaded")
    await desktop.settled(False)
    await desktop.ready()
    loop = asyncio.get_running_loop()
    started = loop.time()
    await desktop.signal("collapse")
    await desktop.settled(True)
    entry_seconds = loop.time() - started
    assert desktop.output_x == 0, "School strip did not dock on the primary"
    assert entry_seconds < 20, "Entry exceeded the native watchdog budget"
    started = loop.time()
    await desktop.signal("return")
    await desktop.settled(False)
    return_seconds = loop.time() - started
    assert desktop.output_x == NORMAL["width"], "HUD did not return to its source monitor"
    assert return_seconds < 20, "Return exceeded the native watchdog budget"
    await asyncio.gather(*desktop.pending_moves)
    assert [move["stage"] for move in desktop.moves] == ["primary", "cover-at"]
    assert not any(move["painted_before_move"] for move in desktop.moves), (
        "Destination animation began on the old monitor before the native hop",
        desktop.moves,
    )
    assert page.viewport_size == NORMAL
    assert await page.locator(".stx, .stx-half, .sbar").count() == 0
    result = {
        "equal_size_monitor_hop": "blocked_timeout" if blocked else "900ms_delay",
        "entry_seconds": round(entry_seconds, 2),
        "return_seconds": round(return_seconds, 2),
        "school_output_x": 0,
        "restored_output_x": desktop.output_x,
        "moves": desktop.moves,
    }
    await page.close()
    return result


async def run(args, url):
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            results = []
            for blocked in (False, True):
                results.append(await cross_monitor_roundtrip(browser, url, blocked))
                print(json.dumps(results[-1]), flush=True)
            if args.cross_only:
                return
            for bar, reduced in [(64, False), (60, True), (64, True)]:
                results.append(
                    await roundtrip(browser, url, bar, reduced, args.screenshots)
                )
                print(json.dumps(results[-1]), flush=True)
            for late in (False, True):
                results.append(await reversal(browser, url, late))
                print(json.dumps(results[-1]), flush=True)
            if args.screenshots:
                await capture_transitions(browser, url, args.screenshots)
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url")
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--cross-only", action="store_true")
    args = parser.parse_args()
    server = None
    try:
        url = args.url
        if not url:
            if not (FRONTEND / "out/index.html").exists():
                parser.error("Build frontend/out first with pnpm build")
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), partial(QuietHandler, directory=str(FRONTEND / "out"))
            )
            Thread(target=server.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{server.server_port}/"
        asyncio.run(run(args, url))
    finally:
        if server:
            server.shutdown()
            server.server_close()
