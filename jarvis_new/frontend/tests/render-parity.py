"""Compare real before/after canvas renderers pixel-for-pixel in headless Chromium.

Both inputs are saved TSX source directories containing school-transition.tsx and
school-return.tsx. React and native dependencies are inert. Tests full scenes,
all packet directions/curves, tail/arrival boundaries, pool, blueprint and scan at
native DPR 1 and 2. The screenshots are review evidence, not mocks
of the rendering implementation.
"""

import argparse
import asyncio
import json
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent


def source(directory, kind, name):
    filename = "school-transition.tsx" if kind == "entry" else "school-return.tsx"
    return subprocess.check_output(
        [
            "node",
            str(HERE / "render-source.cjs"),
            str(directory / filename),
            kind,
            name,
        ],
        text=True,
    )


PROBE = """({dpr}) => {
    let seed = 937;
    const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
    const originalRandom = Math.random;
    const a = document.querySelector('#before'), b = document.querySelector('#after');
    const size = [innerWidth, innerHeight];
    const colors = ['#22d3ee', '#a855f7', '#fb923c', '#6b7280'];
    const failures = [];
    let comparisons = 0;
    function compare(label, before, after, reset = true) {
        if (reset) {
            a.width = b.width = Math.round(size[0] * dpr);
            a.height = b.height = Math.round(size[1] * dpr);
        }
        seed = 937; Math.random = random; before(a, a.getContext('2d'));
        seed = 937; after(b, b.getContext('2d')); Math.random = originalRandom;
        const x = a.getContext('2d').getImageData(0, 0, a.width, a.height).data;
        const y = b.getContext('2d').getImageData(0, 0, b.width, b.height).data;
        let different = 0, maxDelta = 0;
        for (let i=0; i<x.length; i++) if (x[i] !== y[i]) { different++; maxDelta = Math.max(maxDelta, Math.abs(x[i]-y[i])); }
        comparisons++;
        if (different) failures.push({label, different, maxDelta});
    }
    function packets(api, dir, out) {
        const segs = [{x0:30,y0:45,x1:30,y1:310,a:0.8}, {x0:610,y0:45,x1:610,y1:310,a:0.8}];
        return dir === 'flow' ? api.renderProbe.flow(segs, ...size, 0) : out ? api.uplink(segs, dir, ...size, 0) : api.downlink(segs[0], dir, ...size, 0);
    }
    for (const color of colors) for (const direction of ['left','right','up','down','flow']) for (const out of [true,false]) for (const now of [0,180,470,990,1400]) {
        compare(`packets/${direction}/${out}/${now}/${color}`, (canvas,ctx) => {
            ctx.scale(dpr,dpr); ctx.globalCompositeOperation='lighter'; beforeEntry.drawPackets(ctx,packets(beforeEntry,direction,out),now,color);
        }, (canvas,ctx) => {
            ctx.scale(dpr,dpr); ctx.globalCompositeOperation='lighter'; afterEntry.drawPackets(ctx,packets(afterEntry,direction,out),now,color);
        });
    }
    for (const now of [0,50,500,1000,1250,1600]) for (const color of colors) {
        const scene = api => ({segs:[{x0:10,y0:8,x1:630,y1:8,a:0.7}],rim:api.renderProbe.buildRim(...size),tracers:[{s0:10,dir:1,dist:2000,tail0:80,t0:0,dur:1000,seed:1,landed:false},{s0:10,dir:-1,dist:2000,tail0:80,t0:0,dur:1000,seed:2,landed:false}],pool:{ph:64,total:2,arrived:1,level:0.7,spread:0.8,settle:0.3,alpha:0.8,held:true,inset:8},onResize:null,last:now-16,packets:[]});
        compare(`entry/${now}/${color}`, (canvas,ctx) => beforeEntry.renderProbe.drawScene(canvas,ctx,scene(beforeEntry),now,color), (canvas,ctx) => afterEntry.renderProbe.drawScene(canvas,ctx,scene(afterEntry),now,color));
    }
    for (let frame = 0; frame < 3; frame++) {
        const scene = {segs:[],rim:null,tracers:[],pool:{ph:64,total:2,arrived:2,level:0.7,spread:0.8,settle:0.3,alpha:0.8,held:true,inset:0},onResize:null,last:0,packets:[]};
        compare(`pool-only-sequence/${frame}`, (canvas,ctx) => beforeEntry.renderProbe.drawScene(canvas,ctx,structuredClone(scene),frame*16,'#22d3ee'), (canvas,ctx) => afterEntry.renderProbe.drawScene(canvas,ctx,structuredClone(scene),frame*16,'#22d3ee'), frame === 0);
    }
    for (const now of [0,50,180,470,990,1400]) for (const color of colors) {
        const scene = api => ({ox:12,oy:8,w:600,h:330,hair:[{x0:0,y0:340,x1:640,y1:340,a:0.4}],pens:[api.renderProbe.makePen([[0,340],[0,330]],[[0,330],[0,0],[300,0]],0),api.renderProbe.makePen([[640,340],[600,330]],[[600,330],[600,0],[300,0]],0)],outline:0.7,lock:0.8,grid:0.5,label:0.7,scan:now>470?0.6:null,onResize:null,packets:[]});
        compare(`return/${now}/${color}`, (canvas,ctx) => beforeReturn.renderProbe.drawScene(canvas,ctx,scene(beforeReturn),now,color), (canvas,ctx) => afterReturn.renderProbe.drawScene(canvas,ctx,scene(afterReturn),now,color), now === 0);
    }
    return {dpr, comparisons, failures};
}"""


async def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            results = []
            for dpr in (1, 2):
                page = await browser.new_page(
                    viewport={"width": 640, "height": 360}, device_scale_factor=dpr
                )
                await page.set_content(
                    '<style>body{margin:0;background:#03101a}canvas{position:absolute;inset:0;width:640px;height:360px}#after{visibility:hidden}</style><canvas id="before"></canvas><canvas id="after"></canvas>'
                )
                for name, directory in [("before", args.before), ("after", args.after)]:
                    for kind in ("entry", "return"):
                        await page.add_script_tag(content=source(directory, kind, name))
                result = await page.evaluate(PROBE, {"dpr": dpr})
                results.append(result)
                await page.screenshot(path=str(args.output / f"before-dpr{dpr}.png"))
                await page.evaluate(
                    "document.querySelector('#before').style.visibility='hidden'; document.querySelector('#after').style.visibility='visible'"
                )
                await page.screenshot(path=str(args.output / f"after-dpr{dpr}.png"))
                await page.close()
                assert not result["failures"], result
            (args.output / "parity.json").write_text(json.dumps(results, indent=2))
            print(json.dumps(results, indent=2))
        finally:
            await browser.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--before", type=Path, required=True)
    p.add_argument("--after", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    asyncio.run(run(p.parse_args()))
