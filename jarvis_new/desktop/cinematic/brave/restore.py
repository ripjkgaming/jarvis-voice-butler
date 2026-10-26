"""Remove only this task's two unpacked Brave extensions through local DevTools.

Run with the project's uv environment while Brave's existing port 9222 is open.
"""
import asyncio
import json
import urllib.request
from pathlib import Path

import websockets


async def main():
    version = json.load(urllib.request.urlopen('http://127.0.0.1:9222/json/version', timeout=2))
    async with websockets.connect(version['webSocketDebuggerUrl']) as socket:
        serial = 0

        async def call(method, params=None):
            nonlocal serial
            serial += 1
            await socket.send(json.dumps(dict(id=serial, method=method, params=params or {})))
            while True:
                reply = json.loads(await socket.recv())
                if reply.get('id') == serial:
                    if 'error' in reply:
                        raise RuntimeError(reply['error'])
                    return reply['result']

        installed = (await call('Extensions.getExtensions'))['extensions']
        root = Path(__file__).resolve().parent
        for folder in ('extension', 'theme'):
            expected = str(root / folder)
            for extension in installed:
                if extension['path'] == expected:
                    await call('Extensions.uninstall', {'id': extension['id']})
                    print('Removed ' + extension['name'])
        print('Other extensions and browser tabs were preserved.')


if __name__ == '__main__':
    asyncio.run(main())
