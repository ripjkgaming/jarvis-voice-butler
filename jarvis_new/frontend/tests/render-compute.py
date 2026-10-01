"""Isolated real-canvas renderer benchmark; no React/native bridge or live desktop.

Measures synchronous draw-command submission and sampled JS allocation churn for
identical tracer/pool workloads. It is a microbenchmark, not FPS/native GPU time.
Run against saved source directories; whole-UI profiling remains render-profile.py.
"""

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
from statistics import median

from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("parity", HERE / "render-parity.py")
parity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parity)

SETUP = """() => {
    window.workloads = {};
    for (const key of ['before', 'after']) {
        const canvas = document.createElement('canvas');
        canvas.width = innerWidth; canvas.height = innerHeight;
        const ctx = canvas.getContext('2d');
        const api = window[key+'Entry'].renderProbe;
        const scene = {segs:[], rim:api.buildRim(innerWidth,innerHeight),
            tracers: [{s0:10,dir:1,dist:4000,tail0:360,t0:0,dur:4000,seed:1,landed:false},
                      {s0:10,dir:-1,dist:4000,tail0:360,t0:0,dur:4000,seed:2,landed:false}],
            pool:{ph:64,total:2,arrived:2,level:1,spread:1,settle:0.3,alpha:1,held:true,inset:0},
            onResize:null,last:0,packets:[]};
        window.workloads[key] = {canvas,ctx,api,scene};
    }
    window.drawBatch = (key,kind,count) => {
        const {canvas,ctx,api,scene} = workloads[key];
        ctx.globalCompositeOperation = kind === 'tracer' ? 'lighter' : 'source-over';
        const start = performance.now();
        for (let i=0; i<count; i++) {
            const now = 800 + (i%120)*16;
            ctx.clearRect(0,0,canvas.width,canvas.height);
            if (kind === 'pool') api.drawPool(ctx,scene.pool,canvas.width,canvas.height,now,0.016,[34,211,238]);
            else if (kind === 'tracer') for (const tr of scene.tracers) api.drawTracer(ctx,scene,tr,now,'#22d3ee');
            else api.drawScene(canvas,ctx,scene,now,'#22d3ee');
        }
        return performance.now()-start;
    };
}"""


async def run(args):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1600, "height": 900})
            for name, directory in [("before", args.before), ("after", args.after)]:
                await page.add_script_tag(
                    content=parity.source(directory, "entry", name)
                )
            await page.evaluate(SETUP)
            cdp = await page.context.new_cdp_session(page)
            await cdp.send("HeapProfiler.enable")
            results = []
            for kind in ["pool", "tracer", "scene"]:
                # Warm both implementations and all reusable storage before measuring.
                for name in ["before", "after"]:
                    await page.evaluate(
                        "([key,kind])=>drawBatch(key,kind,240)", [name, kind]
                    )
                for name in ["before", "after", "after", "before", "before", "after"]:
                    await cdp.send(
                        "HeapProfiler.startSampling",
                        {
                            "samplingInterval": 4096,
                            "includeObjectsCollectedByMajorGC": True,
                            "includeObjectsCollectedByMinorGC": True,
                        },
                    )
                    elapsed = await page.evaluate(
                        "([key,kind,count])=>drawBatch(key,kind,count)",
                        [name, kind, args.frames],
                    )
                    heap = (await cdp.send("HeapProfiler.stopSampling"))["profile"]

                    def total(n):
                        return n.get("selfSize", 0) + sum(
                            total(child) for child in n.get("children", [])
                        )

                    results.append(
                        {
                            "renderer": kind,
                            "variant": name,
                            "frames": args.frames,
                            "submission_ms": round(elapsed, 2),
                            "sampled_js_alloc_bytes": total(heap["head"]),
                        }
                    )
                    await page.wait_for_timeout(100)
            summary = []
            for kind in ["pool", "tracer", "scene"]:
                row = {"renderer": kind, "frames": args.frames}
                for name in ["before", "after"]:
                    samples = [
                        r
                        for r in results
                        if r["renderer"] == kind and r["variant"] == name
                    ]
                    row[name] = {
                        key: median(r[key] for r in samples)
                        for key in ["submission_ms", "sampled_js_alloc_bytes"]
                    }
                summary.append(row)
            output = {
                "method": "Headless Chromium real-canvas command submission; warmed ABBAAB; sampled JS allocations include collected objects; not presentation/GPU timing.",
                "browser": browser.version,
                "summary": summary,
                "samples": results,
            }
            args.output.write_text(json.dumps(output, indent=2))
            print(json.dumps(output, indent=2))
        finally:
            await browser.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--before", type=Path, required=True)
    p.add_argument("--after", type=Path, required=True)
    p.add_argument("--frames", type=int, default=480)
    p.add_argument("--output", type=Path, required=True)
    asyncio.run(run(p.parse_args()))
