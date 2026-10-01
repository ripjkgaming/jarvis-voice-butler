# Rendering optimization evidence — 1 October 2026

Historical evidence for the previous entry design. The complete reactor/blueprint
remake supersedes these entry measurements; its evidence is documented in
`SCHOOL_ENTRY_REMAKE.md`. The previous renderer is retained only as
`fixtures/school-transition-legacy.tsx` for reproducible historical probes.

The retained change reuses double-precision tracer coordinates and pool surface
samples in `components/hud/school-transition.tsx`. Each pool surface is evaluated
once per frame and reused by its fill and outline. Storage reserves normal growth
capacity, expands for larger geometry, and leaves with the transition scene.

Drawing commands, brightness, particle counts, DPR, timing, geometry, and lifecycle
logic are unchanged. The separately requested ambient particles are included in
both production builds used for the final comparison. The existing shared clock,
polling/visibility work and monitor fixes are preserved, not counted as new gains.

## Measured result

Headless Chromium 151.0.7922.34, 1600 × 900, DPR 1, Intel i5-13420H. The browser
reports SwiftShader. These are synthetic browser measurements, not native
WebKit/KWin pacing or physical GPU execution time. Heavy workloads were serialized.

The real-canvas microbenchmark warms both renderers, then runs ABBAAB batches of
480 frames. Values below are the medians of three batches per renderer. It measures
synchronous draw-command submission, not frame presentation or raster completion.
Sampled JavaScript allocations include objects collected during the batch; they
are estimates of allocation churn, not retained heap or total process memory.

| Workload | Before | Buffer reuse | Change |
| --- | ---: | ---: | ---: |
| Pool command submission / 480 frames | 40.3 ms | 21.2 ms | −47% |
| Tracer command submission / 480 frames | 54.5 ms | 45.4 ms | −17% |
| Combined scene command submission / 480 frames | 85.8 ms | 67.4 ms | **−21%** |
| Combined scene sampled JS allocations / 480 frames | 4.88 MB | 0.42 MB | **−91%** |

[All microbenchmark samples](render-evidence/2026-10-01/canvas-microbenchmark.json)
show combined submission ranges of 77.9–87.0 ms before and 66.0–70.9 ms after.

## Whole-UI profile and limits

The full-UI runs use fake bridge responses/native commands and wait for MIC LIVE
before transitions. “Cold” means the first transition in that page; “warm” means
the second. These runs add CPU/allocation sampling and paint/raster tracing, so
the timings include instrumentation overhead. One matched trace pair follows:

| State | Script ms before → after | Frame p50 before → after | Frame p95 before → after | Estimated missed 60 Hz slots before → after |
| --- | ---: | ---: | ---: | ---: |
| Normal, 3 s | 174.33 → 157.89 | 16.7 → 16.7 | 16.8 → 16.8 | 9 → 0 |
| Entry, cold | 336.11 → 214.32 | 16.7 → 16.7 | 50.0 → 33.3 | 58 → 33 |
| School, 3 s | 164.77 → 160.78 | 16.7 → 16.7 | 16.8 → 16.8 | 0 → 0 |
| Return, cold | 143.04 → 132.85 | 16.7 → 16.7 | 16.8 → 16.7 | 3 → 2 |
| Entry, warm | 263.53 → 214.97 | 16.7 → 16.7 | 33.4 → 16.8 | 63 → 18 |
| Return, warm | 120.80 → 137.66 | 16.7 → 16.7 | 16.8 → 33.4 | 4 → 26 |

The untouched return path also varied substantially. **No general FPS improvement
or native performance claim follows from this trace pair.** The retained gain is
the repeatable command-submission/allocation reduction above. Paint and raster
work remain: warm-entry traced Paint was 14.31 → 12.35 ms and RasterTask totals
132.94 → 80.31 ms, while warm-return RasterTask was 829.49 → 880.39 ms. These trace
totals may include multiple worker threads and are not frame-critical-path times.
No GPUTask events were exposed; hardware GPU work remains unmeasured.

Both builds were also profiled with reduced motion. Steady reduced-motion
normal/school states had zero pending RAFs and zero style recalculations. Hidden
states had zero app RAF callbacks, bridge/native requests, layout, paint and raster
work. CPU/heap sampling itself accounts for residual diagnostic work. Cancellation
and unmount cleanup were rechecked by the existing reversal scenarios.

Raw evidence: [control](render-evidence/2026-10-01/ui-control.json),
[buffer version](render-evidence/2026-10-01/ui-buffers.json), and
[environment](render-evidence/2026-10-01/environment.json). The
[original pre-ambient baseline](render-evidence/2026-10-01/original-pre-ambient.json)
is retained separately as context; it is not the control for the claimed gains.

Two font-setup experiments were rejected and removed: fewer canvas font writes
shifted work into text painting without a reliable total-time improvement. The
early profile runs overlapped by another test browser were excluded; final runs
were serialized. `school-return.tsx` is unchanged from this session’s starting copy.

## Correctness, screenshots and export

- 29 frontend Node tests pass, including 8 new buffer reuse/growth/command tests.
- 502 real-canvas comparisons at DPR 1 and 2 have zero differing RGBA channels.
- All 7 headless school-mode scenarios pass: equal-size delayed monitor movement,
  blocked-move recovery, ordinary/reduced-motion round trips, early/late reversal.
- Production `pnpm build` passes; `frontend/out` and `shell/ui` contain the final
  buffer renderer plus all approved ambient additions. Native build/activation is
  owned by the coordinating session; this rendering session did not restart it.

[Pixel results](render-evidence/2026-10-01/pixel-parity.json),
[before DPR 1](render-evidence/2026-10-01/before-dpr1.png),
[after DPR 1](render-evidence/2026-10-01/after-dpr1.png),
[before DPR 2](render-evidence/2026-10-01/before-dpr2.png), and
[after DPR 2](render-evidence/2026-10-01/after-dpr2.png).
The corresponding before/after PNG hashes are identical.
[Final HUD](render-evidence/2026-10-01/hud-final.png),
[entry screenshot](render-evidence/2026-10-01/entry-final.png),
[school results](render-evidence/2026-10-01/school-smoke.jsonl), and
[source/export/evidence hashes](render-evidence/2026-10-01/sha256.json) are saved here.

## Reproduction

Run from `jarvis_new/`, with an immutable pre-change source directory containing
`school-transition.tsx` and `school-return.tsx`, plus pre/post static export copies.
The probes cannot call native commands or the live bridge.

```bash
node --test frontend/tests/*.test.cjs
uv run --no-sync python frontend/tests/render-compute.py --before /path/to/saved-source --after /path/to/saved-optimized-source --output /tmp/render-compute.json
uv run --no-sync python frontend/tests/render-parity.py --before /path/to/saved-source --after /path/to/saved-optimized-source --output /tmp/render-parity
uv run --no-sync python frontend/tests/render-profile.py --export /path/to/export --trace --output /tmp/render-profile.json
uv run --no-sync python frontend/tests/school-mode-smoke.py --screenshots /tmp/school-review
```

Capture each export separately and serialize browser/CPU work. `render-profile.py`
blocks nonfixture network access, records frame timing, task/script/layout costs,
allocation samples, canvas font/text costs, paint/raster traces, polling and RAF
cleanup. Run without `--trace` when instrumentation overhead is undesirable.
