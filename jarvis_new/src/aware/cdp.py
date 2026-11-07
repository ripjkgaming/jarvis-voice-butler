"""Tiny synchronous CDP helpers over 127.0.0.1 (no Playwright needed).

Read-only uses: /json/list and /json/version, and one Runtime.evaluate
per call. Each call opens and closes its own websocket. Never used to
navigate, reload or close one of Sir's tabs.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request

DEFAULT_PORT = 9222


def port() -> int:
    try:
        return int(os.environ.get("JARVIS_BRAVE_CDP_PORT", DEFAULT_PORT))
    except ValueError:
        return DEFAULT_PORT


def _get(path: str, cdp_port: int | None = None, timeout: float = 3.0):
    url = f"http://127.0.0.1:{cdp_port or port()}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf8", "replace"))


def targets(cdp_port: int | None = None, timeout: float = 3.0) -> list[dict]:
    data = _get("/json/list", cdp_port, timeout)
    return [t for t in data if isinstance(t, dict)] if isinstance(data, list) else []


def version(cdp_port: int | None = None, timeout: float = 2.0) -> dict | None:
    try:
        data = _get("/json/version", cdp_port, timeout)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


async def _call(ws_url: str, method: str, params: dict, timeout: float) -> dict:
    import websockets

    async with websockets.connect(
        ws_url, max_size=8_000_000, open_timeout=timeout
    ) as ws:
        await ws.send(json.dumps({"id": 1, "method": method, "params": params}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            if msg.get("id") == 1:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"])[:200])
                return msg.get("result") or {}


def call(ws_url: str, method: str, params: dict | None = None, timeout: float = 5.0):
    return asyncio.run(_call(ws_url, method, params or {}, timeout))


def evaluate(ws_url: str, expression: str, timeout: float = 5.0, await_promise=False):
    """Runtime.evaluate returning the value; raises on an exception."""
    result = call(
        ws_url,
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": bool(await_promise),
        },
        timeout,
    )
    if result.get("exceptionDetails"):
        raise RuntimeError(
            str(result["exceptionDetails"].get("text") or "evaluate failed")[:200]
        )
    return (result.get("result") or {}).get("value")
