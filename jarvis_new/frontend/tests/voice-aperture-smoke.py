"""Bounded, isolated voice startup UI checks; all bridge/native actions mocked.
No video, real microphone, live call, or user window interaction.
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

from playwright.async_api import async_playwright

FRONTEND = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "voice_smoke", Path(__file__).with_name("voice-link-smoke.py")
)
VOICE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(VOICE)


async def run(url, folder):
    folder.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--disable-gpu"])
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720}, device_scale_factor=1
        )
        page = await context.new_page()
        desktop = VOICE.VoiceDesktop(page)
        await desktop.install()
        await page.add_init_script("""
        const api=window.__TAURI_INTERNALS__; const original=api.invoke;
        const callbacks=new Map(); let counter=0;
        api.transformCallback=fn=>{callbacks.set(++counter,fn);return counter;};
        api.unregisterCallback=id=>callbacks.delete(id);
        api.invoke=(cmd,args)=>{
          if(cmd==='plugin:event|listen'){window.nativeTalkHandler=args.handler;return Promise.resolve(1);}
          if(cmd==='plugin:event|unlisten')return Promise.resolve();
          return original(cmd,args);
        };
        window.fireNativeTalk=()=>callbacks.get(window.nativeTalkHandler)?.();
        """)
        await page.goto(url)
        await desktop.ready()
        await VOICE.phase(page, "idle")
        await page.evaluate("window.fireNativeTalk()")
        await page.locator(".vlink-wake[data-activation]").wait_for()
        await page.wait_for_timeout(200)
        await page.screenshot(path=str(folder / "native-wake-activation.png"))
        desktop.muted = True
        await page.evaluate("window.dispatchEvent(new Event('focus'))")
        await VOICE.phase(page, "idle")
        await page.wait_for_timeout(1100)
        assert not await page.locator(".vlink-wake[data-activation]").count()
        desktop.muted = False
        await page.evaluate("window.dispatchEvent(new Event('focus'))")
        await page.get_by_text("MIC ON", exact=True).wait_for()
        await page.keyboard.press("NumpadEnter")
        await VOICE.phase(page, "request")
        orbit = page.locator(".vlink-reticle__orbit")
        await page.evaluate(
            "window.voiceAnimation = document.querySelector('.vlink-reticle__orbit').getAnimations()[0]"
        )
        assert (
            await orbit.evaluate("n => getComputedStyle(n).animationDuration") == "5s"
        )
        await page.wait_for_timeout(900)
        desktop.stage("connect", fresh=True)
        await VOICE.phase(page, "relay")
        assert await page.evaluate(
            "window.voiceAnimation === document.querySelector('.vlink-reticle__orbit').getAnimations()[0]"
        ), "Stage restarted cycle"
        assert (
            await page.locator("[data-reactor-energy]").evaluate(
                "n => getComputedStyle(n).filter"
            )
            == "none"
        )
        await page.wait_for_function("getComputedStyle(document.querySelector('[data-reactor-energy]')).opacity === '0'")
        assert await page.locator("canvas[data-ambient-running]").evaluate_all("nodes => nodes.every(n => n.dataset.ambientRunning === 'false')")
        assert await page.locator('.vlink-reticle__channels li').evaluate_all("nodes => nodes.map(n => n.dataset.stage)") == ['done', 'current', 'waiting']
        await page.screenshot(path=str(folder / "blue-acquisition-desktop.png"))
        # A full cycle never advances the semantic connection state.
        await page.wait_for_timeout(4400)
        await VOICE.phase(page, "relay")
        desktop.stage("dispatch")
        await VOICE.phase(page, "agent")
        await page.set_viewport_size({"width": 960, "height": 540})
        await page.wait_for_timeout(350)
        await page.screenshot(path=str(folder / "blue-acquisition-narrow.png"))
        box = await page.locator(".vlink-reticle__identity").bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 960
        # Native hide/modal coverage and reduced motion stop every large loop.
        for cls in ["hud-hidden", "suit-open", "school-tx"]:
            await page.evaluate("(c) => document.documentElement.classList.add(c)", cls)
            assert (
                await orbit.evaluate("n => getComputedStyle(n).animationPlayState")
                == "paused"
            )
            await page.evaluate(
                "(c) => document.documentElement.classList.remove(c)", cls
            )
        await page.emulate_media(reduced_motion="reduce")
        for selector in [
            ".vlink-reticle__orbit",
            ".vlink-reticle__counter",
            ".vlink-reticle__scan",
            ".vlink-reticle__pulse",
        ]:
            assert (
                await page.locator(selector).evaluate(
                    "n => getComputedStyle(n).animationName"
                )
                == "none"
            )
        assert (
            await page.locator(".vlink-reticle__scan").evaluate(
                "n => getComputedStyle(n).opacity"
            )
            == "0"
        )
        await page.screenshot(path=str(folder / "blue-acquisition-reduced.png"))
        await page.emulate_media(reduced_motion="no-preference")
        # Readiness is immediate; the ornament crossfades without resetting pose.
        desktop.stage("online")
        await page.wait_for_function("""() => {
          const n=document.querySelector('.vlink-reticle__orbit');
          const p=document.querySelector('[data-voice-phase]')?.dataset.voicePhase;
          const s=n && getComputedStyle(n);
          return (p==='ready' && s.animationName==='vlink-assemble' && getComputedStyle(document.querySelector('.vlink-reticle')).opacity==='0') ||
                 (p==='idle' && s.animationPlayState==='paused');
        }""")
        assert (
            await page.locator(".vlink-reticle__veil").evaluate(
                "n => getComputedStyle(n).display === 'none' || getComputedStyle(n.parentElement).opacity === '0'"
            )
            is True
        )
        assert (
            await page.locator(".vlink-reticle__scan").evaluate(
                "n => getComputedStyle(n).display === 'none' || getComputedStyle(n.parentElement).opacity === '0'"
            )
            is True
        )
        assert await page.locator("[data-reactor-energy]").evaluate("n => getComputedStyle(n).display") != "none"
        await page.screenshot(path=str(folder / "blue-link-ready.png"))
        await page.wait_for_timeout(1200)
        await VOICE.phase(page, "idle")
        wake_stamp = time.time()
        await page.route(
            "http://bridge.invalid/captions?limit=3",
            lambda route: route.fulfill(
                json={
                    "ok": True,
                    "captions": [
                        {"ts": wake_stamp, "role": "sir", "text": "Hey Jarvis"}
                    ],
                },
                headers={"Access-Control-Allow-Origin": "*"},
            ),
        )
        await page.locator(".vlink-wake[data-activation]").wait_for()
        await VOICE.phase(page, "idle")
        await page.wait_for_timeout(200)
        await page.screenshot(path=str(folder / "connected-wake-activation.png"))
        await page.unroute("http://bridge.invalid/captions?limit=3")
        await page.wait_for_timeout(1200)
        assert not await page.locator(".vlink-wake[data-activation]").count()

        desktop.clear()
        await VOICE.phase(page, "idle")
        desktop.stage("dispatch", fresh=True)
        await VOICE.phase(page, "agent")
        desktop.clear()
        await VOICE.phase(page, "failed")
        assert await page.locator(".vlink-rail").get_by_text("LINK INTERRUPTED", exact=True).count() == 1
        await page.wait_for_timeout(300)
        await page.screenshot(path=str(folder / "blue-link-interrupted.png"))
        before = desktop.summons
        await page.get_by_role("button", name="RETRY").click()
        await VOICE.phase(page, "request")
        assert desktop.summons == before + 1
        await page.keyboard.press("NumpadEnter")
        assert desktop.summons == before + 1
        # A rejected retry exposes its failure, then supports another click.
        desktop.stage("dispatch", fresh=True)
        await VOICE.phase(page, "agent")
        desktop.clear()
        await VOICE.phase(page, "failed")
        desktop.summon_ok = False
        await page.get_by_role("button", name="RETRY").click()
        await page.locator(".vlink-rail").get_by_text("LINK UNAVAILABLE", exact=True).wait_for()
        # Existing compact strip inherits status and retry without a large overlay.
        desktop.mode = "school"
        await page.set_viewport_size({"width": 1280, "height": 84})
        await page.evaluate("window.dispatchEvent(new Event('focus'))")
        await page.locator('.vlink-rail[data-compact="true"]').wait_for(timeout=10000)
        await page.wait_for_timeout(1600)
        await page.screenshot(path=str(folder / "blue-link-school-retry.png"))
        assert await page.get_by_role("button", name="RETRY").is_visible()
        desktop.summon_ok = True
        await page.get_by_role("button", name="RETRY").click()
        await VOICE.phase(page, "request")
        assert not desktop.errors, desktop.errors
        await context.close()
        await browser.close()
        print(
            json.dumps(
                {
                    "passed": True,
                    "checks": [
                        "continuous 5s cycle",
                        "no timed readiness",
                        "no reactor filter",
                        "desktop and narrow",
                        "hidden/modal/transition pause",
                        "dynamic reduced motion",
                        "immediate ready",
                        "interrupted join",
                        "click retry",
                        "duplicate prevention",
                        "local failure",
                        "compact retry",
                    ],
                    "screenshots": str(folder),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--screenshots", type=Path, default=Path("/tmp/jarvis-blue-aperture")
    )
    args = parser.parse_args()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(VOICE.SCHOOL.QuietHandler, directory=FRONTEND / "out")
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        asyncio.run(run(f"http://127.0.0.1:{server.server_port}", args.screenshots))
    finally:
        server.shutdown()
        thread.join()
