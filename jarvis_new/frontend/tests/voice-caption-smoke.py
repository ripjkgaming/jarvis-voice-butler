"""Headless caption regression against a static export; all bridge/native I/O is mocked."""

import argparse
import asyncio
import importlib.util
import json
import time
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


class CaptionDesktop(SCHOOL.Desktop):
    def __init__(self, page):
        super().__init__(page)
        self.captions = []
        self.live = None
        self.hold_captions = False
        self.caption_waiting = asyncio.Event()
        self.release_captions = asyncio.Event()

    async def bridge(self, route):
        path = urlparse(route.request.url).path
        if path == "/room":
            data = {"ok": True, "room": "synthetic-caption-room", "boot": None}
        elif path == "/captions":
            if self.hold_captions:
                self.caption_waiting.set()
                await self.release_captions.wait()
            data = {"ok": True, "captions": self.captions}
        elif path == "/caption/live":
            data = {"ok": True, "live": self.live}
        else:
            return await super().bridge(route)
        await route.fulfill(json=data, headers={"Access-Control-Allow-Origin": "*"})

    def user(self, text, stamp=None):
        self.captions = [
            {
                "ts": int(time.time()) if stamp is None else stamp,
                "role": "sir",
                "text": text,
            }
        ]


async def run(url, screenshots):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        # Permit only this private export server; mock IPC registers its own
        # route afterward. A broken mock cannot reach a real bridge/service.
        await page.route(
            "**/*",
            lambda route: (
                route.continue_()
                if route.request.url.startswith(url + "/")
                else route.abort()
            ),
        )
        desktop = CaptionDesktop(page)
        await desktop.install()
        await page.goto(url)
        await desktop.ready()
        results = {}

        async def shown(name, text):
            label = page.get_by_text(text, exact=True)
            await label.wait_for(timeout=5000)
            assert await label.is_visible(), text
            bounds = await label.bounding_box()
            assert bounds and 0 <= bounds["y"] < page.viewport_size["height"], bounds
            assert bounds["y"] + bounds["height"] <= page.viewport_size["height"], (
                bounds
            )
            results[name] = text
            if screenshots:
                screenshots.mkdir(parents=True, exist_ok=True)
                await page.screenshot(
                    path=str(screenshots / f"{name}.png"), animations="disabled"
                )

        stamp = int(time.time())
        desktop.live = {
            "id": "finished-greeting",
            "ts": stamp + 0.4,
            "text": "Synthetic finished greeting.",
            "done": True,
        }
        desktop.user("The user spoke after the greeting.", stamp)
        await shown("normal-same-second", "The user spoke after the greeting.")

        desktop.live = {
            "id": "interrupted-greeting",
            "ts": stamp - 10,
            "text": "Synthetic interrupted greeting.",
            "done": False,
        }
        desktop.user("This user line supersedes the interrupted greeting.")
        await shown(
            "normal-interrupted", "This user line supersedes the interrupted greeting."
        )

        # Exact fractional ordering still allows new synced Jarvis speech.
        stamp = time.time()
        desktop.user("The preceding user request.", stamp - 0.1)
        desktop.live = {
            "id": "current-response",
            "ts": stamp,
            "text": "Synthetic current response.",
            "done": False,
        }
        await shown("normal-live-response", "Synthetic current response.")

        await desktop.signal("collapse")
        await desktop.settled(True)
        desktop.live = {
            "id": "old-school-greeting",
            "ts": time.time() - 10,
            "text": "Synthetic old school greeting.",
            "done": False,
        }
        desktop.user("The school bar shows the current user.")
        await shown("school-interrupted", "The school bar shows the current user.")

        # Remount the normal caption while its shared request is in flight.
        desktop.hold_captions = True
        await asyncio.wait_for(desktop.caption_waiting.wait(), 3)
        desktop.user("The current user remains visible after returning.")
        await desktop.signal("return")
        desktop.hold_captions = False
        desktop.release_captions.set()
        await desktop.settled(False)
        await shown(
            "normal-returned", "The current user remains visible after returning."
        )
        assert not desktop.errors, desktop.errors
        results["page_errors"] = desktop.errors
        print(json.dumps(results))
        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", type=Path, default=FRONTEND / "out")
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(SCHOOL.QuietHandler, directory=str(args.export.resolve())),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        asyncio.run(run(f"http://127.0.0.1:{server.server_port}", args.screenshots))
    finally:
        server.shutdown()
        thread.join()
