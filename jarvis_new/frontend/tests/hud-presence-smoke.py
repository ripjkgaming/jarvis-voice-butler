"""Exercise the shipped HUD intro/presence lifecycle against mocked native I/O.

uv run --no-sync python frontend/tests/hud-presence-smoke.py
"""

import asyncio
import importlib.util
import json
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.async_api import async_playwright

SPEC = importlib.util.spec_from_file_location(
    "voice_smoke", Path(__file__).with_name("voice-link-smoke.py")
)
VOICE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VOICE)


async def run(url):
    folder = Path("/tmp/jarvis-presence-review")
    folder.mkdir(exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1920, "height": 983})
        desktop = VOICE.VoiceDesktop(page)
        await desktop.install()
        await page.add_init_script("""(() => {
            const callbacks = new Map(), events = new Map(); let next = 0;
            const api = window.__TAURI_INTERNALS__, invoke = api.invoke;
            api.transformCallback = fn => { callbacks.set(++next, fn); return next; };
            api.unregisterCallback = id => callbacks.delete(id);
            api.invoke = (cmd, args) => cmd === 'plugin:event|listen'
                ? (events.set(args.handler, args.event), Promise.resolve(args.handler))
                : cmd === 'plugin:event|unlisten' ? Promise.resolve() : invoke(cmd, args);
            window.mockTalk = () => {
                callbacks.forEach((fn, id) => {
                    if (events.get(id) === 'jarvis-talk') fn({payload:null});
                });
                return [...events.values()];
            };
            const now = Date.now;
            window.idleOffset = 0;
            Date.now = () => now() + window.idleOffset;
        })();""")
        await page.goto(url)
        wallpaper = Path("/home/ripjk/deck/assets/locke-wall-ascii.png")
        if wallpaper.exists():
            await page.route(
                "**/__presence-wallpaper.png",
                lambda route: route.fulfill(path=wallpaper),
            )
            await page.add_style_tag(
                content="html { background: url('/__presence-wallpaper.png') center / cover fixed !important; }"
            )
        await desktop.ready()
        await page.wait_for_selector('[data-ix-replica][data-ready="true"]')
        await page.wait_for_function(
            "document.querySelector('[data-intro]')?.dataset.intro === 'done'",
            timeout=15000,
        )

        async def state(value, timeout=15000):
            try:
                await page.wait_for_function(
                    "s => window.__jarvisPresence?.state === s",
                    arg=value,
                    timeout=timeout,
                )
            except Exception:
                print(
                    await page.evaluate(
                        "({state:window.__jarvisPresence?.state, intro:document.querySelector('[data-intro]')?.dataset.intro})"
                    ),
                    desktop.errors,
                    flush=True,
                )
                raise

        async def snapshot(name):
            await page.screenshot(path=str(folder / f"{name}.png"))

        async def idle():
            await page.evaluate("window.idleOffset += 26000")
            await state("fading", 3000)

        await snapshot("loaded")
        # Natural inactivity starts a fade; a show while fading reverses it.
        await idle()
        await page.wait_for_timeout(2200)
        await snapshot("fading")
        await page.evaluate("window.dispatchEvent(new Event('jarvis-hud-show'))")
        await state("present", 4000)
        assert not await page.locator("[data-ix='empty']").count()

        # Fully gone is paused and click-through. Native show reuses the intro.
        await idle()
        await state("gone")
        assert await page.evaluate(
            "document.documentElement.hasAttribute('data-hud-gone')"
        )
        assert await page.evaluate(
            "document.documentElement.classList.contains('hud-hidden')"
        )
        assert ("set_overlay_click_through", {"ignore": True}) in desktop.calls
        await snapshot("gone")
        await page.evaluate("window.dispatchEvent(new Event('jarvis-hud-show'))")
        await state("appearing", 2000)
        await page.wait_for_selector("[data-ix='empty']", timeout=2000)
        await snapshot("wake")
        await state("present")
        assert not await page.evaluate(
            "document.documentElement.hasAttribute('data-hud-gone')"
        )
        assert desktop.summons == 0, "Showing the HUD started a voice call"
        assert ("set_overlay_click_through", {"ignore": False}) in desktop.calls

        # P persists the opt-out; inactivity cannot fade while it is off.
        await page.keyboard.press("p")
        assert (
            await page.evaluate("localStorage.getItem('jarvis.presence.enabled')")
            == "0"
        )
        await page.evaluate("window.idleOffset += 60000")
        await page.wait_for_timeout(1300)
        await state("present", 1000)
        await page.keyboard.press("p")
        await idle()
        await state("gone")
        print("Talk listeners:", await page.evaluate("window.mockTalk()"), flush=True)
        await state("appearing", 2000)
        await state("present")

        # A new voice link also wakes the HUD before the call becomes ready.
        await idle()
        await state("gone")
        desktop.room = {"ok": True, "room": None, "waking": True, "boot": None}
        await state("appearing", 4000)
        await state("present")
        await snapshot("voice-wake")
        assert not desktop.errors, desktop.errors
        result = {
            "checks": [
                "wallpaper asset",
                "boot intro",
                "idle fade",
                "partial reversal",
                "native show wake",
                "click-through",
                "paused when gone",
                "P toggle",
                "talk event wake",
                "voice link wake",
            ],
            "errors": desktop.errors,
            "alignment": await page.evaluate("window.__jarvisAlign?.check()"),
        }
        (folder / "results.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
        await browser.close()


if __name__ == "__main__":
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(VOICE.SCHOOL.QuietHandler, directory=VOICE.FRONTEND / "out"),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        asyncio.run(run(f"http://127.0.0.1:{server.server_port}"))
    finally:
        server.shutdown()
        thread.join()
