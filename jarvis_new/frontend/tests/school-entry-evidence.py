"""Isolated evidence for the rebuilt normal-to-school entry.

Run ONLY after a fresh export and after the shared browser/GPU test slot is free:
uv run --no-sync python frontend/tests/school-entry-evidence.py --output /tmp/jarvis-entry-review

All native IPC and bridge traffic use the existing mocked Desktop. No real
windows, modes, displays, or session services are changed. Capture runs are
separate from unrecorded performance runs; reported timings are Chromium
headless measurements, not native compositor FPS guarantees.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import re
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.async_api import async_playwright

FRONTEND = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "school_smoke", Path(__file__).with_name("school-mode-smoke.py")
)
SCHOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCHOOL)
PHASES = ("aperture", "compress", "descent", "forge", "reveal")


class EvidenceDesktop(SCHOOL.Desktop):
    def __init__(self, page, *, cross=False, dock_delay_ms=0):
        super().__init__(page, cross=cross)
        self.dock_delay_ms = dock_delay_ms
        self.dock_requested = asyncio.Event()

    async def invoke(self, source, command, args):
        if command == "school_stage" and args.get("stage") == "dock":
            self.dock_requested.set()
            if self.dock_delay_ms:
                await asyncio.sleep(self.dock_delay_ms / 1000)
        return await super().invoke(source, command, args)


# Associate canvas drawing with its real RAF callback, rather than treating a
# non-drawing busy loop as suspended. Instrumentation is limited to .stx-canvas;
# background HUD and unrelated application animation loops are excluded.
PROBE = r"""(() => {
    const request = window.requestAnimationFrame.bind(window);
    const cancel = window.cancelAnimationFrame.bind(window);
    const pending = new Map();
    const entryCallbacks = new Set();
    const canvases = new Map();
    const phases = [];
    let current = null, serial = 0, entryFrames = 0, captureFrozen = false;
    window.requestAnimationFrame = callback => {
        const id = request(now => {
            pending.delete(id);
            if (captureFrozen && entryCallbacks.has(callback)) return;
            const previous = current;
            current = callback;
            if (entryCallbacks.has(callback)) entryFrames += 1;
            try { callback(now); } finally { current = previous; }
        });
        pending.set(id, callback);
        return id;
    };
    window.cancelAnimationFrame = id => { pending.delete(id); cancel(id); };
    // Used only by dedicated single-phase still captures, never video/perf.
    window.freezeEntryCapture = () => { captureFrozen = true; };
    function mark(canvas) {
        if (!canvas.matches('.stx-canvas')) return;
        if (current) entryCallbacks.add(current);
        let item = canvases.get(canvas);
        if (!item) {
            item = {id: ++serial, canvas, clears: 0, draws: 0};
            canvases.set(canvas, item);
        }
        return item;
    }
    const clear = CanvasRenderingContext2D.prototype.clearRect;
    CanvasRenderingContext2D.prototype.clearRect = function(...args) {
        const item = mark(this.canvas);
        if (item) item.clears += 1;
        return clear.apply(this, args);
    };
    const draw = CanvasRenderingContext2D.prototype.drawImage;
    CanvasRenderingContext2D.prototype.drawImage = function(...args) {
        const item = mark(this.canvas);
        if (item) item.draws += 1;
        return draw.apply(this, args);
    };
    function phaseChanged() {
        const phase = document.querySelector('.stx')?.dataset.entryPhase || null;
        if (phases.at(-1)?.phase !== phase) phases.push({phase, at: performance.now()});
    }
    new MutationObserver(phaseChanged).observe(document, {
        subtree: true, childList: true, attributes: true,
        attributeFilter: ['data-entry-phase'],
    });
    window.entryProbe = () => ({
        entryFrames,
        pendingEntry: [...pending.values()].filter(fn => entryCallbacks.has(fn)).length,
        canvases: [...canvases.values()].map(item => ({
            id: item.id, clears: item.clears, draws: item.draws,
            connected: item.canvas.isConnected,
            running: item.canvas.dataset.entryRunning || null,
        })),
        phases: [...phases],
    });
})();"""


async def create_page(
    browser, url, *, dpr=1, cross=False, video_dir=None, reduced=False, dock_delay_ms=0
):
    options = {
        "viewport": SCHOOL.NORMAL,
        "screen": SCHOOL.NORMAL,
        "device_scale_factor": dpr,
        "reduced_motion": "reduce" if reduced else "no-preference",
    }
    if video_dir:
        options.update(record_video_dir=str(video_dir), record_video_size=SCHOOL.NORMAL)
    context = await browser.new_context(**options)
    page = await context.new_page()
    page.set_default_timeout(10000)
    desktop = EvidenceDesktop(page, cross=cross, dock_delay_ms=dock_delay_ms)
    await desktop.install()
    await page.add_init_script(PROBE)
    await page.goto(url, wait_until="domcontentloaded")
    await desktop.ready()
    await desktop.settled(False)
    return context, page, desktop


async def phase(page, name):
    # Locator waiting backs off toward 500ms and can observe a short phase near
    # its end. RAF polling keeps capture offsets relative to its real start.
    await page.wait_for_function(
        "name => document.querySelector('.stx')?.dataset.entryPhase === name",
        arg=name,
        polling="raf",
        timeout=25000,
    )


async def canvas_quality(page):
    return await page.locator(".stx-canvas").evaluate("""canvas => {
        const rect = canvas.getBoundingClientRect();
        const rgba = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        let visiblePixels = 0, peakAlpha = 0;
        for (let i = 3; i < rgba.length; i += 4) {
            if (rgba[i] > 24) visiblePixels += 1;
            peakAlpha = Math.max(peakAlpha, rgba[i]);
        }
        return {
            phase: document.querySelector('.stx')?.dataset.entryPhase,
            width: canvas.width, height: canvas.height,
            cssWidth: rect.width, cssHeight: rect.height,
            dpr: devicePixelRatio, visiblePixels, peakAlpha,
            snapshots: document.querySelectorAll('.stx-snapshot').length,
            pointerEvents: getComputedStyle(canvas).pointerEvents,
        };
    }""")


async def dark_desktop(context, page):
    # Simulate the desktop under a transparent native window, not a UI edit.
    cdp = await context.new_cdp_session(page)
    await cdp.send(
        "Emulation.setDefaultBackgroundColorOverride",
        {"color": {"r": 3, "g": 8, "b": 14, "a": 1}},
    )


async def capture(browser, url, output, dpr, phases=PHASES):
    samples = []
    for name in phases:
        # A separate transition per still prevents PNG encoding from skipping
        # later phases. Freeze only the sampled entry RAF after reaching its
        # interior, preserving the exact rendered picture during pixel readback.
        context, page, desktop = await create_page(browser, url, dpr=dpr)
        try:
            await dark_desktop(context, page)
            if name == PHASES[0]:
                await page.screenshot(path=str(output / f"entry-dpr{dpr}-normal.png"))
            await desktop.signal("collapse")
            await phase(page, name)
            await page.wait_for_timeout(220)
            await page.evaluate("freezeEntryCapture()")
            sample = await canvas_quality(page)
            assert sample["phase"] == name, f"Capture missed phase {name}: {sample}"
            assert abs(sample["width"] - round(sample["cssWidth"] * dpr)) <= 1, sample
            assert abs(sample["height"] - round(sample["cssHeight"] * dpr)) <= 1, sample
            assert sample["visiblePixels"] > 0, f"Blank {name} canvas: {sample}"
            assert sample["pointerEvents"] == "none", sample
            if name == "aperture":
                assert sample["snapshots"] > 0, "Original HUD snapshot missing"
            samples.append(sample)
            await page.screenshot(path=str(output / f"entry-dpr{dpr}-{name}.png"))
            sample["after_capture_phase"] = await page.evaluate(
                "document.querySelector('.stx')?.dataset.entryPhase || null"
            )
            assert sample["after_capture_phase"] == name, (
                f"Screenshot wait advanced phase {name}: {sample}"
            )
            assert not desktop.errors, desktop.errors
        finally:
            await context.close()
    return {"capture_dpr": dpr, "samples": samples, "still_frames_paused": True}


async def video(browser, url, output):
    context, page, desktop = await create_page(browser, url, video_dir=output)
    try:
        await dark_desktop(context, page)
        await page.wait_for_timeout(150)
        await desktop.signal("collapse")
        await desktop.settled(True)
        await page.wait_for_timeout(350)
        await page.screenshot(path=str(output / "entry-docked.png"))
        assert desktop.output_x == 0 and page.viewport_size["height"] == 64
        assert not desktop.errors, desktop.errors
        assert await page.locator(".stx-snapshot").count() == 0
        recording = page.video
        await page.close()
        await recording.save_as(str(output / "normal-to-school.webm"))
        await recording.delete()
        return {
            "video": "normal-to-school.webm",
            "stages": desktop.stages(),
            "paused": False,
        }
    finally:
        await context.close()


async def performance(browser, url, dpr, *, cross=False):
    context, page, desktop = await create_page(browser, url, dpr=dpr, cross=cross)
    cdp = await context.new_cdp_session(page)
    await cdp.send("Performance.enable")

    async def sample():
        return {
            m["name"]: m["value"]
            for m in (await cdp.send("Performance.getMetrics"))["metrics"]
        }

    try:
        initial = await sample()
        await page.evaluate("frameProbe.start()")
        await desktop.signal("collapse")
        await desktop.settled(True)
        frame_data = await SCHOOL.frame_metrics(page)
        final = await sample()
        probe = await page.evaluate("entryProbe()")
        elapsed = (final["Timestamp"] - initial["Timestamp"]) * 1000
        assert elapsed < 20000, "Entry exceeded the native watchdog budget"
        assert "dock" in desktop.stages() and desktop.output_x == 0
        assert probe["pendingEntry"] == 0, "Entry RAF leaked after docking"
        assert all(not item["connected"] for item in probe["canvases"]), probe
        assert not desktop.errors, desktop.errors
        if cross:
            assert desktop.moves, "Cross-monitor move was never requested"
            assert not any(move["painted_before_move"] for move in desktop.moves), (
                desktop.moves
            )
            phases = [entry["phase"] for entry in probe["phases"]]
            assert "transfer-out" in phases and "transfer-in" in phases, phases
        return {
            "performance_dpr": dpr,
            "cross_monitor": cross,
            "elapsed_ms": round(elapsed),
            "frames": frame_data,
            "task_ms": round(
                (final["TaskDuration"] - initial["TaskDuration"]) * 1000, 2
            ),
            "script_ms": round(
                (final["ScriptDuration"] - initial["ScriptDuration"]) * 1000, 2
            ),
            "layouts": final["LayoutCount"] - initial["LayoutCount"],
            "style_recalculations": final["RecalcStyleCount"]
            - initial["RecalcStyleCount"],
            "heap_delta_mb": round(
                (final["JSHeapUsedSize"] - initial["JSHeapUsedSize"]) / 1e6, 2
            ),
            "probe": probe,
            "moves": desktop.moves,
        }
    finally:
        await cdp.detach()
        await context.close()


async def lifecycle(browser, url, *, hidden=False):
    context, page, desktop = await create_page(browser, url)
    try:
        await desktop.signal("collapse")
        await phase(page, "aperture")
        await page.wait_for_function("entryProbe().entryFrames > 1")
        if hidden:
            await page.evaluate("""() => {
                Object.defineProperty(document, 'hidden', {configurable: true, value: true});
                document.dispatchEvent(new Event('visibilitychange'));
            }""")
            # Hidden entry completes safely instead of making the user wait
            # for an invisible animation when they return to the window.
            await desktop.settled(True)
            stopped = await page.evaluate("entryProbe()")
            assert stopped["pendingEntry"] == 0, stopped
            await page.wait_for_timeout(250)
            assert await page.evaluate("entryProbe()") == stopped, (
                "Hidden entry loop did not stop"
            )
            await page.evaluate("""() => {
                delete document.hidden;
                document.dispatchEvent(new Event('visibilitychange'));
            }""")
            await page.wait_for_timeout(150)
            assert await page.evaluate("entryProbe()") == stopped, (
                "Completed entry restarted on foreground"
            )
            assert not desktop.errors, desktop.errors
            return {
                "lifecycle": "hidden finishes safely with no remaining RAF",
                "final": stopped,
            }
        await page.locator(".stx").evaluate(
            "node => node.style.transform = 'translateX(100000px)'"
        )
        await page.wait_for_timeout(100)
        offscreen = await page.evaluate("entryProbe()")
        assert offscreen["pendingEntry"] == 0, offscreen
        await page.wait_for_timeout(250)
        assert await page.evaluate("entryProbe()") == offscreen, (
            "Offscreen entry loop did not stop"
        )
        await page.locator(".stx").evaluate(
            "node => node.style.removeProperty('transform')"
        )
        await page.wait_for_function(
            "count => entryProbe().entryFrames > count", arg=offscreen["entryFrames"]
        )
        await desktop.settled(True)
        assert not desktop.errors, desktop.errors
        return {
            "lifecycle": "offscreen stops and resumes",
            "final": await page.evaluate("entryProbe()"),
        }
    finally:
        await context.close()


async def reversal(browser, url, at):
    context, page, desktop = await create_page(browser, url)
    try:
        await desktop.signal("collapse")
        await phase(page, at)
        # Native exit while ENTERING dispatches expand, then restores the HUD
        # directly. Both fixtures use the same window rectangle here.
        await desktop.signal("expand")
        await desktop.settled(False)
        canceled_stages = desktop.stages()
        canceled_probe = await page.evaluate("entryProbe()")
        await page.wait_for_timeout(400)
        assert desktop.stages() == canceled_stages, (
            "Canceled entry issued a late native stage"
        )
        assert await page.evaluate("entryProbe()") == canceled_probe, (
            "Canceled entry retained animation work"
        )
        assert canceled_probe["pendingEntry"] == 0
        await desktop.signal("collapse")
        await desktop.settled(True)
        stages = desktop.stages()
        probe = await page.evaluate("entryProbe()")
        await page.wait_for_timeout(750)
        assert desktop.stages() == stages, "Canceled entry issued a late native stage"
        assert await page.evaluate("entryProbe()") == probe, (
            "Canceled entry retained animation work"
        )
        assert await page.locator(".stx-snapshot").count() == 0
        assert probe["pendingEntry"] == 0
        assert not desktop.errors, desktop.errors
        return {"reversal_at": at, "stages": stages, "probe": probe}
    finally:
        await context.close()


async def return_reentry(browser, url):
    context, page, desktop = await create_page(browser, url, reduced=True)
    try:
        await desktop.signal("collapse")
        await desktop.settled(True)
        await page.emulate_media(reduced_motion="no-preference")
        await desktop.signal("return")
        await page.locator(".stx").wait_for()
        await page.wait_for_timeout(120)
        # Native enter while RETURNING uses arrive, since a full original HUD
        # is not available to fold. No old restore may land afterward.
        await desktop.signal("arrive")
        await desktop.settled(True)
        stages = desktop.stages()
        await page.wait_for_timeout(750)
        assert desktop.stages() == stages, "Canceled return issued a late restore"
        assert page.viewport_size["height"] == 64
        assert await page.locator(".stx-snapshot").count() == 0
        probe = await page.evaluate("entryProbe()")
        assert probe["pendingEntry"] == 0
        assert not desktop.errors, desktop.errors
        return {"return_reentry": "return -> arrive", "stages": stages, "probe": probe}
    finally:
        await context.close()


async def reduced(browser, url):
    context, page, desktop = await create_page(browser, url, reduced=True)
    try:
        await desktop.signal("collapse")
        await desktop.settled(True)
        probe = await page.evaluate("entryProbe()")
        assert probe["entryFrames"] == 0 and probe["pendingEntry"] == 0
        assert await page.locator(".stx-snapshot").count() == 0
        assert "dock" in desktop.stages() and not desktop.errors
        return {"reduced_motion": "direct dock without animation RAF"}
    finally:
        await context.close()


async def delayed_dock(browser, url):
    context, page, desktop = await create_page(browser, url, dock_delay_ms=450)
    try:
        await desktop.signal("collapse")
        await asyncio.wait_for(desktop.dock_requested.wait(), timeout=20)
        clips = []
        for delay in (0, 200):
            await page.wait_for_timeout(delay)
            clip = await page.locator(".stx-bar .sbar-root").evaluate(
                "element => getComputedStyle(element).clipPath"
            )
            clips.append(clip)
            assert clip == "none" or (
                clip.startswith("inset(")
                and all(
                    float(number) == 0
                    for number in re.findall(r"-?\d+(?:\.\d+)?", clip)
                )
            ), f"Real school bar disappeared while native dock was pending: {clip}"
        await desktop.settled(True)
        assert not desktop.errors, desktop.errors
        return {
            "delayed_dock": "revealed bar stays visible until native dock",
            "clip_paths": clips,
        }
    finally:
        await context.close()


async def reveal_curve(browser, url):
    context, page, desktop = await create_page(browser, url)
    try:
        await desktop.signal("collapse")
        await phase(page, "descent")
        background = await page.evaluate("""() => ({
            html: getComputedStyle(document.documentElement).backgroundColor,
            body: getComputedStyle(document.body).backgroundColor,
            stack: document.elementsFromPoint(innerWidth / 2, innerHeight / 3).map(node => ({
                tag: node.tagName, class: String(node.className).slice(0, 120),
                background: getComputedStyle(node).backgroundColor,
            })),
        })""")
        await phase(page, "reveal")
        clips = []
        for delay in (100, 150):
            await page.wait_for_timeout(delay)
            clips.append(
                await page.evaluate("""() => {
                const root = document.documentElement;
                const bar = document.querySelector('.stx-bar .sbar-root');
                const began = entryProbe().phases.findLast(entry => entry.phase === 'reveal');
                return {
                    phase: document.querySelector('.stx')?.dataset.entryPhase,
                    clip: bar ? getComputedStyle(bar).clipPath : null,
                    rootPercent: parseFloat(root.style.getPropertyValue('--school-entry-reveal')),
                    pooling: bar?.closest('.stx-bar')?.dataset.pooling,
                    schoolEntry: root.classList.contains('school-entry'),
                    elapsed: performance.now() - began.at,
                };
            }""")
            )
        result = {"reveal_curve": clips, "descent_background": background}
        print(json.dumps(result), flush=True)
        for clip in clips:
            assert (
                clip["phase"] == "reveal"
                and clip["pooling"] == "true"
                and clip["schoolEntry"]
            ), clip
            percent = re.findall(r"(-?\d+(?:\.\d+)?)%", clip["clip"])
            assert percent and 0 < float(percent[0]) < 50, clip
            assert abs(float(percent[0]) - clip["rootPercent"]) < 0.1, clip
            # Entry progress uses the clamped drawn-frame clock, not wall time.
            # Slow/suspended frames deliberately extend the reveal, so elapsed
            # wall time cannot predict its percentage. Check the visible curve
            # through bounded, decreasing clips and the matching CSS variable.
        assert clips[1]["rootPercent"] < clips[0]["rootPercent"], clips
        for name in ("html", "body"):
            assert background[name] == "rgba(0, 0, 0, 0)", background
        await desktop.settled(True)
        assert not desktop.errors, desktop.errors
        return result
    finally:
        await context.close()


async def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    server = None
    url = args.url
    if not url:
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            partial(SCHOOL.QuietHandler, directory=str(FRONTEND / "out")),
        )
        Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}"
    report = args.output / "report.json"
    results = (
        json.loads(report.read_text()) if args.captures_only and report.exists() else []
    )
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                if args.reveal_only:
                    result = await reveal_curve(browser, url)
                    (args.output / "reveal-report.json").write_text(
                        json.dumps(result, indent=2)
                    )
                    return
                jobs = (
                    []
                    if args.captures_only
                    else [
                        lambda: performance(browser, url, 1),
                        lambda: performance(browser, url, 2),
                        lambda: performance(browser, url, 1, cross=True),
                        lambda: lifecycle(browser, url),
                        lambda: lifecycle(browser, url, hidden=True),
                        lambda: reversal(browser, url, "compress"),
                        lambda: reversal(browser, url, "forge"),
                        lambda: return_reentry(browser, url),
                        lambda: reduced(browser, url),
                        lambda: delayed_dock(browser, url),
                    ]
                )
                if not args.capture_phase:
                    jobs.append(lambda: video(browser, url, args.output))
                chosen = (args.capture_phase,) if args.capture_phase else PHASES
                jobs.extend(
                    [
                        lambda: capture(browser, url, args.output, 1, chosen),
                        lambda: capture(browser, url, args.output, 2, chosen),
                    ]
                )
                for job in jobs:
                    result = await job()
                    existing = next(
                        (
                            entry
                            for entry in results
                            if result.get("capture_dpr")
                            and entry.get("capture_dpr") == result["capture_dpr"]
                        ),
                        None,
                    )
                    if existing is not None:
                        by_phase = {
                            sample["phase"]: sample for sample in existing["samples"]
                        }
                        by_phase.update(
                            {sample["phase"]: sample for sample in result["samples"]}
                        )
                        existing.update(result)
                        existing["samples"] = [
                            by_phase[name] for name in PHASES if name in by_phase
                        ]
                    else:
                        results.append(result)
                    print(json.dumps(result), flush=True)
                    (args.output / "report.json").write_text(
                        json.dumps(results, indent=2)
                    )
            finally:
                await browser.close()
    finally:
        if server:
            server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url")
    parser.add_argument(
        "--capture-phase",
        choices=PHASES,
        help="Limit still captures to one phase; combine with --captures-only",
    )
    parser.add_argument(
        "--reveal-only",
        action="store_true",
        help="Check live reveal curve and transparent descent backgrounds only",
    )
    parser.add_argument(
        "--captures-only",
        action="store_true",
        help="Reuse completed behavior/performance results and recapture media",
    )
    parser.add_argument("--output", type=Path, default=Path("/tmp/jarvis-entry-review"))
    asyncio.run(main(parser.parse_args()))
