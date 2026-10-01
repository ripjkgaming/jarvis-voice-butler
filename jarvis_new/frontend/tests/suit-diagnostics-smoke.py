"""Mocked browser checks for the lazy suit diagnostics dialog.

Requires a fresh frontend/out export and an agreed browser/GPU test slot.
uv run --no-sync python frontend/tests/suit-diagnostics-smoke.py --output /tmp/jarvis-suit-review
Native commands and shared bridge responses are intercepted; no live desktop,
microphone, model service, or hardware telemetry is accessed.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import re
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

from playwright.async_api import async_playwright

FRONTEND = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "school_smoke", Path(__file__).with_name("school-mode-smoke.py")
)
SCHOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCHOOL)
PANEL = '[data-suit-diagnostics="true"]'
PHYSICAL_SCREEN = """(() => {
    for (const [property, value] of Object.entries({
        width: 1600, height: 900, availWidth: 1600, availHeight: 900,
    })) Object.defineProperty(screen, property, {configurable: true, get: () => value});
})();"""


class Desktop(SCHOOL.Desktop):
    def __init__(self, page):
        super().__init__(page)
        self.command = None
        self.revision = 0
        self.local_commands = []
        self.focus_serial = 122
        self.focus_lease = None
        self.focus_events = []
        self.focus_acquired = asyncio.Event()

    async def invoke(self, source, command, args):
        if command != "suit_focus":
            return await super().invoke(source, command, args)
        self.calls.append((command, args))
        if args["active"]:
            self.focus_serial += 1
            self.focus_lease = self.focus_serial
            result = self.focus_lease
            self.focus_acquired.set()
        else:
            if args.get("lease") in (None, self.focus_lease):
                self.focus_lease = None
            result = None
        self.focus_events.append({**args, "held": self.focus_lease})
        return result

    async def publish(self, operation):
        self.revision += 1
        self.command = {
            "op": operation,
            "revision": self.revision,
            "issued_at": max(
                await self.page.evaluate("Date.now()"),
                (self.command or {}).get("issued_at", 0) + 1,
            ),
            "session": "isolated-suit-ui-test",
        }

    async def bridge(self, route):
        path = urlparse(route.request.url).path
        if route.request.method == "OPTIONS":
            return await route.fulfill(
                status=204,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, Authorization",
                },
            )
        if path == "/suit":
            payload = route.request.post_data_json
            assert type(payload.get("open")) is bool, payload
            self.local_commands.append(payload)
            await self.publish("open" if payload["open"] else "close")
            return await route.fulfill(
                json={"ok": True, "suit_diagnostics": self.command},
                headers={"Access-Control-Allow-Origin": "*"},
            )
        if path != "/sys":
            return await super().bridge(route)
        owner = self

        class Intercept:
            request = route.request

            async def fulfill(self, **kwargs):
                kwargs["json"]["suit_diagnostics"] = owner.command
                return await route.fulfill(**kwargs)

        return await super().bridge(Intercept())

    async def suit(self, operation):
        await self.publish(operation)
        await self.page.get_by_role(
            "dialog", name="Suit diagnostics", exact=True
        ).wait_for(
            state="visible" if operation == "open" else "detached", timeout=15000
        )


PROBE = r"""(() => {
    const request = window.requestAnimationFrame.bind(window);
    const cancel = window.cancelAnimationFrame.bind(window);
    const frames = new Map(), callbacks = new Set(), canvases = new Map();
    let current = null, serial = 0, executed = 0;
    window.requestAnimationFrame = callback => {
        const id = request(now => {
            frames.delete(id);
            const previous = current;
            current = callback;
            if (callbacks.has(callback)) executed += 1;
            try { callback(now); } finally { current = previous; }
        });
        frames.set(id, callback);
        return id;
    };
    window.cancelAnimationFrame = id => { frames.delete(id); cancel(id); };
    for (const type of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) {
        if (!type) continue;
        const clear = type.prototype.clear;
        type.prototype.clear = function(...args) {
            let item = canvases.get(this.canvas);
            if (!item && this.canvas.closest('[data-suit-diagnostics]')) {
                item = {id: ++serial, canvas: this.canvas, renders: 0};
                canvases.set(this.canvas, item);
            }
            if (item) { item.renders += 1; if (current) callbacks.add(current); }
            return clear.apply(this, args);
        };
    }
    window.suitProbe = () => ({executed,
        pending: [...frames.values()].filter(fn => callbacks.has(fn)).length,
        canvases: [...canvases.values()].map(item => ({id: item.id, renders: item.renders,
            connected: item.canvas.isConnected, width: item.canvas.width, height: item.canvas.height})),
    });
})();"""


async def meters(page):
    return (
        await page.get_by_role("dialog", name="Suit diagnostics")
        .get_by_role("meter")
        .evaluate_all(
            "elements => Object.fromEntries(elements.map(node => [node.getAttribute('aria-label'), Number(node.getAttribute('aria-valuenow'))]))"
        )
    )


async def ready_scene(page):
    await page.wait_for_function(
        "suitProbe().canvases.some(canvas => canvas.connected && canvas.renders > 1)",
        timeout=20000,
    )


async def stable_scene(page, *, duration=350):
    await page.wait_for_function(
        "document.querySelector('[data-suit-renderer=webgl]')?.dataset.suitRunning === 'false'",
        timeout=10000,
    )
    # Let ResizeObserver and initialization changes drain, then assert actual
    # WebGL work and the renderer's owning RAF stay unchanged independently.
    await page.wait_for_timeout(120)
    before = await page.evaluate("suitProbe()")
    assert before["pending"] == 0, before
    await page.wait_for_timeout(duration)
    assert await page.evaluate("suitProbe()") == before, (
        "Idle model retained a RAF loop"
    )
    return before


async def visibility_checks(page):
    await stable_scene(page)
    await page.evaluate("""() => {
        Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
        document.dispatchEvent(new Event('visibilitychange'));
    }""")
    hidden = await page.evaluate("suitProbe()")
    await page.get_by_role("button", name="Front", exact=True).click()
    await page.wait_for_timeout(180)
    assert await page.evaluate("suitProbe()") == hidden, (
        "Hidden viewer rendered a camera change"
    )
    await page.evaluate("""() => {
        delete document.hidden;
        document.dispatchEvent(new Event('visibilitychange'));
    }""")
    await page.wait_for_function(
        "count => suitProbe().canvases.reduce((sum, item) => sum + item.renders, 0) > count",
        arg=sum(canvas["renders"] for canvas in hidden["canvases"]),
    )
    await stable_scene(page)
    await page.locator("[data-suit-renderer]").evaluate(
        "node => { node.style.transform = 'translateX(200vw)'; }"
    )
    await page.wait_for_timeout(160)
    offscreen = await page.evaluate("suitProbe()")
    await page.get_by_role("button", name="3/4 view", exact=True).click()
    await page.wait_for_timeout(180)
    assert await page.evaluate("suitProbe()") == offscreen, (
        "Offscreen viewer rendered a camera change"
    )
    await page.locator("[data-suit-renderer]").evaluate(
        "node => { node.style.transform = ''; }"
    )
    await page.wait_for_function(
        "count => suitProbe().canvases.reduce((sum, item) => sum + item.renders, 0) > count",
        arg=sum(canvas["renders"] for canvas in offscreen["canvases"]),
    )
    await stable_scene(page)


async def context_loss_checks(page):
    await stable_scene(page)
    supported = await page.locator("[data-suit-canvas]").evaluate("""canvas => {
        const context = canvas.getContext('webgl2') || canvas.getContext('webgl');
        const extension = context?.getExtension('WEBGL_lose_context');
        if (!extension) return false;
        extension.loseContext();
        return true;
    }""")
    assert supported, "Browser did not expose the context-loss test extension"
    await (
        page.get_by_role("status")
        .filter(has_text="3D rendering is unavailable")
        .wait_for()
    )
    await page.wait_for_timeout(120)
    lost = await page.evaluate("suitProbe()")
    await page.get_by_role("button", name="Critical", exact=True).click()
    assert (await meters(page))["Simulated reactor charge"] == 29
    await page.wait_for_timeout(180)
    assert await page.evaluate("suitProbe()") == lost, (
        "Lost context retained renderer work"
    )
    await page.get_by_role("button", name="Retry 3D renderer", exact=True).click()
    await ready_scene(page)
    await (
        page.get_by_role("status")
        .filter(has_text="3D rendering is unavailable")
        .wait_for(state="detached")
    )
    retried = await stable_scene(page)
    assert sum(canvas["connected"] for canvas in retried["canvases"]) == 1, retried
    assert len(retried["canvases"]) > len(lost["canvases"]), retried


async def focus_checks(page):
    close = page.get_by_role("button", name="Close suit diagnostics", exact=True)
    assert await close.evaluate("node => document.activeElement === node")
    await page.keyboard.press("Shift+Tab")
    assert await page.locator(PANEL).evaluate(
        "node => node.contains(document.activeElement)"
    )
    await page.keyboard.press("Tab")
    assert await close.evaluate("node => document.activeElement === node"), (
        "Focus trap failed to wrap"
    )
    selected = page.get_by_role("option", name=re.compile("Thoracic armor"))
    await selected.focus()
    await selected.press("ArrowDown")
    reactor = page.get_by_role("option", name=re.compile("Arc reactor"))
    assert await reactor.get_attribute("aria-selected") == "true"
    assert await reactor.evaluate("node => node === document.activeElement")
    await reactor.press("End")
    assert (
        await page.get_by_role("option", name=re.compile("Right leg")).get_attribute(
            "aria-selected"
        )
        == "true"
    )
    await page.get_by_role("option", name=re.compile("Right leg")).press("Home")
    assert (
        await page.get_by_role("option", name=re.compile("Helmet")).get_attribute(
            "aria-selected"
        )
        == "true"
    )
    # Every region must be reachable with visible keyboard focus, including
    # entries that require the compact/narrow panel to scroll.
    for _ in range(7):
        focused = await page.evaluate("""() => {
            const node = document.activeElement;
            const rect = node.getBoundingClientRect();
            const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
            return {role: node.getAttribute('role'), text: node.textContent,
                visible: !!hit && (node === hit || node.contains(hit))};
        }""")
        assert focused["role"] == "option" and focused["visible"], focused
        await page.keyboard.press("ArrowDown")


async def run(browser, url, output, *, school=False, width=1600, height=900, dpr=1):
    label = ("school" if school else f"normal-{width}") + f"-dpr{dpr}"
    context = await browser.new_context(
        # Hydrate at the normal HUD size before exercising narrow layouts;
        # its MIC ON readiness signal is intentionally hidden on small screens.
        viewport=SCHOOL.NORMAL,
        screen=SCHOOL.NORMAL,
        device_scale_factor=dpr,
        reduced_motion="reduce" if school else "no-preference",
    )
    page = await context.new_page()
    assets = []
    page.on(
        "request",
        lambda request: assets.append(
            {"url": request.url, "type": request.resource_type}
        ),
    )
    desktop = Desktop(page)
    await desktop.install()
    # Playwright changes screen.availHeight alongside set_viewport_size, unlike
    # the native desktop where resizing the window preserves its monitor size.
    await page.add_init_script(PHYSICAL_SCREEN)
    await page.add_init_script(PROBE)
    await page.goto(url, wait_until="domcontentloaded")
    await desktop.ready()
    try:
        await page.set_viewport_size({"width": width, "height": height})
        assert await page.locator(f"{PANEL} canvas").count() == 0
        if school:
            await desktop.signal("collapse")
            await desktop.settled(True)
            await page.emulate_media(reduced_motion="no-preference")
        await page.evaluate("""() => {
            const sentinel = document.createElement('button');
            sentinel.id = 'suit-focus-sentinel';
            sentinel.style.cssText = 'position:fixed;left:-9999px';
            sentinel.textContent = 'Test focus return';
            document.body.append(sentinel);
            sentinel.focus();
        }""")
        before_open = len(assets)
        scripts_before = sorted(
            {asset["url"] for asset in assets if asset["type"] == "script"}
        )
        await desktop.suit("open")
        await ready_scene(page)
        idle = await stable_scene(page)
        initial_screenshot = output / f"suit-{label}-initial.png"
        await page.screenshot(path=str(initial_screenshot))
        print(json.dumps({"initial_screenshot": str(initial_screenshot)}), flush=True)
        opened_assets = assets[before_open:]
        lazy_scripts = sorted(
            {
                asset["url"]
                for asset in opened_assets
                if asset["type"] == "script" and asset["url"] not in scripts_before
            }
        )
        assert lazy_scripts, (
            "Suit renderer was already loaded while diagnostics were closed"
        )
        external_assets = [
            asset
            for asset in opened_assets
            if asset["type"] in {"script", "image", "font", "media"}
            and urlparse(asset["url"]).scheme in {"http", "https"}
            and urlparse(asset["url"]).netloc != urlparse(url).netloc
        ]
        assert not external_assets, external_assets
        pixels = await page.locator("[data-suit-canvas]").evaluate("""canvas => {
            const rect = canvas.parentElement.getBoundingClientRect();
            return {width: canvas.width, height: canvas.height, cssWidth: rect.width,
                cssHeight: rect.height, dpr: devicePixelRatio};
        }""")
        assert abs(pixels["width"] - round(pixels["cssWidth"]) * dpr) <= 1, pixels
        assert abs(pixels["height"] - round(pixels["cssHeight"]) * dpr) <= 1, pixels
        dialog = page.get_by_role("dialog", name="Suit diagnostics", exact=True)
        layout_width = await dialog.evaluate("""node => {
            const rect = node.getBoundingClientRect();
            const main = node.querySelector('main');
            return {left: rect.left, right: rect.right, viewport: innerWidth,
                bodyScroll: document.body.scrollWidth,
                mainScroll: main.scrollWidth, mainWidth: main.clientWidth};
        }""")
        assert layout_width["left"] >= -1 and layout_width["right"] <= width + 1, (
            layout_width
        )
        assert layout_width["bodyScroll"] <= width + 1, layout_width
        assert layout_width["mainScroll"] <= layout_width["mainWidth"] + 1, layout_width
        assert await dialog.get_attribute("aria-modal") == "true"
        assert await page.get_by_label(
            "Simulation, fictional suit telemetry", exact=True
        ).is_visible()
        assert await meters(page) == {
            "Simulated reactor charge": 76,
            "Simulated fuel reserve": 58,
            "Simulated coolant level": 83,
        }
        await focus_checks(page)
        if school:
            assert page.viewport_size["height"] > 64, (
                "School native viewport did not expand"
            )
            layout = await page.locator(PANEL).evaluate("""node => ({
                bottom: node.getBoundingClientRect().bottom,
                viewport: innerHeight,
                barVisible: !!document.querySelector('.sbar'),
                barInert: document.querySelector('.sbar-root')?.inert,
            })""")
            assert layout["bottom"] <= layout["viewport"] - 64 + 1, layout
            assert layout["barVisible"] and layout["barInert"], layout
            assert desktop.focus_lease == 123, desktop.focus_events
        else:
            assert await page.locator(".hud").evaluate("node => node.inert"), (
                "Background HUD remains interactive"
            )
        await page.get_by_role("option", name=re.compile("Thoracic armor")).click()
        await page.locator(f"{PANEL} main").evaluate("node => { node.scrollTop = 0; }")
        await page.screenshot(path=str(output / f"suit-{label}-post-flight.png"))

        await page.get_by_role("button", name="Critical", exact=True).click()
        assert await meters(page) == {
            "Simulated reactor charge": 29,
            "Simulated fuel reserve": 18,
            "Simulated coolant level": 42,
        }
        await page.get_by_role("option", name=re.compile("Left arm")).click()
        selected = page.get_by_role("region", name="Selected region", exact=True)
        assert "78%" in await selected.inner_text()
        await page.get_by_role("button", name="Back", exact=True).click()
        assert (
            await page.get_by_role("button", name="Back", exact=True).get_attribute(
                "aria-pressed"
            )
            == "true"
        )
        await stable_scene(page, duration=100)
        await page.locator(f"{PANEL} main").evaluate("node => { node.scrollTop = 0; }")
        await page.screenshot(path=str(output / f"suit-{label}-critical-back.png"))
        await page.get_by_role("button", name="Nominal", exact=True).click()
        assert await meters(page) == {
            "Simulated reactor charge": 98,
            "Simulated fuel reserve": 94,
            "Simulated coolant level": 96,
        }
        await page.get_by_role("button", name="Pause model motion", exact=True).click()
        await page.wait_for_timeout(120)
        paused = await page.evaluate("suitProbe()")
        await page.wait_for_timeout(200)
        assert await page.evaluate("suitProbe()") == paused, (
            "Paused model still uses RAF"
        )
        await page.get_by_role("button", name="Resume model motion", exact=True).click()
        await page.wait_for_timeout(120)
        if not school and width == 1600 and dpr == 1:
            await visibility_checks(page)

        await page.keyboard.press("Escape")
        await dialog.wait_for(state="detached")
        assert await page.evaluate("document.activeElement.id") == "suit-focus-sentinel"
        assert not any(
            command in ("hide_overlay", "overlay_hide")
            for command, _args in desktop.calls
        ), desktop.calls
        if school:
            await page.wait_for_function("innerHeight === 64")
            assert await page.locator(".sbar-root").evaluate("node => !node.inert")
        await page.wait_for_timeout(100)
        closed = await page.evaluate("suitProbe()")
        assert closed["pending"] == 0 and all(
            not canvas["connected"] for canvas in closed["canvases"]
        )
        await page.wait_for_timeout(800)
        if school:
            assert desktop.focus_lease is None, desktop.focus_events
            assert any(
                event["active"] is False and event.get("lease") == 123
                for event in desktop.focus_events
            ), desktop.focus_events
        assert desktop.local_commands[-1] == {"open": False}, desktop.local_commands
        assert desktop.command["op"] == "close", desktop.command
        assert await page.evaluate("suitProbe()") == closed, (
            "Closed viewer retained rendering work"
        )
        assert await page.locator(PANEL).count() == 0, (
            "Consumed open command replayed after close"
        )
        await desktop.suit("open")
        await ready_scene(page)
        assert (await meters(page))["Simulated reactor charge"] == 76, (
            "Local preset state leaked across mounts"
        )
        if not school and width == 1600 and dpr == 1:
            await context_loss_checks(page)
        await desktop.suit("close")
        await page.wait_for_timeout(100)
        assert desktop.focus_lease is None, desktop.focus_events
        if school:
            assert any(
                event["active"] is False and event.get("lease") == 124
                for event in desktop.focus_events
            ), desktop.focus_events
        else:
            assert not any(event["active"] for event in desktop.focus_events), (
                desktop.focus_events
            )
        assert not desktop.errors, desktop.errors
        return {
            "school": school,
            "width": width,
            "dpr": dpr,
            "canvas_pixels": pixels,
            "layout_width": layout_width,
            "idle_probe": idle,
            "scripts_before_open": scripts_before,
            "lazy_scripts_on_open": lazy_scripts,
            "external_scene_assets": external_assets,
            "native_focus_events": desktop.focus_events,
            "initial_meters": "post-flight",
            "focus_keyboard_presets_escape": "passed",
            "closed_probe": closed,
        }
    finally:
        await context.close()


async def reload_school(browser, url, output):
    """A frontend reload must release a school dialog's expanded native window."""
    context = await browser.new_context(
        viewport=SCHOOL.NORMAL,
        screen=SCHOOL.NORMAL,
        reduced_motion="reduce",
    )
    page = await context.new_page()
    desktop = Desktop(page)
    await desktop.install()
    await page.add_init_script(PHYSICAL_SCREEN)
    try:
        await page.goto(url, wait_until="domcontentloaded")
        await desktop.ready()
        await desktop.signal("collapse")
        await desktop.settled(True)
        await desktop.suit("open")
        await page.wait_for_function("innerHeight > 64")
        await asyncio.wait_for(desktop.focus_acquired.wait(), timeout=5)
        assert desktop.focus_lease == 123, desktop.focus_events
        start = len(desktop.calls)
        await page.reload(wait_until="domcontentloaded")
        try:
            await page.wait_for_function(
                "innerHeight === 64 && !!document.querySelector('.sbar') && !document.querySelector('[data-suit-diagnostics]') && !document.querySelector('.hud')",
                timeout=10000,
            )
            await page.wait_for_timeout(800)
            state = await page.evaluate("""() => ({
                height: innerHeight,
                suit: !!document.querySelector('[data-suit-diagnostics]'),
                bar: !!document.querySelector('.sbar'),
                hud: !!document.querySelector('.hud'),
            })""")
            assert state == {"height": 64, "suit": False, "bar": True, "hud": False}, (
                state
            )
            assert desktop.focus_lease is None, desktop.focus_events
            assert not desktop.errors, desktop.errors
        except Exception:
            state = await page.evaluate("""() => ({
                height: innerHeight,
                suit: !!document.querySelector('[data-suit-diagnostics]'),
                bar: !!document.querySelector('.sbar'),
                hud: !!document.querySelector('.hud'),
            })""")
            failure = {
                "school_reload": "failed",
                "state": state,
                "native_calls": desktop.calls[start:],
                "errors": desktop.errors,
            }
            (output / "reload-report.json").write_text(json.dumps(failure, indent=2))
            print(json.dumps(failure), flush=True)
            raise
        result = {
            "school_reload": "passed",
            "state": state,
            "native_calls": desktop.calls[start:],
            "errors": desktop.errors,
        }
        (output / "reload-report.json").write_text(json.dumps(result, indent=2))
        return result
    finally:
        await context.close()


async def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
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
                results = []
                cases = (
                    []
                    if args.reload_only
                    else [
                        {},
                        {"school": True},
                        {"width": 520, "height": 820},
                        {"dpr": 2},
                    ]
                )
                for options in cases:
                    result = await run(browser, url, args.output, **options)
                    results.append(result)
                    print(json.dumps(result), flush=True)
                    (args.output / "report.json").write_text(
                        json.dumps(results, indent=2)
                    )
                print(
                    json.dumps(await reload_school(browser, url, args.output)),
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
    parser.add_argument("--reload-only", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("/tmp/jarvis-suit-review"))
    asyncio.run(main(parser.parse_args()))
