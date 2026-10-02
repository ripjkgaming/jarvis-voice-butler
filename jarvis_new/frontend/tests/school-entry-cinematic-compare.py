"""All-phase ABBA renderer submission benchmark on one visible headless canvas.

The two renderers intentionally differ visually. This measures CPU-side draw
submission and possible canvas backpressure, not GPU completion or native FPS.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright

FRONTEND = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "entry_compare", Path(__file__).with_name("school-entry-compare.py")
)
COMPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPARE)

BENCHMARK = r"""async (sources) => {
    const makers = sources.map(source => new Function(
        'document', 'exports', source + ';return exports.createEntryScene;'
    )(document, {}));
    const phases = ['aperture', 'compress', 'transfer-out', 'transfer-in', 'descent', 'forge', 'reveal'];
    const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
    const median = values => {
        const sorted = [...values].sort((a, b) => a - b);
        return (sorted[(sorted.length - 1) >> 1] + sorted[sorted.length >> 1]) / 2;
    };
    const boundaryChecks = [];
    // Readback uses disposable canvases. Timing uses fresh canvases below,
    // avoiding a getImageData-induced change to their rendering mode.
    for (const dpr of [1, 2]) for (let index = 0; index < makers.length; index++) {
        const canvas = document.createElement('canvas');
        document.body.append(canvas);
        const scene = makers[index](canvas, '#5fe3ff');
        scene.resize(1600, 900, dpr);
        try {
            for (const [from, to] of [['aperture', 'compress'], ['forge', 'reveal']]) {
                const context = canvas.getContext('2d');
                scene.draw(from, 1, 736, 369, 64, 0, 'left');
                const before = context.getImageData(0, 0, canvas.width, canvas.height).data;
                scene.draw(to, 0, 736, 369, 64, 0, 'left');
                const after = context.getImageData(0, 0, canvas.width, canvas.height).data;
                let changedPixels = 0, maxChannelDifference = 0, absoluteChannelDifference = 0;
                for (let offset = 0; offset < before.length; offset += 4) {
                    let changed = false;
                    for (let channel = 0; channel < 4; channel++) {
                        const delta = Math.abs(before[offset + channel] - after[offset + channel]);
                        changed ||= delta > 0;
                        maxChannelDifference = Math.max(maxChannelDifference, delta);
                        absoluteChannelDifference += delta;
                    }
                    changedPixels += Number(changed);
                }
                boundaryChecks.push({renderer: index ? 'candidate' : 'baseline', dpr,
                    from: from + ':1', to: to + ':0', changedPixels, maxChannelDifference,
                    absoluteChannelDifference, pixels: canvas.width * canvas.height,
                    exact: changedPixels === 0});
            }
        } finally { scene.dispose(); canvas.remove(); }
    }
    const results = [];
    for (const dpr of [1, 2]) {
        const canvas = document.createElement('canvas');
        document.body.append(canvas);
        const pair = makers.map(make => make(canvas, '#5fe3ff'));
        if (pair.some(scene => !scene)) throw Error('Canvas 2D unavailable');
        for (const scene of pair) scene.resize(1600, 900, dpr);
        try {
            for (const phase of phases) {
                for (let i = 0; i < 80; i++) for (const scene of pair)
                    scene.draw(phase, (i + .5) / 80, 736, 369, 64, 0, 'left');
                await frame(); await frame();
                const samples = [[], []];
                for (let round = 0; round < 6; round++) {
                    for (const index of round % 2 ? [1, 0, 0, 1] : [0, 1, 1, 0]) {
                        await frame();
                        const start = performance.now();
                        for (let i = 0; i < 20; i++) pair[index].draw(
                            phase, (i + .5) / 20, 736, 369, 64, 0, 'left');
                        samples[index].push((performance.now() - start) / 20);
                    }
                }
                results.push({dpr, phase, baselineMs: median(samples[0]),
                    candidateMs: median(samples[1]), samples});
            }
        } finally {
            pair.forEach(scene => scene.dispose());
            canvas.remove();
        }
    }
    return {boundaryChecks, submissionTimings: results};
}"""


async def main(args):
    paths = [args.before.resolve(), args.after.resolve()]
    payloads = [path.read_bytes() for path in paths]
    args.output.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "pending",
        "sources": {
            label: {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}
            for label, path, data in zip(("before", "after"), paths, payloads)
        },
        "method": {
            "viewport": {"width": 1600, "height": 900},
            "dpr": [1, 2],
            "timing": "Same visible canvas; independent scene state; alternating ABBA/BAAB rounds",
            "warmupDrawsPerRendererPerPhase": 80,
            "timingSamplesPerRendererPerPhase": 12,
            "drawsPerTimingSample": 20,
            "phases": "all seven including both transfer directions in the sequence",
        },
        "limitations": [
            "Visual design changes intentionally; no pixel equality between versions is expected or asserted.",
            "Candidate phase endpoints are compared within the candidate renderer at aperture1/compress0 and forge1/reveal0.",
            "Headless Chromium CPU-side canvas submission; not GPU completion or native WebKit/KWin FPS.",
            "Short samples and host contention can produce noise; report every phase rather than selected wins.",
        ],
    }
    try:
        transpiled = subprocess.run(
            ["node", "-e", COMPARE.TRANSPILE],
            input=json.dumps([data.decode() for data in payloads]),
            text=True,
            capture_output=True,
            check=True,
            cwd=FRONTEND,
        )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                report["browser"] = {"name": "Chromium", "version": browser.version}
                page = await browser.new_page(viewport={"width": 1600, "height": 900})
                await page.set_content(
                    "<style>html,body{margin:0;background:#03080e}"
                    "canvas{position:absolute;inset:0;width:1600px;height:900px}</style>"
                )
                report["results"] = await page.evaluate(
                    BENCHMARK, json.loads(transpiled.stdout)
                )
                report["status"] = (
                    "passed"
                    if all(
                        item["exact"]
                        for item in report["results"]["boundaryChecks"]
                        if item["renderer"] == "candidate"
                    )
                    else "boundary_mismatch"
                )
            finally:
                await browser.close()
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
    (args.output / "renderer-comparison.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(json.dumps(report))
    return report["status"] != "passed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
