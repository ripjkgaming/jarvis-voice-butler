"""Production-export return recovery with only mocked native IPC and bridge I/O.

Run after export: uv run --no-sync python frontend/tests/school-return-smoke.py.
No real native/desktop commands, microphone or model calls are possible.
"""

from __future__ import annotations

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
    "school_smoke", Path(__file__).with_name("school-mode-smoke.py")
)
SCHOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCHOOL)


class FailingReturnDesktop(SCHOOL.Desktop):
    def __init__(self, page, failure):
        super().__init__(page)
        self.failure = failure
        self.armed = False
        self.release = asyncio.Event()

    async def invoke(self, source, command, args):
        if (
            self.armed
            and command == "school_stage"
            and args.get("stage") == self.failure
        ):
            self.calls.append((command, args))
            await self.release.wait()
            # The late reply itself cannot trigger a new geometry operation.
            return None
        return await super().invoke(source, command, args)


async def case(browser, url, failure):
    context = await browser.new_context(
        viewport=SCHOOL.NORMAL, screen=SCHOOL.NORMAL, reduced_motion="reduce"
    )
    page = await context.new_page()
    desktop = FailingReturnDesktop(page, failure)
    # Restrict network to this static export; page-level fixture routes added
    # below take precedence over this deny-by-default context route.
    await context.route(
        "**/*",
        lambda route: (
            route.continue_()
            if route.request.url.startswith(url.rstrip("/") + "/")
            else route.abort()
        ),
    )
    await desktop.install()
    try:
        await page.goto(url, wait_until="domcontentloaded")
        await desktop.ready()
        await desktop.signal("collapse")
        await desktop.settled(True)
        if failure == "measure":
            await page.evaluate("localStorage.removeItem('jarvis.school.hudRect')")
        if failure == "no-context":
            await page.evaluate("""() => {
                const original = HTMLCanvasElement.prototype.getContext;
                HTMLCanvasElement.prototype.getContext = function(kind, ...args) {
                    if (kind === '2d' && this.closest('.stx--return')) return null;
                    return original.call(this, kind, ...args);
                };
            }""")
        await page.emulate_media(
            reduced_motion="reduce" if failure == "restore" else "no-preference"
        )
        desktop.armed = True
        before = len(desktop.stages())
        started = asyncio.get_running_loop().time()
        await desktop.signal("return")
        await desktop.settled(False)
        elapsed = asyncio.get_running_loop().time() - started
        assert elapsed < 6, (failure, elapsed)
        stages = desktop.stages()[before:]
        assert stages.count("restore") == 1, (failure, stages)
        assert (
            await page.locator("html.school-tx, html.stx-scan, .stx--return").count()
            == 0
        )
        desktop.release.set()
        await page.wait_for_timeout(250)
        assert desktop.stages()[before:] == stages, "Late reply restarted native stages"
        assert not desktop.errors, desktop.errors
        return {"failure": failure, "elapsed_s": round(elapsed, 2), "stages": stages}
    finally:
        desktop.release.set()
        await context.close()


async def run(url):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for failure in ("cover-at", "restore", "measure", "no-context"):
                print(json.dumps(await case(browser, url, failure)), flush=True)
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
                partial(SCHOOL.QuietHandler, directory=str(SCHOOL.FRONTEND / "out")),
            )
            Thread(target=server.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{server.server_port}/"
        asyncio.run(run(url))
    finally:
        if server:
            server.shutdown()
            server.server_close()
