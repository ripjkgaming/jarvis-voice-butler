"""Regression for volume edits, external changes, failed writes and close flush.

uv run python frontend/tests/quick-volume-smoke.py
Requires frontend/out from a frontend build. All audio/native calls are mocked.
"""

import asyncio
import importlib.util
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

from playwright.async_api import async_playwright

spec = importlib.util.spec_from_file_location(
    "school_smoke", Path(__file__).with_name("school-mode-smoke.py")
)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class AudioDesktop(smoke.Desktop):
    def __init__(self, page):
        super().__init__(page)
        self.volume = {"pct": 33, "muted": False}
        self.edits = []
        self.fail = False
        self.active = 0
        self.max_active = 0

    async def bridge(self, route):
        if urlparse(route.request.url).path != "/quick":
            return await super().bridge(route)
        if route.request.method == "POST":
            body = route.request.post_data_json
            self.edits.append(body)
            self.active += 1
            self.max_active = max(self.active, self.max_active)
            await asyncio.sleep(0.25)
            if not self.fail:
                if body["action"] == "volume":
                    self.volume["pct"] = body["pct"]
                elif body["action"] == "mute":
                    self.volume["muted"] = body["on"]
            self.active -= 1
        await route.fulfill(
            json={"ok": not self.fail, "volume": self.volume},
            headers={"Access-Control-Allow-Origin": "*"},
        )


async def main():
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(smoke.QuietHandler, directory=str(smoke.FRONTEND / "out")),
    )
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page(
                viewport=smoke.NORMAL, reduced_motion="reduce"
            )
            page.set_default_timeout(5000)
            desktop = AudioDesktop(page)
            await desktop.install()
            await page.goto(f"http://127.0.0.1:{server.server_port}")
            await desktop.ready()
            await desktop.signal("collapse")
            await desktop.settled(True)
            toggle = page.get_by_role("button", name="Quick settings", exact=True)
            await toggle.click()
            slider = page.get_by_role("slider", name="VOLUME", exact=True)
            await slider.wait_for()
            assert await slider.input_value() == "33"
            desktop.volume["pct"] = 67
            await page.wait_for_function(
                "document.querySelector('input[aria-label=VOLUME]').value === '67'"
            )
            await slider.fill("21")
            await page.wait_for_timeout(100)
            await slider.fill("49")
            await page.wait_for_timeout(100)
            await slider.fill("0")
            await page.wait_for_function(
                "document.querySelector('input[aria-label=VOLUME]').value === '0'"
            )
            await page.wait_for_timeout(1200)
            assert desktop.volume["pct"] == 0
            assert desktop.max_active == 1, "volume writes overlapped"
            await page.locator(".smenu__slider button").click()
            await page.locator(".smenu__slider button", has_text="UNMUTE").wait_for()
            await page.wait_for_timeout(450)
            assert desktop.volume["muted"] is True
            desktop.fail = True
            await slider.fill("70")
            await page.get_by_role("alert").wait_for()
            await page.wait_for_function(
                "document.querySelector('input[aria-label=VOLUME]').value === '0'"
            )
            assert await slider.input_value() == "0", "failed edit remained on slider"
            desktop.fail = False
            await slider.fill("42")
            await toggle.click()
            await page.wait_for_timeout(600)
            assert desktop.volume["pct"] == 42, "closing dropped the final edit"
            assert not desktop.errors, desktop.errors
            print(
                "PASS: external sync, ordered writes, zero, mute, failure read-back, close flush"
            )
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()


asyncio.run(main())
