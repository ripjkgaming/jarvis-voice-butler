"""Voice join UX on an isolated static export; all native/bridge calls mocked.
uv run --no-sync python frontend/tests/voice-link-smoke.py --screenshots /tmp/jarvis-voice-link
"""

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


class VoiceDesktop(SCHOOL.Desktop):
    def __init__(self, page):
        super().__init__(page)
        self.room = {"ok": True, "room": None, "waking": False, "boot": None}
        self.summons = 0
        self.summon_ok = True
        self.hang = False
        self.release = asyncio.Event()

    async def invoke(self, source, command, args):
        if command == "talk":
            self.summons += 1
            if self.hang:
                await self.release.wait()
                return {"ok": False}
            return {"ok": self.summon_ok}
        return await super().invoke(source, command, args)

    async def bridge(self, route):
        if urlparse(route.request.url).path == "/room":
            return await route.fulfill(
                json=self.room, headers={"Access-Control-Allow-Origin": "*"}
            )
        return await super().bridge(route)

    def stage(self, stage, *, ago=0, fresh=False):
        stamp = time.time() - ago
        previous = None if fresh else self.room.get("boot")
        self.room = {
            "ok": True,
            "room": "mock-voice" if stage in ("dispatch", "online") else None,
            "waking": True,
            "boot": (previous or [["wake", stamp - 0.01]]) + [[stage, stamp]],
        }

    def clear(self):
        self.room = {"ok": True, "room": None, "waking": False, "boot": None}


async def phase(page, value):
    await page.wait_for_function(
        "v => document.querySelector('[data-voice-phase]')?.dataset.voicePhase === v",
        arg=value,
        timeout=4000,
    )


async def run(url, folder):
    folder.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport=SCHOOL.NORMAL,
            device_scale_factor=2,
            record_video_dir=str(folder / "video"),
            record_video_size=SCHOOL.NORMAL,
        )
        page = await context.new_page()
        desktop = VoiceDesktop(page)
        await desktop.install()
        await page.goto(url)
        await desktop.ready()
        await phase(page, "idle")
        await page.keyboard.press("NumpadEnter")
        await phase(page, "request")
        await page.keyboard.press("NumpadEnter")
        assert desktop.summons == 1, "Repeated summon duplicated a starting call"
        await page.wait_for_timeout(400)
        await phase(page, "request")  # native acceptance is not agent readiness
        await page.screenshot(path=str(folder / "main-acquiring-dpr2.png"))
        desktop.stage("connect", fresh=True)
        await phase(page, "relay")
        await page.wait_for_timeout(300)
        await page.screenshot(path=str(folder / "main-relay-dpr2.png"))
        desktop.stage("dispatch", ago=18)
        await phase(page, "agent")
        assert await page.locator(".hud").get_attribute("data-jstate") == "idle"
        assert await page.get_by_text("CONTACTING JARVIS", exact=True).count() == 1
        assert not await page.get_by_text("LISTENING", exact=True).count()
        assert (
            await page.get_by_text(
                "STILL WAITING FOR THE AGENT AUDIO LINK", exact=True
            ).count()
            == 1
        )
        assert (
            await page.locator('.vlink-reticle__sectors [data-stage="done"]').count()
            == 2
        )
        assert (
            await page.locator('.vlink-reticle__sectors [data-stage="current"]').count()
            == 1
        )
        await page.screenshot(path=str(folder / "main-agent-link-dpr2.png"))
        # Expired server boot must not transform an observed pending room into listening.
        desktop.room = {"ok": True, "room": "mock-voice", "waking": False, "boot": None}
        await page.wait_for_timeout(650)
        await phase(page, "agent")
        assert await page.locator(".hud").get_attribute("data-jstate") == "idle"
        # New generation before old ready decoration would end stays visible.
        desktop.stage("online", fresh=True)
        await phase(page, "ready")
        desktop.stage("connect", fresh=True)
        await phase(page, "relay")
        await page.wait_for_timeout(1400)
        await phase(page, "relay")
        desktop.stage("online")
        await phase(page, "ready")
        await page.wait_for_timeout(1250)
        await phase(page, "idle")
        assert await page.locator(".hud").get_attribute("data-jstate") == "listening"
        # Backend cancel/failure/silence: clearing setup returns standby; a quiet
        # connected room remains established with no perpetual joining overlay.
        desktop.clear()
        await page.wait_for_timeout(400)
        desktop.stage("dispatch", fresh=True)
        await phase(page, "agent")
        desktop.clear()
        await phase(page, "idle")
        desktop.summon_ok = False
        await page.keyboard.press("NumpadEnter")
        await phase(page, "failed")
        assert not await page.locator(".vlink-stages").count(), (
            "Failure still showed a pending stage list"
        )
        await page.screenshot(path=str(folder / "main-link-unavailable.png"))
        desktop.summon_ok = True
        desktop.hang = True
        await page.keyboard.press("NumpadEnter")
        await phase(page, "request")
        await page.wait_for_timeout(10200)
        await phase(page, "stalled")
        await page.screenshot(path=str(folder / "main-stalled-dpr2.png"))
        desktop.hang = False
        prior = desktop.summons
        await page.keyboard.press("NumpadEnter")
        await phase(page, "request")
        assert desktop.summons == prior + 1, "A hanging old request made retry inert"
        desktop.release.set()
        await page.wait_for_timeout(100)
        await phase(page, "request")  # old rejection must not overwrite retry
        desktop.stage("dispatch", fresh=True)
        await phase(page, "agent")
        # Visible CSS motion, hidden pause and reduced-motion semantics.
        moving = await page.locator(".vlink-reticle__orbit").evaluate(
            "n => getComputedStyle(n).animationPlayState"
        )
        assert moving == "running"
        await page.evaluate(
            "() => {Object.defineProperty(document, 'hidden', {configurable:true, value:true}); document.dispatchEvent(new Event('visibilitychange'));}"
        )
        assert (
            await page.locator(".vlink-reticle__orbit").evaluate(
                "n => getComputedStyle(n).animationPlayState"
            )
            == "paused"
        )
        await page.evaluate(
            "() => {delete document.hidden; document.dispatchEvent(new Event('visibilitychange'));}"
        )
        await page.locator(".vlink-reticle").evaluate(
            "n => n.style.transform = 'translateY(4000px)'"
        )
        await page.wait_for_function(
            "document.querySelector('.vlink-reticle').dataset.visible === 'false'"
        )
        assert (
            await page.locator(".vlink-reticle__orbit").evaluate(
                "n => getComputedStyle(n).animationPlayState"
            )
            == "paused"
        )
        await page.locator(".vlink-reticle").evaluate("n => n.style.transform = ''")
        await page.wait_for_function(
            "document.querySelector('.vlink-reticle').dataset.visible === 'true'"
        )
        await page.emulate_media(reduced_motion="reduce")
        assert (
            await page.locator(".vlink-reticle__orbit").evaluate(
                "n => getComputedStyle(n).animationName"
            )
            == "none"
        )
        await page.screenshot(path=str(folder / "main-reduced-motion.png"))
        await page.emulate_media(reduced_motion="no-preference")
        # School uses the exact same summon controller/state while preserving
        # existing menu hit targets. Geometry change is mocked; no live transition.
        desktop.mode = "school"
        await page.set_viewport_size({"width": 1600, "height": 64})
        await page.wait_for_timeout(1600)
        await page.locator(".sbar").wait_for()
        await phase(page, "agent")
        assert await page.locator('.vlink-rail[data-compact="true"]').count() == 1
        await page.screenshot(path=str(folder / "school-agent-link-dpr2.png"))
        await page.evaluate(
            "() => {Object.defineProperty(document, 'hidden', {configurable:true, value:true}); document.dispatchEvent(new Event('visibilitychange'));}"
        )
        assert (
            await page.locator(".vlink-beacon svg").evaluate(
                "n => getComputedStyle(n).animationPlayState"
            )
            == "paused"
        )
        await page.evaluate(
            "() => {delete document.hidden; document.dispatchEvent(new Event('visibilitychange'));}"
        )
        await page.emulate_media(reduced_motion="reduce")
        assert (
            await page.locator(".vlink-beacon svg").evaluate(
                "n => getComputedStyle(n).animationName"
            )
            == "none"
        )
        await page.emulate_media(reduced_motion="no-preference")
        await page.get_by_role("button", name="Calendar", exact=True).click()
        assert (
            await page.get_by_role("button", name="Calendar", exact=True).get_attribute(
                "aria-expanded"
            )
            == "true"
        )
        await page.locator(".smenu").wait_for(state="visible")
        await page.screenshot(path=str(folder / "school-menu-during-link.png"))
        desktop.clear()
        await phase(page, "idle")
        await page.keyboard.press("NumpadEnter")
        await phase(page, "request")
        desktop.stage("online", fresh=True)
        await phase(page, "ready")
        await page.screenshot(path=str(folder / "school-ready-dpr2.png"))
        await page.wait_for_timeout(1250)
        await phase(page, "idle")
        assert not desktop.errors, desktop.errors
        print(
            json.dumps(
                {
                    "ok": True,
                    "scenarios": [
                        "immediate summon",
                        "no duplicate summon",
                        "observed stages",
                        "slow join over15s",
                        "expired pending boot",
                        "generation supersession",
                        "quiet connected room",
                        "backend clear",
                        "local failure",
                        "hung request retry",
                        "obsolete rejection ignored",
                        "hidden pause",
                        "reduced motion",
                        "school continuity",
                        "menu hit targets",
                        "slow-wait explanation without fake progress",
                        "school hidden and reduced-motion pause",
                        "failure replaces pending stage list",
                    ],
                    "dpr": 2,
                    "screenshots": str(folder),
                }
            )
        )
        await context.close()
        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--screenshots", type=Path, default=Path("/tmp/jarvis-voice-link")
    )
    args = parser.parse_args()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SCHOOL.QuietHandler, directory=FRONTEND / "out")
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        asyncio.run(run(f"http://127.0.0.1:{server.server_port}", args.screenshots))
    finally:
        server.shutdown()
        thread.join()
