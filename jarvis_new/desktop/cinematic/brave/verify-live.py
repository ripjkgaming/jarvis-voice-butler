"""Check the installed Brave extension using only owned fixture tabs.

Requires the existing DevTools endpoint at 127.0.0.1:9222 and websockets.
Run from jarvis_new with uv run python desktop/cinematic/brave/verify-live.py.
"""
import asyncio, base64, json, os, threading, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import websockets

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parent / 'validation'
class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<!doctype html><title>Motion test channel</title><body style="background:#eef4fa;font:20px sans-serif;padding:80px"><h1>Browser motion test</h1><button id="probe" onclick="this.textContent=\'Clicked\'">Input remains available</button></body>'
        self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers(); self.wfile.write(body)
    def log_message(self, *args): pass

async def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}/'
    version = json.load(urllib.request.urlopen(f"http://127.0.0.1:{os.environ.get('JARVIS_BRAVE_DEBUG_PORT', '9222')}/json/version"))
    async with websockets.connect(version['webSocketDebuggerUrl'], max_size=20_000_000) as ws:
        serial = 0
        errors = []
        async def call(method, params=None, session=None):
            nonlocal serial
            serial += 1; number = serial
            payload = dict(id=number, method=method, params=params or {})
            if session: payload['sessionId'] = session
            await ws.send(json.dumps(payload))
            while True:
                reply = json.loads(await ws.recv())
                if reply.get('method') == 'Runtime.exceptionThrown': errors.append(reply['params']['exceptionDetails'].get('text'))
                if reply.get('id') == number:
                    if 'error' in reply: raise RuntimeError(reply['error'])
                    return reply.get('result', {})
        async def evaluate(code, session):
            result = await call('Runtime.evaluate', {'expression': code, 'awaitPromise': True, 'returnByValue': True}, session)
            if 'exceptionDetails' in result: raise RuntimeError(result['exceptionDetails'])
            return result.get('result', {}).get('value')
        async def attach(target):
            session = (await call('Target.attachToTarget', {'targetId': target, 'flatten': True}))['sessionId']
            await call('Runtime.enable', session=session)
            return session
        installed = (await call('Extensions.getExtensions'))['extensions']
        extension = next((item['id'] for item in installed if item['path'] == str(ROOT / 'extension') and item['enabled']), None)
        if extension is None:
            raise RuntimeError('Install the extension through Brave’s Load unpacked flow before running verification.')
        print('Verifying installed extension', extension, flush=True)
        target = (await call('Target.createTarget', {'url': 'brave://newtab/', 'newWindow': True}))['targetId']
        session = await attach(target)
        await call('Page.bringToFront', session=session)
        owned = []
        old_setting = None
        try:
            await asyncio.sleep(1.5)
            assert await evaluate("document.body.classList.contains('motion-ready')", session)
            old_setting = await evaluate("chrome.storage.local.get('jarvis-quiet')", session)
            await evaluate("chrome.storage.local.set({'jarvis-quiet':false})", session)
            owned = await evaluate(f"Promise.all([chrome.tabs.create({{url:{json.dumps(url+'a')},active:false}}),chrome.tabs.create({{url:{json.dumps(url+'b')},active:false}})]).then(t=>t.map(x=>x.id))", session)
            await asyncio.sleep(.8)
            assert await evaluate("document.querySelectorAll('.tab').length", session) == 2
            assert await evaluate("document.getElementById('status').textContent", session) == ''
            first, second = owned
            selector = f'.tab[data-id="{first}"]'
            await evaluate(f"document.querySelector('{selector} .pin').click()", session)
            await asyncio.sleep(.25)
            assert await evaluate(f"chrome.tabs.get({first}).then(t=>t.pinned)", session)
            assert await evaluate(f"document.querySelector('{selector} .pin').getAttribute('aria-pressed')", session) == 'true'
            native = await evaluate("chrome.runtime.sendMessage({jarvisNativeStatus:true})", session)
            assert native.get('ready'), native
            assert native.get('visible') and native.get('inputTransparent'), native
            assert await evaluate('chrome.windows.getCurrent().then(w=>w.focused)', session), 'Native overlay stole focus'
            await evaluate(f"document.querySelector('{selector} .mute').click()", session)
            await asyncio.sleep(.2)
            assert await evaluate(f"chrome.tabs.get({first}).then(t=>t.mutedInfo.muted)", session)
            await evaluate(f"document.querySelector('{selector} .reload').click()", session)
            await asyncio.sleep(.5)
            # Save only this isolated window with our fixture titles.
            shot = await call('Page.captureScreenshot', {'format':'png'}, session)
            (OUT / 'brave-motion-controls.png').write_bytes(base64.b64decode(shot['data']))
            await call('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':False}, session)
            await asyncio.sleep(.15)
            assert await evaluate('document.documentElement.scrollWidth <= innerWidth', session)
            assert await evaluate("[...document.querySelectorAll('.tab button')].every(b=>b.getBoundingClientRect().right<=innerWidth)", session)
            shot = await call('Page.captureScreenshot', {'format':'png'}, session)
            (OUT / 'brave-motion-narrow.png').write_bytes(base64.b64decode(shot['data']))
            await call('Emulation.clearDeviceMetricsOverride', session=session)
            await evaluate(f"document.querySelector('{selector} .close').click()", session)
            await asyncio.sleep(.15)
            assert await evaluate(f"chrome.tabs.get({first}).then(()=>true,()=>false)", session), 'Closed before dissolve'
            await asyncio.sleep(.8)
            assert not await evaluate(f"chrome.tabs.get({first}).then(()=>true,()=>false)", session)
            native_close = await evaluate("chrome.runtime.sendMessage({jarvisNativeStatus:true})", session)
            assert native_close.get('visible') and native_close.get('kind') == 'close', native_close
            owned.remove(first)
            assert await evaluate(f"chrome.tabs.get({second}).then(()=>true,()=>false)", session)
            await asyncio.sleep(.2)
            assert await evaluate("document.querySelectorAll('.tab').length", session) == 1
            # Exercise the real content script and worker in the surviving HTTP test tab.
            targets = (await call('Target.getTargets'))['targetInfos']
            page_target = next(t['targetId'] for t in targets if t.get('url') == url+'b')
            page = await attach(page_target)
            await evaluate(f"chrome.tabs.update({second},{{active:true}})", session)
            await asyncio.sleep(.18)
            cue = await evaluate("document.querySelector('[data-jarvis-motion]')?.dataset.jarvisMotion", page)
            assert cue == 'switch', cue
            assert await evaluate("getComputedStyle(document.querySelector('[data-jarvis-motion]')).pointerEvents", page) == 'none'
            await evaluate("document.getElementById('probe').click()", page)
            assert await evaluate("document.getElementById('probe').textContent", page) == 'Clicked'
            await asyncio.sleep(1.2)
            assert await evaluate("document.querySelectorAll('[data-jarvis-motion]').length", page) == 0
            await evaluate(f"chrome.tabs.update({second},{{pinned:true}})", session)
            await asyncio.sleep(.16)
            assert await evaluate("document.querySelector('[data-jarvis-motion]')?.dataset.jarvisMotion", page) == 'pin'
            await evaluate("chrome.storage.local.set({'jarvis-quiet':true})", session)
            await asyncio.sleep(.12)
            assert await evaluate("document.querySelectorAll('[data-jarvis-motion]').length", page) == 0
            assert not (await evaluate("chrome.runtime.sendMessage({jarvisNativeStatus:true})", session)).get('visible')
            await evaluate(f"chrome.tabs.update({second},{{muted:true}})", session)
            await asyncio.sleep(.16)
            assert await evaluate("document.querySelectorAll('[data-jarvis-motion]').length", page) == 0
            await evaluate("chrome.storage.local.set({'jarvis-quiet':false})", session)
            await call('Emulation.setEmulatedMedia', {'features':[{'name':'prefers-reduced-motion','value':'reduce'}]}, page)
            await evaluate(f"chrome.tabs.setZoom({second},1.1)", session)
            await asyncio.sleep(.16)
            assert await evaluate("document.querySelectorAll('[data-jarvis-motion]').length", page) == 0
            assert not (await evaluate("chrome.runtime.sendMessage({jarvisNativeStatus:true})", session)).get('visible')
            # Reduced-motion close is immediate in the new-tab manager.
            await evaluate('chrome.tabs.getCurrent().then(t=>chrome.tabs.update(t.id,{active:true}))', session)
            await call('Emulation.setEmulatedMedia', {'features':[{'name':'prefers-reduced-motion','value':'reduce'}]}, session)
            await evaluate(f"document.querySelector('.tab[data-id=\"{second}\"] .close').click()", session)
            await asyncio.sleep(.15)
            assert not await evaluate(f"chrome.tabs.get({second}).then(()=>true,()=>false)", session)
            owned.remove(second)
            assert not errors, errors
            report = dict(extension=extension,version='1.2.0',checks=['pin','mute','reload','390px layout','600ms close retains target until animation ends','only requested tab closes','switch edge cue','input transparent','finite cleanup','global motion off','OS reduced motion','immediate reduced-motion close'],runtimeErrors=errors,nativeBar=native,nativeClose=native_close)
            (OUT / 'brave-motion-validation.json').write_text(json.dumps(report,indent=2)+'\n')
            print(json.dumps(report, indent=2), flush=True)
        finally:
            if old_setting is not None:
                try:
                    await evaluate('chrome.storage.local.set('+json.dumps(old_setting)+')', session)
                except Exception as error:
                    print('Could not restore motion preference:', error)
            if owned:
                try: await evaluate(f'chrome.tabs.remove({json.dumps(owned)})', session)
                except Exception: pass
            await call('Target.closeTarget', {'targetId':target})
            server.shutdown()

asyncio.run(main())
