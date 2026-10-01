"""Ambient canvas browser checks; all native/bridge calls are mocked.

Run after exporting the frontend:
uv run --no-sync python frontend/tests/ambient-particles-smoke.py --screenshots /tmp/jarvis-ambient-review
Optional --url reuses an existing export/dev server. This never opens or changes
the real desktop, does not play live transitions, and never calls the real bridge.
"""

import argparse
import asyncio
import importlib.util
import json
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.async_api import async_playwright

FRONTEND = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "school_smoke", Path(__file__).with_name("school-mode-smoke.py")
)
SCHOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCHOOL)
SELECTOR = "canvas[data-ambient-particles]"

PROBE = r"""(() => {
    const entries = new Map();
    let serial = 0;
    const original = CanvasRenderingContext2D.prototype.clearRect;
    CanvasRenderingContext2D.prototype.clearRect = function(...args) {
        if (this.canvas.matches('canvas[data-ambient-particles]')) {
            let item = entries.get(this.canvas);
            if (!item) {
                item = {id: ++serial, canvas: this.canvas, frames: 0};
                entries.set(this.canvas, item);
            }
            item.frames += 1;
        }
        return original.apply(this, args);
    };
    window.ambientProbe = () => [...entries.values()].map(item => ({
        id: item.id, frames: item.frames, connected: item.canvas.isConnected,
        kind: item.canvas.dataset.ambientParticles,
    }));
})();"""


async def stats(page):
    return await page.locator(SELECTOR).evaluate_all("""canvases => canvases.map(canvas => {
        const box = canvas.getBoundingClientRect();
        const ctx = canvas.getContext('2d');
        const rgba = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
        let visiblePixels = 0, peakAlpha = 0;
        for (let i = 3; i < rgba.length; i += 4) {
            if (rgba[i] > 24) visiblePixels += 1;
            peakAlpha = Math.max(peakAlpha, rgba[i]);
        }
        return {
            kind: canvas.dataset.ambientParticles,
            width: canvas.width, height: canvas.height,
            cssWidth: box.width, cssHeight: box.height,
            dpr: devicePixelRatio, visiblePixels, peakAlpha,
            pointerEvents: getComputedStyle(canvas).pointerEvents,
            ariaHidden: canvas.getAttribute('aria-hidden'),
            tabIndex: canvas.tabIndex,
        };
    })""")


async def frames(page):
    return await page.evaluate("ambientProbe()")


async def check_canvas_quality(page):
    await page.locator(SELECTOR).first.wait_for(state="visible")
    await page.wait_for_timeout(150)
    result = await stats(page)
    assert result, "Ambient particles canvas missing"
    for item in result:
        assert item["visiblePixels"] > 0, f"Particles render blank: {item}"
        assert item["peakAlpha"] > 48, f"Particles are almost invisible: {item}"
        assert abs(item["width"] - round(item["cssWidth"] * item["dpr"])) <= 1, item
        assert abs(item["height"] - round(item["cssHeight"] * item["dpr"])) <= 1, item
        assert item["pointerEvents"] == "none", item
        assert item["ariaHidden"] == "true", item
        assert item["tabIndex"] == -1, item
    return result


async def check_suspension(page):
    start = await frames(page)
    await page.wait_for_timeout(160)
    moving = await frames(page)
    assert any(b["frames"] > a["frames"] for a, b in zip(start, moving)), (
        "Ambient animation never advances while visible"
    )
    await page.evaluate("""() => {
        Object.defineProperty(document, 'hidden', {configurable: true, value: true});
        document.dispatchEvent(new Event('visibilitychange'));
    }""")
    await page.wait_for_timeout(60)
    hidden = await frames(page)
    await page.wait_for_timeout(200)
    assert await frames(page) == hidden, "Hidden ambient canvas keeps rendering"
    await page.evaluate("""() => {
        delete document.hidden;
        document.dispatchEvent(new Event('visibilitychange'));
    }""")
    await page.wait_for_timeout(160)
    assert await frames(page) != hidden, "Ambient canvas failed to resume"
    await page.emulate_media(reduced_motion="reduce")
    await page.wait_for_timeout(80)
    reduced = await frames(page)
    await page.wait_for_timeout(200)
    assert await frames(page) == reduced, "Reduced-motion still animates particles"
    static = await check_canvas_quality(page)
    await page.emulate_media(reduced_motion="no-preference")
    await page.wait_for_timeout(160)
    assert await frames(page) != reduced, "Removing reduced-motion failed to resume"
    await page.locator(SELECTOR).evaluate_all(
        "canvases => canvases.forEach(canvas => canvas.style.transform = 'translateX(100000px)')"
    )
    await page.wait_for_timeout(100)
    offscreen = await frames(page)
    await page.wait_for_timeout(200)
    assert await frames(page) == offscreen, "Offscreen ambient canvas keeps rendering"
    await page.locator(SELECTOR).evaluate_all(
        "canvases => canvases.forEach(canvas => canvas.style.removeProperty('transform'))"
    )
    await page.wait_for_timeout(160)
    assert await frames(page) != offscreen, "Offscreen ambient canvas did not resume"
    await page.evaluate("document.documentElement.classList.add('school-tx')")
    await page.wait_for_timeout(80)
    transition = await frames(page)
    await page.wait_for_timeout(200)
    assert await frames(page) == transition, (
        "Ambient rendering competes with transition ownership"
    )
    await page.evaluate("document.documentElement.classList.remove('school-tx')")
    await page.wait_for_timeout(160)
    assert await frames(page) != transition, (
        "Ambient canvas did not resume after transition"
    )
    return static


async def screenshot_pair(page, shots, label):
    if not shots:
        return
    # Capture the same UI with only its ambient layer hidden, for visual review.
    await page.screenshot(path=str(shots / f"{label}-particles.png"))
    style = await page.add_style_tag(
        content=f"{SELECTOR} {{ visibility: hidden !important; }}"
    )
    await page.screenshot(path=str(shots / f"{label}-without-particles.png"))
    await style.evaluate("node => node.remove()")


async def run(browser, url, shots, dpr):
    page = await browser.new_page(
        viewport=SCHOOL.NORMAL,
        screen=SCHOOL.NORMAL,
        device_scale_factor=dpr,
        reduced_motion="no-preference",
    )
    desktop = SCHOOL.Desktop(page)
    await desktop.install()
    await page.add_init_script(PROBE)
    await page.goto(url, wait_until="domcontentloaded")
    await desktop.ready()
    await desktop.settled(False)
    main = await check_canvas_quality(page)
    await screenshot_pair(page, shots, f"hud-dpr{dpr}")
    await check_suspension(page)
    # Switching is mocked and instantaneous under reduced motion. The new work
    # is ambient content; this check only verifies its lifecycle across modes.
    await page.emulate_media(reduced_motion="reduce")
    await desktop.signal("collapse")
    await desktop.settled(True)
    await page.emulate_media(reduced_motion="no-preference")
    bar = await check_canvas_quality(page)
    await screenshot_pair(page, shots, f"school-bar-dpr{dpr}")
    await check_suspension(page)
    retired = [item for item in await frames(page) if not item["connected"]]
    assert retired, "HUD ambient canvas was not unmounted in school mode"
    await page.get_by_role("button", name="Applications", exact=True).click()
    await page.get_by_role("menu", name="Apps", exact=True).wait_for()
    await page.wait_for_timeout(250)
    menu = await check_canvas_quality(page)
    await screenshot_pair(page, shots, f"school-menu-dpr{dpr}")
    assert [item for item in await frames(page) if not item["connected"]] == retired, (
        "Unmounted HUD canvas kept rendering"
    )
    await page.get_by_role("button", name="Applications", exact=True).click()
    await page.wait_for_function("innerHeight === 64")
    assert not desktop.errors, desktop.errors
    await page.close()
    return {"dpr": dpr, "main": main, "bar": bar, "menu": menu}


async def main(args):
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    server = None
    url = args.url
    if not url:
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            partial(SCHOOL.QuietHandler, directory=str(FRONTEND / "out")),
        )
        Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}"
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                for dpr in (1, 2):
                    print(
                        json.dumps(await run(browser, url, args.screenshots, dpr)),
                        flush=True,
                    )
            finally:
                await browser.close()
    finally:
        if server:
            server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url")
    parser.add_argument("--screenshots", type=Path)
    asyncio.run(main(parser.parse_args()))
