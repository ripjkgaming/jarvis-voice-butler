"""Offline production DOM AND canvas colour regression; native/bridge calls mocked.
No live mic, wake word, voice call, or production mode changes.
"""

import asyncio
import importlib.util
import json
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "voice", ROOT / "frontend/tests/voice-link-smoke.py"
)
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)
FOLDER = Path("/tmp/jarvis-colour-state-evidence")
FOLDER.mkdir(exist_ok=True)


class Desktop(v.VoiceDesktop):
    failure = False

    def __init__(self, page):
        super().__init__(page)
        self.bar = 56  # actual school-strip geometry, rather than generic smoke default

    async def bridge(self, route):
        if self.failure and urlparse(route.request.url).path == "/room":
            return await route.fulfill(
                status=503,
                json={"ok": False},
                headers={"Access-Control-Allow-Origin": "*"},
            )
        return await super().bridge(route)

    def live(self, state="listening", owner="new"):
        self.room = {
            "ok": True,
            "room": "mock-voice",
            "call_live": True,
            "agent_state": state,
            "voice_owner": owner,
            "waking": False,
            "boot": None,
        }


# Read rendered pixels, not just the CSS variable (baked canvases can retain old ink).
PIXELS = """() => {
 const root=document.querySelector('.sbar')||document.querySelector('.hud');
 const c=document.querySelector('.sbar-trace canvas')||document.querySelector('canvas[data-ascii-reactor]')||document.querySelector('canvas[class*="pxPlasma"]');
 if(!c)return {state:root.dataset.state||root.dataset.jstate,color:getComputedStyle(root).getPropertyValue(root.classList.contains('sbar')?'--sbar-accent':'--state').trim(),counts:null,height:root.getBoundingClientRect().height};
 const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
 const counts={purple:0,green:0,cyan:0,neutral:0};
 for(let i=0;i<d.length;i+=4){if(d[i+3]<32)continue;const r=d[i]/255,g=d[i+1]/255,b=d[i+2]/255;
  const max=Math.max(r,g,b),min=Math.min(r,g,b),delta=max-min;
  if(delta<.12){counts.neutral++;continue;}
  let h=60*(max===r?((g-b)/delta)%6:max===g?(b-r)/delta+2:(r-g)/delta+4);if(h<0)h+=360;
  if(h>255&&h<315)counts.purple++;else if(h>105&&h<165)counts.green++;else if(h>170&&h<215)counts.cyan++;
 }
 return {state:root.dataset.state||root.dataset.jstate,color:getComputedStyle(root).getPropertyValue(root.classList.contains('sbar')?'--sbar-accent':'--state').trim(),counts,height:root.getBoundingClientRect().height};
}"""


async def run(url):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport=v.SCHOOL.NORMAL)
        desktop = Desktop(page)
        await desktop.install()
        await page.goto(url)
        await desktop.ready()
        rows = []

        async def check(mode, step, state, hue):
            await page.wait_for_function(
                "s => {const n=document.querySelector('.sbar')||document.querySelector('.hud'); return (n.dataset.state||n.dataset.jstate)===s}",
                arg=state,
            )
            await page.wait_for_timeout(850)
            row = await page.evaluate(PIXELS)
            expected = {
                "purple": "#a855f7",
                "green": "#22c55e",
                "cyan": "#22d3ee",
                "neutral": "#6b7280",
            }[hue]
            rgb = (
                "rgb("
                + ", ".join(str(int(expected[i : i + 2], 16)) for i in (1, 3, 5))
                + ")"
            )
            assert row["color"] in (expected, rgb), row
            if hue != "neutral" and row["counts"] is not None:
                assert row["counts"][hue] > 20, row
                assert all(
                    row["counts"][hue] > row["counts"][other]
                    for other in ("purple", "green", "cyan")
                    if other != hue
                ), row
            if mode == "school":
                assert row["height"] == 56, row
            if row["counts"] is None:
                assert step == "waiting-for-agent", (step, row)
            row.update(mode=mode, step=step)
            rows.append(row)

        for mode in ("normal", "school"):
            if mode == "school":
                desktop.live("speaking")
                await desktop.signal("collapse")
                await desktop.settled(True)
                await check(mode, "active-call-during-entry", "speaking", "purple")
            desktop.clear()
            await check(mode, "no-call", "idle", "cyan")
            desktop.stage("dispatch", fresh=True)
            await check(mode, "waiting-for-agent", "idle", "cyan")
            desktop.live()
            await check(mode, "quiet-active-call", "listening", "purple")
            desktop.live("speaking")
            await check(mode, "speech-start", "speaking", "purple")
            await page.wait_for_timeout(3300)
            await check(mode, "speech-survives-unchanged-polls", "speaking", "purple")
            desktop.live("thinking")
            await check(mode, "execution-overlapping-speech", "thinking", "green")
            await page.screenshot(path=str(FOLDER / f"{mode}-executing.png"))
            desktop.live()
            await check(
                mode, "interruption-and-tool-error-return", "listening", "purple"
            )
            desktop.room["call_live"] = False  # stale room file survives terminal close
            await check(mode, "closed-with-room-file", "idle", "cyan")
            desktop.live("thinking")
            await check(mode, "new-session", "thinking", "green")
            desktop.failure = True
            await page.wait_for_timeout(3200)
            await check(mode, "bridge-stall-expires-cache", "idle", "cyan")
            desktop.failure = False
            desktop.live("speaking", "reconnected")
            await check(mode, "reconnected", "speaking", "purple")
            await page.keyboard.press("m")
            # School reports muted as state; normal retains semantic state with data-muted.
            await page.wait_for_function(
                "() => document.querySelector('.sbar')?.dataset.state === 'muted' || document.querySelector('.hud')?.dataset.muted === 'true'"
            )
            await check(
                mode, "muted", "muted" if mode == "school" else "speaking", "neutral"
            )
            await page.keyboard.press("m")
            await check(mode, "unmuted-active-call", "speaking", "purple")
            await page.screenshot(path=str(FOLDER / f"{mode}-active.png"))
            desktop.clear()
            await check(mode, "call-end", "idle", "cyan")
            await page.screenshot(path=str(FOLDER / f"{mode}-idle.png"))
        desktop.live("thinking")
        await desktop.signal("return")
        await desktop.settled(False)
        await check("normal", "return-during-execution", "thinking", "green")
        assert not desktop.errors, desktop.errors
        await browser.close()
        (FOLDER / "render-states.json").write_text(json.dumps(rows, indent=2))
        print(
            json.dumps(
                {
                    "checks": len(rows),
                    "modes": ["normal", "school"],
                    "canvas_pixels_verified": True,
                    "evidence": str(FOLDER),
                }
            )
        )


server = ThreadingHTTPServer(
    ("127.0.0.1", 0),
    partial(v.SCHOOL.QuietHandler, directory=str(ROOT / "frontend/out")),
)
Thread(target=server.serve_forever, daemon=True).start()
try:
    asyncio.run(run(f"http://127.0.0.1:{server.server_port}"))
finally:
    server.shutdown()
