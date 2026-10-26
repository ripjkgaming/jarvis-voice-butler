"""Isolated export motion/focus smoke check; all native and bridge I/O is mocked."""

import asyncio
import importlib.util
import json
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

from playwright.async_api import async_playwright

SPEC = importlib.util.spec_from_file_location(
    "insights", Path(__file__).with_name("insights-smoke.py")
)
INSIGHTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSIGHTS)
FRONTEND = Path(__file__).resolve().parents[1]
OUTPUT = FRONTEND / "tests" / "motion-evidence" / "2026-10-03"


class Desktop(INSIGHTS.Desktop):
    def __init__(self, page):
        super().__init__(page)
        self.panel = {"tab": None, "seq": 1}

    async def bridge(self, route):
        if urlparse(route.request.url).path == "/panels":
            return await route.fulfill(
                json={"insights": self.panel},
                headers={"Access-Control-Allow-Origin": "*"},
            )
        return await super().bridge(route)

    def open(self):
        self.panel = {"tab": "usage", "seq": self.panel["seq"] + 1}


async def run(url):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--disable-dev-shm-usage"]
        )
        try:
            page = await browser.new_page(viewport={"width": 1600, "height": 900})
            desk = Desktop(page)
            await desk.install()
            await page.goto(url)
            await desk.ready()
            await page.wait_for_timeout(700)
            await page.screenshot(path=str(OUTPUT / "hud-settled.png"))
            desk.muted = True
            await page.evaluate("window.dispatchEvent(new Event('focus'))")
            await page.locator('[data-voice-readout="MUTED"]').wait_for()
            assert (
                await page.locator("[data-voice-readout]").evaluate(
                    "e => getComputedStyle(e).animationDuration"
                )
                == "0.24s"
            )
            await page.wait_for_timeout(600)
            assert await page.locator("[data-voice-readout]").evaluate(
                'e => e.getAnimations({subtree:true}).every(a => a.playState === "finished")'
            )
            desk.muted = False
            await page.evaluate("window.dispatchEvent(new Event('focus'))")
            await page.locator('[data-voice-readout="STANDBY"]').wait_for()
            await page.evaluate("""() => {
                const trigger = document.createElement('button');
                trigger.id = 'mock-trigger'; trigger.textContent = 'Mock launcher';
                trigger.style.position = 'fixed'; trigger.style.left = '-1000px';
                document.body.append(trigger); trigger.focus();
            }""")
            desk.open()
            dialog = page.get_by_role("dialog", name="Insights", exact=True)
            await dialog.wait_for()
            await page.get_by_text("REPORT READY", exact=True).wait_for()
            await page.wait_for_timeout(350)
            assert await page.locator(".hud").evaluate("e => e.inert")
            await page.screenshot(path=str(OUTPUT / "insights-open.png"))
            usage = page.get_by_role(
                "tab", name="01 AI Usage Tokens & estimated spend", exact=True
            )
            await usage.focus()
            await page.keyboard.press("ArrowRight")
            market = page.get_by_role(
                "tab", name="02 Paper Market Portfolio & performance", exact=True
            )
            assert await market.evaluate(
                'e => e === document.activeElement && e.getAttribute("aria-selected") === "true"'
            )
            await page.keyboard.press("ArrowLeft")
            assert await usage.evaluate("e => e === document.activeElement")
            exit_state = await page.evaluate("""() => {
                document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
                document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
                return {held:document.querySelector('.hud').inert, dialog:!!document.querySelector('[role=dialog]')};
            }""")
            assert exit_state == {"held": True, "dialog": True}, exit_state
            await dialog.wait_for(state="detached")
            assert not await page.locator(".hud").evaluate("e => e.inert")
            assert await page.locator("#mock-trigger").evaluate(
                "e => e === document.activeElement"
            )
            await page.emulate_media(reduced_motion="reduce")
            desk.open()
            await dialog.wait_for()
            assert (
                await dialog.evaluate("e => getComputedStyle(e).animationName")
                == "none"
            )
            assert (
                await page.locator("[data-voice-readout]").evaluate(
                    "e => getComputedStyle(e).animationName"
                )
                == "none"
            )
            await page.set_viewport_size({"width": 520, "height": 900})
            await page.screenshot(path=str(OUTPUT / "insights-narrow-reduced.png"))
            box = await dialog.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= 521
            await page.keyboard.press("Escape")
            await dialog.wait_for(state="detached")
            assert not await page.locator(".hud").evaluate("e => e.inert")
            assert not desk.errors, desk.errors
            result = {
                "state_reveal_settles": True,
                "keyboard_tabs_keep_focus": True,
                "close_returns_focus": True,
                "repeated_escape_is_idempotent": True,
                "exit_retains_inert_until_unmount": True,
                "reopen_and_reduced_motion": True,
                "narrow_layout": True,
                "errors": desk.errors,
            }
            (OUTPUT / "report.json").write_text(json.dumps(result, indent=2))
            print(json.dumps(result))
        finally:
            await browser.close()


if __name__ == "__main__":
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(INSIGHTS.SUIT.SCHOOL.QuietHandler, directory=FRONTEND / "out"),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        asyncio.run(run(f"http://127.0.0.1:{server.server_port}"))
    finally:
        server.shutdown()
        thread.join()
