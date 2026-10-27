"""Character skin harness: serves frontend/out, mocks the bridge in-page, drives
real engine transitions, captures screenshots and frame times.

  uv run --no-sync python frontend/tests/character-harness.py --out DIR [--gl 0|1] [--video]
Never touches the real bridge: every request to the bridge port is fulfilled here.
"""
import argparse, asyncio, json, sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import yaml
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src import characters as C  # noqa: E402

OUT = ROOT / "frontend" / "out"


def payload():
    chars = {k: C.public_dict(v) for k, v in C.load_all().items()}
    return {"ok": True, "active": "jarvis", "motion": C.motion(), "characters": chars}


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


async def run(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(OUT)))
    Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/"
    state = {"active": "jarvis", "target": None, "phase": "idle", "char_turn": 0}
    P = payload()
    if a.budget:
        P["motion"]["glow"]["budget_ms"] = a.budget  # visual runs on software GL only

    async def bridge(route):
        req = route.request
        path = req.url.split("4317", 1)[1].split("?")[0]
        hdr = {"access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*"}
        if req.method == "OPTIONS":
            return await route.fulfill(status=204, headers=hdr)
        if path == "/characters":
            body = P
        elif path == "/character":
            body = dict(state, ok=True)
        elif path == "/character/switch":
            t = json.loads(req.post_data or "{}").get("target")
            state["char_turn"] += 1
            state.update(target=t, phase="idle", active=t)
            body = {"ok": True, "switched": True, "char_turn": state["char_turn"], "character": dict(state)}
        else:
            body = {"ok": True}
        await route.fulfill(status=200, headers=hdr, content_type="application/json", body=json.dumps(body))

    async with async_playwright() as p:
        b = await p.chromium.launch(args=[] if a.gl else ["--disable-gpu", "--disable-webgl"])
        ctx = await b.new_context(
            viewport={"width": a.w, "height": a.h},
            record_video_dir=str(out / "video") if a.video else None,
            record_video_size={"width": a.w, "height": a.h} if a.video else None,
        )
        await ctx.route("**/127.0.0.1:4317/**", bridge)
        pg = await ctx.new_page()
        await pg.add_init_script("localStorage.setItem('jarvis.cx.effects','1')")
        await pg.goto(url, wait_until="domcontentloaded")
        await pg.wait_for_selector("[data-jstate]", timeout=20000)
        await pg.wait_for_function("window.__cx !== undefined || true")
        await pg.wait_for_timeout(2500)
        await pg.screenshot(path=str(out / "jarvis.png"))
        results = {}
        order = ["jarvis", "ultron", "jarvis", "friday", "jarvis", "ultron", "friday", "ultron", "friday", "jarvis"]
        pairs = [("jarvis", "ultron"), ("ultron", "jarvis"), ("jarvis", "friday"), ("friday", "jarvis"), ("ultron", "friday"), ("friday", "ultron")]

        async def to(target):
            state["char_turn"] += 1
            state.update(target=target, phase="preparing")
            await pg.wait_for_timeout(300)
            state.update(active=target, target=None, phase="idle")

        cur = "jarvis"
        for src, dst in pairs:
            for step in ([x for x in [("ultron" if src == "friday" and dst == "jarvis" else None)] if x] if False else []):
                pass
            if cur != src:
                await to(src)
                await pg.wait_for_timeout(2600)
                cur = src
            await pg.evaluate("window.__cx && (window.__cx.stats = {frames:0,worst:0,over20:0,over34:0,total:0})")
            await to(dst)
            await pg.wait_for_timeout(a.mid)
            await pg.screenshot(path=str(out / f"{src}-to-{dst}-mid.png"))
            await pg.wait_for_timeout(2500)
            st = await pg.evaluate("window.__cx ? {...window.__cx.stats, trace: window.__cx.trace.slice()} : null")
            results[f"{src}->{dst}"] = st
            await pg.screenshot(path=str(out / f"{dst}-after-{src}.png"))
            cur = dst
        results["fx"] = await pg.evaluate("window.__fx ? {mode: window.__fx.mode, ...window.__fx.stats} : null")
        (out / "transitions.json").write_text(json.dumps(results))
        await ctx.close()
        await b.close()
    srv.shutdown()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--w", type=int, default=1600)
    ap.add_argument("--h", type=int, default=900)
    ap.add_argument("--mid", type=int, default=1100)
    ap.add_argument("--gl", type=int, default=1)
    ap.add_argument("--budget", type=float, default=0)
    ap.add_argument("--video", action="store_true")
    asyncio.run(run(ap.parse_args()))
