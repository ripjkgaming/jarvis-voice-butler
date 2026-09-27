import asyncio, subprocess, sys, time
from playwright.async_api import async_playwright
srv = subprocess.Popen([sys.executable, "-m", "http.server", "4711", "--bind", "127.0.0.1"], cwd="frontend/out", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
async def m():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1600, "height": 900})
        cdp = await pg.context.new_cdp_session(pg)
        await cdp.send("Performance.enable")
        await pg.goto("http://127.0.0.1:4711/", wait_until="domcontentloaded"); await pg.wait_for_timeout(5000)
        a = {x["name"]: x["value"] for x in (await cdp.send("Performance.getMetrics"))["metrics"]}
        await pg.wait_for_timeout(10000)
        z = {x["name"]: x["value"] for x in (await cdp.send("Performance.getMetrics"))["metrics"]}
        anims = await pg.evaluate("document.getAnimations().length")
        canv = await pg.evaluate("[...document.querySelectorAll('canvas')].map(c=>c.width+'x'+c.height).join(',')")
        print(f"heap={z['JSHeapUsedSize']/1e6:.1f}MB nodes={int(z['Nodes'])} animations={anims} canvases={canv} cpu_10s={(z['TaskDuration']-a['TaskDuration'])*100:.1f}% script_10s={(z['ScriptDuration']-a['ScriptDuration'])*1000:.0f}ms layout={int(z['LayoutCount']-a['LayoutCount'])} recalc={int(z['RecalcStyleCount']-a['RecalcStyleCount'])}")
        await b.close()
try: asyncio.run(m())
finally: srv.terminate()
