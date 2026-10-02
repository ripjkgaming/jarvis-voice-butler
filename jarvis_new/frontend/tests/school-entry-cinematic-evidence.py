"""Matched, isolated headless evidence for the 2 October cinematic entry.

Use an isolated Next dev source copy; never run the frontend build/export script.
All bridge/native operations are mocked and non-fixture HTTP traffic is denied.
Performance runs have no screenshots/video; media uses separate transitions.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import statistics
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

SPEC = importlib.util.spec_from_file_location(
    "entry_evidence", Path(__file__).with_name("school-entry-evidence.py")
)
ENTRY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ENTRY)

COST_PROBE = r"""(() => {
    const request = window.requestAnimationFrame.bind(window);
    const clear = CanvasRenderingContext2D.prototype.clearRect;
    const samples = [];
    let active = null, last = null, phaseName = null, phaseElapsed = 0, capture = null;
    window.armEntryCapture = target => { capture = target; };
    window.requestAnimationFrame = callback => request(now => {
        const previous = active;
        const frame = {at: now, entry: false, start: performance.now()};
        active = frame;
        try { callback(now); } finally {
            if (frame.entry) {
                const phase = document.querySelector('.stx')?.dataset.entryPhase || null;
                if (phase !== phaseName) { phaseName = phase; phaseElapsed = 0; }
                if (last !== null) phaseElapsed += Math.max(0, Math.min(frame.at - last, 34));
                last = frame.at;
                samples.push({at: frame.at, callbackMs: performance.now() - frame.start, phase});
                if (capture && phase === capture.phase && phaseElapsed >= capture.duration * capture.fraction) {
                    window.entryCaptureResult = {...capture, drawnElapsedMs: phaseElapsed,
                        sampledFraction: phaseElapsed / capture.duration};
                    window.freezeEntryCapture();
                    capture = null;
                }
            }
            active = previous;
        }
    });
    CanvasRenderingContext2D.prototype.clearRect = function(...args) {
        if (active && this.canvas.matches('.stx-canvas')) active.entry = true;
        return clear.apply(this, args);
    };
    window.entryCostProbe = () => [...samples];
})();"""


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0}
    return {
        "samples": len(ordered),
        "median": round(statistics.median(ordered), 3),
        "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
        "max": round(ordered[-1], 3),
    }


def guard_network(url):
    original = ENTRY.EvidenceDesktop.install
    origin = urlparse(url).netloc

    async def install(desktop):
        desktop.page.set_default_navigation_timeout(60000)

        async def route_request(route):
            parsed = urlparse(route.request.url)
            if parsed.netloc == origin:
                await route.continue_()
            else:
                await route.abort()

        # Playwright applies the most recently installed matching route first,
        # so the existing explicit fixture bridge route wins over this guard.
        await desktop.page.route("**/*", route_request)
        await original(desktop)
        await desktop.page.add_init_script(COST_PROBE)

    ENTRY.EvidenceDesktop.install = install


async def performance(browser, url, dpr, cross, repetition):
    context, page, desktop = await ENTRY.create_page(browser, url, dpr=dpr, cross=cross)
    cdp = await context.new_cdp_session(page)
    await cdp.send("Performance.enable")

    async def sample():
        return {
            item["name"]: item["value"]
            for item in (await cdp.send("Performance.getMetrics"))["metrics"]
        }

    try:
        # Let hydration, fonts and the development connection settle before
        # taking either baseline or revised measurements.
        await page.wait_for_timeout(400)
        initial = await sample()
        await page.evaluate("frameProbe.start()")
        await desktop.signal("collapse")
        await desktop.settled(True)
        frames = await ENTRY.SCHOOL.frame_metrics(page)
        final = await sample()
        probe = await page.evaluate("entryProbe()")
        costs = await page.evaluate("entryCostProbe()")
        assert probe["pendingEntry"] == 0, probe
        assert all(not item["connected"] for item in probe["canvases"]), probe
        assert not desktop.errors, desktop.errors
        assert "dock" in desktop.stages() and desktop.output_x == 0
        if cross:
            assert desktop.moves and not any(
                move["painted_before_move"] for move in desktop.moves
            ), desktop.moves
        return {
            "kind": "performance",
            "dpr": dpr,
            "cross_monitor": cross,
            "repetition": repetition,
            "elapsed_ms": round((final["Timestamp"] - initial["Timestamp"]) * 1000, 2),
            "frames": frames,
            "entry_callback_ms": distribution([item["callbackMs"] for item in costs]),
            "entry_callback_ms_by_phase": {
                phase: distribution(
                    [item["callbackMs"] for item in costs if item["phase"] == phase]
                )
                for phase in dict.fromkeys(item["phase"] for item in costs)
            },
            "task_ms": round(
                (final["TaskDuration"] - initial["TaskDuration"]) * 1000, 2
            ),
            "script_ms": round(
                (final["ScriptDuration"] - initial["ScriptDuration"]) * 1000, 2
            ),
            "layouts": final["LayoutCount"] - initial["LayoutCount"],
            "style_recalculations": final["RecalcStyleCount"]
            - initial["RecalcStyleCount"],
            "probe": probe,
            "moves": desktop.moves,
        }
    finally:
        await cdp.detach()
        await context.close()


async def fraction_stills(browser, args):
    samples = []
    durations = (
        {"compress": 820, "forge": 1100}
        if args.stage == "baseline"
        else {"compress": 720, "forge": 900}
    )
    for name, fractions in (
        ("compress", (0.2, 0.45, 0.65)),
        ("forge", (0.15, 0.35, 0.6)),
    ):
        for fraction in fractions:
            context, page, desktop = await ENTRY.create_page(browser, args.url, dpr=1)
            try:
                await ENTRY.dark_desktop(context, page)
                await page.evaluate(
                    "target => armEntryCapture(target)",
                    {
                        "phase": name,
                        "duration": durations[name],
                        "fraction": fraction,
                    },
                )
                await desktop.signal("collapse")
                await page.wait_for_function("window.entryCaptureResult", timeout=15000)
                result = await page.evaluate("entryCaptureResult")
                result["quality"] = await ENTRY.canvas_quality(page)
                result["file"] = f"entry-{name}-{round(fraction * 100):02d}.png"
                await page.screenshot(path=str(args.output / result["file"]))
                assert result["quality"]["phase"] == name, result
                assert not desktop.errors, desktop.errors
                samples.append(result)
            finally:
                await context.close()
    return {
        "kind": "fraction_stills",
        "samples": samples,
        "method": "Capture-only RAF freeze at first frame crossing requested clamped drawn-clock fraction.",
    }


async def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    guard_network(args.url)
    report = {
        "stage": args.stage,
        "url": args.url,
        "environment": "isolated Next dev source; headless Chromium; mocked native and bridge",
        "limits": [
            "Development build and synthetic headless timing; not production or native WebKit/KWin FPS.",
            "Entry callback timing is JavaScript submission time, not GPU raster/compositor time.",
            "Separate capture transitions are paused for stills; video and performance run freely.",
            "Both stages reuse the same source tree except the two entry implementation files.",
        ],
        "results": [],
    }

    def save(result):
        report["results"].append(result)
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(result), flush=True)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            # Compile and warm the copied application before measurement.
            context, _, _ = await ENTRY.create_page(browser, args.url)
            await context.close()
            if args.fractions_only:
                save(await fraction_stills(browser, args))
                return
            if not args.media_only:
                for repetition in range(1, args.repetitions + 1):
                    for dpr, cross in ((1, False), (2, False), (1, True)):
                        save(
                            await performance(browser, args.url, dpr, cross, repetition)
                        )
                if args.stage == "after":
                    for job in (
                        lambda: ENTRY.lifecycle(browser, args.url),
                        lambda: ENTRY.lifecycle(browser, args.url, hidden=True),
                        lambda: ENTRY.reversal(browser, args.url, "compress"),
                        lambda: ENTRY.reversal(browser, args.url, "forge"),
                        lambda: ENTRY.return_reentry(browser, args.url),
                        lambda: ENTRY.reduced(browser, args.url),
                        lambda: ENTRY.delayed_dock(browser, args.url),
                        lambda: ENTRY.reveal_curve(browser, args.url),
                    ):
                        save(await job())
            if not args.performance_only:
                save(await ENTRY.video(browser, args.url, args.output))
                for dpr in (1, 2):
                    save(await ENTRY.capture(browser, args.url, args.output, dpr))
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--stage", required=True, choices=("baseline", "after"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--performance-only", action="store_true")
    parser.add_argument("--media-only", action="store_true")
    parser.add_argument("--fractions-only", action="store_true")
    asyncio.run(main(parser.parse_args()))
