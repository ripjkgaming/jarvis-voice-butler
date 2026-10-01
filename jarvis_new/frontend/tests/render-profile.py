"""Headless-only rendering profile; every bridge/native call uses the smoke fake.

Use --export for an immutable build and --output for JSON evidence. Timing is
synthetic Chromium, not native WebKit/KWin or physical GPU/compositor latency.
"""

import argparse
import asyncio
import importlib.util
import json
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.async_api import async_playwright

spec = importlib.util.spec_from_file_location(
    "school_smoke", Path(__file__).with_name("school-mode-smoke.py")
)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


async def profile(browser, url, reduced, trace=False):
    page = await browser.new_page(
        viewport=smoke.NORMAL, reduced_motion="reduce" if reduced else "no-preference"
    )
    await page.add_init_script("""(() => {
        const request = window.requestAnimationFrame.bind(window);
        const cancel = window.cancelAnimationFrame.bind(window);
        const pending = new Set();
        let calls = 0;
        window.requestAnimationFrame = fn => {
            const id = request(now => { pending.delete(id); calls++; fn(now); });
            pending.add(id);
            return id;
        };
        window.cancelAnimationFrame = id => { pending.delete(id); cancel(id); };
        window.renderWork = () => ({pendingRaf: pending.size, callbacks: calls});
        const costs = {fontWrites: 0, fontMs: 0, textCalls: 0, textMs: 0};
        window.renderCanvasCosts = () => ({...costs});
        const proto = CanvasRenderingContext2D.prototype;
        const font = Object.getOwnPropertyDescriptor(proto, 'font');
        Object.defineProperty(proto, 'font', {...font, set(value) {
            if (!this.canvas.classList.contains('stx-canvas')) return font.set.call(this, value);
            const start = performance.now();
            font.set.call(this, value);
            costs.fontMs += performance.now() - start;
            costs.fontWrites++;
        }});
        const fillText = proto.fillText;
        proto.fillText = function(...args) {
            if (!this.canvas.classList.contains('stx-canvas')) return fillText.apply(this, args);
            const start = performance.now();
            fillText.apply(this, args);
            costs.textMs += performance.now() - start;
            costs.textCalls++;
        };
    })();""")

    # Only this local export and the explicit bridge fake may be contacted.
    async def isolate(route):
        if route.request.url.startswith(url):
            await route.continue_()
        else:
            await route.abort()

    await page.route("**/*", isolate)
    desktop = smoke.Desktop(page)
    await desktop.install()
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Performance.enable")
    await cdp.send("Profiler.enable")
    await cdp.send("Profiler.setSamplingInterval", {"interval": 500})
    await cdp.send("HeapProfiler.enable")
    results = []

    async def metrics():
        return {
            x["name"]: x["value"]
            for x in (await cdp.send("Performance.getMetrics"))["metrics"]
        }

    async def measure(name, action):
        requests, calls = len(desktop.requests), len(desktop.calls)
        work_before = await page.evaluate("window.renderWork?.()")
        canvas_before = await page.evaluate("window.renderCanvasCosts?.()")
        a = await metrics()
        if trace:
            await cdp.send(
                "Tracing.start",
                {
                    "categories": "devtools.timeline,disabled-by-default-devtools.timeline,blink,cc,gpu",
                    "transferMode": "ReturnAsStream",
                },
            )
        await cdp.send("Profiler.start")
        await cdp.send(
            "HeapProfiler.startSampling",
            {
                "samplingInterval": 32768,
                "includeObjectsCollectedByMajorGC": True,
                "includeObjectsCollectedByMinorGC": True,
            },
        )
        # Loading has no frame probe yet.
        if name not in ("cold_load", "hidden"):
            await page.evaluate("frameProbe.start()")
        await action()
        frames = (
            await smoke.frame_metrics(page)
            if name not in ("cold_load", "hidden")
            else {}
        )
        z = await metrics()
        work_after = await page.evaluate("window.renderWork()")
        canvas_after = await page.evaluate("window.renderCanvasCosts()")
        cpu = (await cdp.send("Profiler.stop"))["profile"]
        heap = (await cdp.send("HeapProfiler.stopSampling"))["profile"]
        trace_work = {}
        if trace:
            complete = asyncio.get_running_loop().create_future()
            cdp.once(
                "Tracing.tracingComplete", lambda event: complete.set_result(event)
            )
            await cdp.send("Tracing.end")
            stream = (await complete)["stream"]
            chunks = []
            try:
                while True:
                    chunk = await cdp.send("IO.read", {"handle": stream})
                    chunks.append(chunk["data"])
                    if chunk.get("eof"):
                        break
            finally:
                await cdp.send("IO.close", {"handle": stream})
            events = json.loads("".join(chunks))["traceEvents"]
            names = [
                "Paint",
                "RasterTask",
                "Layout",
                "UpdateLayoutTree",
                "CompositeLayers",
                "GPUTask",
                "MinorGC",
                "MajorGC",
            ]
            for event_name in names:
                durations = [
                    e.get("dur", 0)
                    for e in events
                    if e.get("name") == event_name and e.get("ph") == "X"
                ]
                trace_work[event_name] = {
                    "count": len(durations),
                    "duration_ms": round(sum(durations) / 1000, 2),
                }

        def allocations(node):
            return node.get("selfSize", 0) + sum(
                allocations(n) for n in node.get("children", [])
            )

        hot = sorted(cpu["nodes"], key=lambda n: n.get("hitCount", 0), reverse=True)[
            :12
        ]
        results.append(
            {
                "state": name,
                "reduced": reduced,
                **frames,
                "pending_raf_after_probe": work_after["pendingRaf"],
                "raf_callbacks_including_probe": work_after["callbacks"]
                - (work_before or {}).get("callbacks", 0),
                "elapsed_ms": round((z["Timestamp"] - a["Timestamp"]) * 1000, 1),
                **{
                    k: round((z[k] - a[k]) * 1000, 2)
                    for k in [
                        "TaskDuration",
                        "ScriptDuration",
                        "LayoutDuration",
                        "RecalcStyleDuration",
                    ]
                },
                **{k: z[k] - a[k] for k in ["LayoutCount", "RecalcStyleCount"]},
                "heap_delta_bytes": z["JSHeapUsedSize"] - a["JSHeapUsedSize"],
                "sampled_alloc_bytes_including_collected": allocations(heap["head"]),
                "bridge_requests": len(desktop.requests) - requests,
                "native_fake_calls": len(desktop.calls) - calls,
                "hot_samples": [
                    {
                        "fn": n["callFrame"]["functionName"],
                        "url": n["callFrame"]["url"].split("/")[-1],
                        "line": n["callFrame"]["lineNumber"],
                        "column": n["callFrame"]["columnNumber"],
                        "hits": n.get("hitCount", 0),
                    }
                    for n in hot
                ],
                "trace_work": trace_work,
                "canvas_costs": {
                    k: round(v - (canvas_before or {}).get(k, 0), 2)
                    for k, v in canvas_after.items()
                },
                "canvas": await page.locator("canvas").evaluate_all(
                    "(cs)=>cs.map(c=>[c.width,c.height])"
                ),
            }
        )
        print(json.dumps(results[-1]), flush=True)

    async def load():
        await page.goto(url, wait_until="domcontentloaded")
        await desktop.ready()

    async def transition(school):
        await desktop.signal("collapse" if school else "return")
        await desktop.settled(school)

    await measure("cold_load", load)
    await page.wait_for_timeout(2500)
    await measure("normal", lambda: page.wait_for_timeout(3000))
    await measure("entry_cold", lambda: transition(True))
    await measure("school", lambda: page.wait_for_timeout(3000))
    await measure("return_cold", lambda: transition(False))
    await measure("entry_warm", lambda: transition(True))
    await measure("return_warm", lambda: transition(False))
    await page.evaluate(
        "Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))"
    )
    await page.wait_for_timeout(150)
    await measure("hidden", lambda: page.wait_for_timeout(2000))
    assert results[-1]["bridge_requests"] == 0
    assert results[-1]["pending_raf_after_probe"] == 0, results[-1]
    assert results[-1]["raf_callbacks_including_probe"] == 0, results[-1]
    assert await page.locator(".stx,.stx-half,.sbar").count() == 0
    assert not desktop.errors, desktop.errors
    await page.close()
    return results


async def run(args, url):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            result = []
            for reduced in (
                [False, True] if args.motion == "both" else [args.motion == "reduced"]
            ):
                result.extend(await profile(browser, url, reduced, args.trace))
            args.output.write_text(json.dumps(result, indent=2))
            probe = await browser.new_page()
            renderer = await probe.evaluate("""() => {
                const gl = document.createElement('canvas').getContext('webgl');
                const info = gl?.getExtension('WEBGL_debug_renderer_info');
                return info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : null;
            }""")
            args.output.with_suffix(".environment.json").write_text(
                json.dumps(
                    {
                        "browser": browser.version,
                        "headless": True,
                        "viewport": smoke.NORMAL,
                        "device_scale_factor": 1,
                        "webgl_renderer": renderer,
                        "note": "WebGL renderer identifies this headless browser environment; trace GPU-task durations are not physical GPU execution time.",
                    },
                    indent=2,
                )
            )
            await probe.close()
        finally:
            await browser.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--export", type=Path, default=smoke.FRONTEND / "out")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--motion", choices=["both", "normal", "reduced"], default="both")
    p.add_argument(
        "--trace",
        action="store_true",
        help="Record paint/raster/GC and GPU-thread task durations; adds profiling overhead, not hardware GPU execution time.",
    )
    args = p.parse_args()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(smoke.QuietHandler, directory=str(args.export.resolve())),
    )
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        asyncio.run(run(args, f"http://127.0.0.1:{server.server_port}/"))
    finally:
        server.shutdown()
        server.server_close()
