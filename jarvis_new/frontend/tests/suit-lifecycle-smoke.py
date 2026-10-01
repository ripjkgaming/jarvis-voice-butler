"""Independent suit lifecycle checks on a production export, entirely mocked."""

import argparse
import asyncio
import importlib.util
import json
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.async_api import async_playwright

SPEC = importlib.util.spec_from_file_location(
    "suit_smoke", Path(__file__).with_name("suit-diagnostics-smoke.py")
)
SUIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUIT)

CONTEXT_PROBE = """(() => {
 const original = HTMLCanvasElement.prototype.getContext;
 window.suitContextRequests = 0;
 HTMLCanvasElement.prototype.getContext = function(kind, ...args) {
   if (kind === 'webgl' || kind === 'webgl2') {
     window.suitContextRequests++;
     if (window.qaDisableWebGL) return null;
   }
   return original.call(this, kind, ...args);
 };
})();"""


async def setup(browser, url, *, no_webgl=False):
    context = await browser.new_context(
        viewport=SUIT.SCHOOL.NORMAL, screen=SUIT.SCHOOL.NORMAL, device_scale_factor=2
    )
    await context.route(
        "**/*",
        lambda route: (
            route.continue_()
            if route.request.url.startswith(url.rstrip("/") + "/")
            else route.abort()
        ),
    )
    page = await context.new_page()
    desktop = SUIT.Desktop(page)
    desktop.command = {
        "op": "open",
        "revision": 1,
        "issued_at": 1,
        "session": "old-boot",
    }
    await desktop.install()
    await page.add_init_script(SUIT.PHYSICAL_SCREEN)
    await page.add_init_script(CONTEXT_PROBE)
    await page.add_init_script(SUIT.PROBE)
    if no_webgl:
        await page.add_init_script("window.qaDisableWebGL = true")
    await page.goto(url, wait_until="domcontentloaded")
    await desktop.ready()
    await page.wait_for_timeout(300)
    assert await page.locator(SUIT.PANEL).count() == 0, "Old command replayed on boot"
    assert await page.evaluate("suitContextRequests") == 0, (
        "Closed boot allocated WebGL"
    )
    return context, page, desktop


async def stable_probe(page):
    await page.wait_for_timeout(150)
    before = await page.evaluate("suitProbe()")
    await page.wait_for_timeout(250)
    after = await page.evaluate("suitProbe()")
    assert after == before, {"unexpected_render_work": [before, after]}
    assert after["pending"] == 0, after
    return before


async def lifecycle(browser, url):
    context, page, desktop = await setup(browser, url)
    try:
        await desktop.suit("open")
        await SUIT.ready_scene(page)
        scene = page.locator("[data-suit-renderer]")
        quality = await scene.locator("canvas").evaluate("""canvas => {
            const r = canvas.getBoundingClientRect();
            return {width: canvas.width, height: canvas.height,
                cssWidth: r.width, cssHeight: r.height, dpr: devicePixelRatio};
        }""")
        assert abs(quality["width"] - quality["cssWidth"] * 2) <= 2, quality
        assert abs(quality["height"] - quality["cssHeight"] * 2) <= 2, quality
        await stable_probe(page)
        await page.evaluate("""() => {
            Object.defineProperty(document, 'hidden', {configurable:true, value:true});
            document.dispatchEvent(new Event('visibilitychange'));
        }""")
        hidden = await stable_probe(page)
        await page.get_by_role("button", name="Critical", exact=True).evaluate(
            "n => n.click()"
        )
        assert await stable_probe(page) == hidden
        await page.evaluate("""() => {
            delete document.hidden; document.dispatchEvent(new Event('visibilitychange'));
        }""")
        await page.wait_for_function(
            "n => suitProbe().canvases.some(c => c.connected && c.renders > n)",
            arg=max(c["renders"] for c in hidden["canvases"]),
        )
        await scene.evaluate("node => node.style.transform='translateX(100000px)'")
        offscreen = await stable_probe(page)
        await page.get_by_role("button", name="Nominal", exact=True).evaluate(
            "n => n.click()"
        )
        assert await stable_probe(page) == offscreen
        await scene.evaluate("node => node.style.removeProperty('transform')")
        # Intersection/media changes legitimately request a fresh frame. Observe
        # those asynchronous events before measuring the subsequent idle period.
        await page.wait_for_function(
            "n => suitProbe().canvases.some(c => c.connected && c.renders > n)",
            arg=max(c["renders"] for c in offscreen["canvases"]),
        )
        resumed = await SUIT.stable_scene(page)
        await page.emulate_media(reduced_motion="reduce")
        await page.wait_for_function(
            "n => suitProbe().canvases.some(c => c.connected && c.renders > n)",
            arg=max(c["renders"] for c in resumed["canvases"]),
        )
        await stable_probe(page)
        # A mode transition owns geometry and closes diagnostics immediately.
        await desktop.signal("collapse")
        await desktop.settled(True)
        assert await page.locator(SUIT.PANEL).count() == 0
        closed = await stable_probe(page)
        assert closed["pending"] == 0
        assert all(not c["connected"] for c in closed["canvases"])
        await desktop.suit("open")
        await SUIT.ready_scene(page)
        calls_before = len(desktop.calls)
        await desktop.signal("return")
        await desktop.settled(False)
        assert await page.locator(SUIT.PANEL).count() == 0
        assert not any(
            cmd == "school_menu" and args.get("extra") == 0
            for cmd, args in desktop.calls[calls_before:]
        ), "Diagnostics close raced native transition geometry"
        assert not desktop.errors, desktop.errors
        return {
            "lifecycle": "boot, DPR2, idle, hidden, offscreen, reduced, mode transitions",
            "quality": quality,
        }
    finally:
        await context.close()


async def unavailable(browser, url):
    context, page, desktop = await setup(browser, url, no_webgl=True)
    try:
        await desktop.suit("open")
        await page.get_by_text("3D rendering is unavailable.", exact=False).wait_for()
        assert await page.get_by_role("meter").count() == 3
        await page.get_by_role("button", name="Critical", exact=True).click()
        assert (await SUIT.meters(page))["Simulated fuel reserve"] == 18
        await page.get_by_role(
            "button", name="Close suit diagnostics", exact=True
        ).click()
        await page.locator(SUIT.PANEL).wait_for(state="detached")
        assert not desktop.errors, desktop.errors
        return {
            "unavailable_webgl": "clear fallback, simulation controls and dismissal remain usable"
        }
    finally:
        await context.close()


async def run(url):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for check in (lifecycle, unavailable):
                print(json.dumps(await check(browser, url)), flush=True)
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url")
    args = parser.parse_args()
    server = None
    try:
        url = args.url
        if not url:
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                partial(SUIT.SCHOOL.QuietHandler, directory=str(SUIT.FRONTEND / "out")),
            )
            Thread(target=server.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{server.server_port}/"
        asyncio.run(run(url))
    finally:
        if server:
            server.shutdown()
            server.server_close()
