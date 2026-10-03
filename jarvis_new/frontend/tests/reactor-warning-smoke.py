"""Mocked amber light scenarios against the static production export."""
import asyncio
import importlib.util
import json
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse
from playwright.async_api import async_playwright

spec = importlib.util.spec_from_file_location('voice', Path(__file__).with_name('voice-link-smoke.py'))
voice = importlib.util.module_from_spec(spec)
spec.loader.exec_module(voice)

class Desktop(voice.VoiceDesktop):
    def __init__(self, page):
        super().__init__(page)
        self.patch = {}
    async def bridge(self, route):
        if urlparse(route.request.url).path == '/sys':
            data = {'ok': True, 'windows': [], 'load_1_5_15':[0.6,0.5,0.4], 'cpu_count':8,
                'mem_bytes':{'MemTotal':16000000000,'MemAvailable':10000000000},
                'net':{'kind':'wifi','name':'Lab','signal':80}, 'volume':{'pct':45,'muted':False},
                'cpu_temp_c':49, 'laptop_power':{'battery':81,'ac':False}, **self.patch}
            return await route.fulfill(json=data, headers={'Access-Control-Allow-Origin':'*'})
        return await super().bridge(route)

async def run(url):
    folder=Path('/tmp/jarvis-reactor-warning'); folder.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        page=await browser.new_page(viewport=voice.SCHOOL.NORMAL,device_scale_factor=1)
        desk=Desktop(page); await desk.install(); await page.goto(url); await desk.ready()
        light=page.locator('[data-reactor-warning]')
        async def capture(name, sources):
            await page.wait_for_function("s => document.querySelector('[data-reactor-warning]')?.dataset.warningSources === s",arg=sources,timeout=8000)
            await page.wait_for_timeout(1000)
            await page.screenshot(path=str(folder/f'{name}.png'))
            return await light.evaluate('(n) => ({opacity:getComputedStyle(n).opacity,sources:n.dataset.warningSources,transition:getComputedStyle(n).transitionDuration})')
        results={}
        results['normal']=await capture('normal','')
        desk.patch={'load_1_5_15':[7.6,0,0]}
        results['cpu']=await capture('cpu-hot','cpu')
        assert results['cpu']['opacity']=='1'
        assert await page.locator('[data-source=cpu]').get_attribute('data-on')=='true'
        desk.patch={'cpu_temp_c':90,'laptop_power':{'battery':12,'ac':False}}
        results['warning']=await capture('temperature-power','temp power')
        desk.stage('connect',fresh=True)
        await voice.phase(page,'relay')
        await page.wait_for_timeout(3400)
        results['join']=await capture('warning-during-join','temp power')
        assert '66deg' in await page.locator('[data-reactor-energy]').evaluate('n=>getComputedStyle(n).filter')
        await page.emulate_media(reduced_motion='reduce')
        results['reduced']=await capture('reduced-motion','temp power')
        assert results['reduced']['transition']=='0s'
        desk.patch={}; desk.clear()
        results['recovered']=await capture('recovered','')
        assert results['recovered']['opacity']=='0'
        assert not desk.errors,desk.errors
        (folder/'results.json').write_text(json.dumps(results,indent=2))
        print(json.dumps(results))
        await browser.close()

if __name__=='__main__':
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(voice.SCHOOL.QuietHandler,directory=voice.FRONTEND/'out'))
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try: asyncio.run(run(f'http://127.0.0.1:{server.server_port}'))
    finally: server.shutdown();thread.join()
