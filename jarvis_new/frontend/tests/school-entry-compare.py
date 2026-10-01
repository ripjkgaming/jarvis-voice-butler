"""Compare two school-entry renderers without touching the real desktop.

From jarvis_new, after installing frontend dependencies:
    uv run --no-sync python frontend/tests/school-entry-compare.py \
        --before frontend/tests/entry-evidence/2026-10-01/school-entry-scene-before-refinement.ts.txt \
        --output /tmp/jarvis-entry-comparison

TypeScript is transpiled with the frontend's existing Node dependency. An
isolated headless Chromium page compares exact RGBA pixels, then benchmarks both
renderers on the SAME visible canvas in alternating ABBA/BAAB order. It never
loads the application, invokes native IPC, changes modes, or calls the bridge.
Submission timings are CPU-side calls/enqueue work, not GPU completion or FPS.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright

FRONTEND = Path(__file__).resolve().parents[1]
TRANSPILE = r"""
const fs = require('node:fs');
const ts = require('typescript');
const sources = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(sources.map(source => ts.transpileModule(source, {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020},
}).outputText)));
"""

COMPARE = r"""async (sources) => {
    const makers = sources.map(source => new Function(
        'document', 'exports', source + ';return exports.createEntryScene;'
    )(document, {}));
    const make = index => {
        const canvas = document.createElement('canvas');
        document.body.append(canvas);
        const scene = makers[index](canvas, '#5fe3ff');
        if (!scene) throw Error('Canvas 2D context unavailable');
        return {canvas, scene};
    };
    let pair = [make(0), make(1)];
    let cases = 0;
    let pixels = 0;
    const test = (width, height, dpr, phase, progress, direction = 'left') => {
        for (const item of pair) {
            item.scene.resize(width, height, dpr);
            item.scene.draw(phase, progress, width * .46, height * .41, 48, 8, direction);
        }
        const bytes = pair.map(item => item.canvas.getContext('2d').getImageData(
            0, 0, item.canvas.width, item.canvas.height
        ).data);
        if (bytes[0].length !== bytes[1].length) throw Error('Backing size mismatch');
        for (let i = 0; i < bytes[0].length; i++) {
            if (bytes[0][i] !== bytes[1][i]) {
                throw Error('Pixel mismatch: ' + JSON.stringify({
                    width, height, dpr, phase, progress, direction,
                    byte: i, before: bytes[0][i], after: bytes[1][i],
                }));
            }
        }
        cases++;
        pixels += bytes[0].length / 4;
    };
    const phases = [
        'aperture', 'compress', 'transfer-out', 'transfer-in', 'descent', 'forge', 'reveal',
    ];
    try {
        for (const dpr of [1, 2]) {
            for (const phase of phases) {
                for (const progress of [0, .3, .7, 1]) test(640, 360, dpr, phase, progress);
            }
            for (const phase of ['transfer-out', 'transfer-in']) {
                for (const direction of ['left', 'right', 'up', 'down']) {
                    for (const progress of [.15, .65]) {
                        test(640, 360, dpr, phase, progress, direction);
                    }
                }
            }
        }
        for (const [phase, progress] of [
            ['aperture', .3], ['compress', .7], ['descent', .5],
            ['forge', 1], ['reveal', .3], ['reveal', 1],
        ]) test(1600, 900, 2, phase, progress);
    } finally {
        pair.forEach(item => { item.scene.dispose(); item.canvas.remove(); });
    }

    // A fresh shared canvas avoids both readback-mode and occlusion biases.
    // Each renderer retains its own atlas, seeds and scene state.
    const shared = document.createElement('canvas');
    document.body.append(shared);
    pair = makers.map(make => ({canvas: shared, scene: make(shared, '#5fe3ff')}));
    if (pair.some(item => !item.scene)) throw Error('Canvas 2D context unavailable');
    for (const item of pair) item.scene.resize(1600, 900, 2);
    const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
    const timings = {};
    try {
        for (const phase of ['aperture', 'compress', 'descent']) {
            for (let i = 0; i < 100; i++) {
                for (const item of pair) item.scene.draw(
                    phase, (i + .5) / 100, 736, 369, 48, 8, 'left'
                );
            }
            await frame();
            await frame();
            const samples = [[], []];
            for (let round = 0; round < 6; round++) {
                for (const index of round % 2 ? [1, 0, 0, 1] : [0, 1, 1, 0]) {
                    await frame();
                    const start = performance.now();
                    for (let i = 0; i < 40; i++) pair[index].scene.draw(
                        phase, (i + .5) / 40, 736, 369, 48, 8, 'left'
                    );
                    samples[index].push((performance.now() - start) / 40);
                }
            }
            const median = values => [...values].sort((a, b) => a - b)[
                Math.floor(values.length / 2)
            ];
            timings[phase] = {
                baselineMs: median(samples[0]), candidateMs: median(samples[1]), samples,
            };
        }
    } finally {
        pair.forEach(item => item.scene.dispose());
        shared.remove();
    }
    return {pixelIdenticalCases: cases, rgbaPixelsCompared: pixels, submissionTimings: timings};
}"""


async def compare(args: argparse.Namespace) -> int:
    paths = [args.before.resolve(), args.after.resolve()]
    payloads = [path.read_bytes() for path in paths]
    args.output.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": 1,
        "status": "pending",
        "sources": {
            label: {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}
            for label, path, data in zip(("before", "after"), paths, payloads)
        },
        "method": {
            "pixels": "94 exact RGBA comparisons at 640x360 DPR1/2 and 1600x900 DPR2",
            "timing": "Same visible canvas; fresh contexts after pixel readback; ABBA/BAAB",
            "warmupDrawsPerRendererPerPhase": 100,
            "timingSamplesPerRendererPerPhase": 12,
            "drawsPerTimingSample": 40,
        },
        "limitations": [
            "Headless Chromium renderer evidence; no native desktop or full-app FPS measurement.",
            "Submission times measure JavaScript/canvas enqueue and possible backpressure, not GPU completion.",
            "Timing differences can be noisy; pixel parity is exact for the sampled cases.",
        ],
    }
    try:
        transpiled = subprocess.run(
            ["node", "-e", TRANSPILE],
            input=json.dumps([data.decode("utf-8") for data in payloads]),
            text=True,
            capture_output=True,
            check=True,
            cwd=FRONTEND,
        )
        sources = json.loads(transpiled.stdout)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                report["browser"] = {"name": "Chromium", "version": browser.version}
                page = await browser.new_page(viewport={"width": 640, "height": 360})
                await page.set_content(
                    "<style>body{margin:0}canvas{position:absolute;inset:0}</style>"
                )
                report["results"] = await page.evaluate(COMPARE, sources)
                report["status"] = "passed"
            finally:
                await browser.close()
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        if isinstance(error, subprocess.CalledProcessError):
            report["transpilerStderr"] = error.stderr
    target = args.output / "comparison.json"
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(target.resolve())}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--before", type=Path, required=True, help="Baseline TypeScript renderer"
    )
    parser.add_argument(
        "--after",
        type=Path,
        default=FRONTEND / "lib" / "school-entry-scene.ts",
        help="Candidate renderer; defaults to the current project implementation",
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="Directory for comparison.json"
    )
    arguments = parser.parse_args()
    for source in (arguments.before, arguments.after):
        if not source.is_file():
            parser.error(f"Renderer file not found: {source}")
    raise SystemExit(asyncio.run(compare(arguments)))
