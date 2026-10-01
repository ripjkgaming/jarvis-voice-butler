# School entry remake — 1–2 October 2026

Normal → school now contracts the actual STARK HUD into its reactor, transports
that core when a monitor change is necessary, and fabricates the school bar from
expanding blueprint rails and interlocking plates. Two luminous seams reveal the
real, interactive bar from its centre. The old edge lap and liquid pool are gone.
The normal HUD, school controls, ambient particles, voice-link choreography and
school → normal design are preserved.

## Choreography

| Phase | Drawn time | Visual |
| --- | ---: | --- |
| Aperture | 560 ms | Segmented concentric rings and motes converge on the actual reactor centre. |
| Compress | 820 ms | One frozen full-resolution HUD contracts into the core. |
| Transfer, when required | 640 + 720 ms | Core leaves the source output and arrives through the facing edge of the primary output. |
| Descent | 960 ms | Core follows a curved flight path to the bar centre. |
| Forge | 1,100 ms | Blueprint wings expand and staggered plates join the target panel rectangle. |
| Reveal | 620 ms | Scanning seams expose the live school bar; docking keeps it visible. |

Native geometry waits are additional and use real-time deadlines. Animation
progress uses the shared frame clock. Late frames do not skip a large portion of
the choreography. A single transition-owned RAF updates and paints all stages.

## Reliability and continuity

- The frozen HUD is attached in a layout effect before the first post-signal
  paint, including while the native geometry measurement is pending.
- A new entry saves the original frame/content/output geometry before any
  reduced-motion shortcut. Failed measurement clears a stale previous record.
- Each window move is followed by a fresh nonce-based geometry acknowledgement.
  Equal-sized monitors cannot be mistaken for each other. Cross-monitor arrival
  stays transparent until the reported output and viewport both match.
- Entry cancellation disposes its frame clock, cancels pending RAF, removes the
  snapshot/listeners/observer and prevents later native stages or UI callbacks.
- Hidden windows complete quietly by docking; offscreen transitions suspend RAF
  and resume when visible. Reduced motion docks directly after geometry capture.
  An overall 15-second frontend deadline complements the existing native watchdog.
- The revealed bar stays unclipped while docking is acknowledged. Failed geometry
  or unavailable canvas rendering falls back to native docking.
- Voice and mic hooks remain mounted in HudShell; the cloned HUD has no React
  effects or voice connection. Native audio/backend behavior is unchanged.
- The native primary-monitor keeper is retained. No Rust changes or live desktop
  actions were made for this remake.

## Rendering and measurement

The renderer keeps full device-pixel ratio, particle detail and brightness. Glow
textures are cached at native DPR; seeded particles are allocated once; path
segments are batched. Its frame function performs no layout reads, gradient
construction or particle-object allocation. One compositor-transformed HUD copy
replaces the former two full HUD copies. Only transform/opacity and the bar reveal
clip change during those portions of the sequence.

Initial isolated production-export measurements, 1600 × 900 in headless Chromium:

| Case | Duration | Frame median | Frame p95 | Gaps over 34 ms |
| --- | ---: | ---: | ---: | ---: |
| DPR 1 | 4.22 s | 16.7 ms | 16.8 ms | 0 |
| DPR 2 | 4.29 s | 16.7 ms | 33.3 ms | 1 |
| Cross-monitor, DPR 1 | 6.58 s | 16.7 ms | 16.7 ms | 0 |

The cross-monitor case injects a 900 ms native move delay. These numbers describe
synthetic Chromium scheduling and include the mocked shell/React work. They do
not establish native WebKit/KWin refresh pacing or hardware GPU performance.
The DPR 2 result includes longer frames; no universal frame-rate claim is made.

## Final refinement

The retained refinement reuses repeated compression easing, evaluates the
shared descent envelope once per frame, and rebuilds the glow atlas only when
DPR changes. Particle positions, path commands, alpha, colors, phase durations,
backing dimensions and drawing order remain identical.

- 1,344 recorded draw-command frames match exactly.
- 94 real-canvas comparisons match every RGBA byte, covering 85,248,000 pixels
  across all phases, four transfer directions, DPR 1/2 and full-size boundaries.
- The resize scenario constructs 6 gradients instead of 15; the cached image is
  retained across window-size changes on the same DPR.
- In the renderer-only Node benchmark with a coordinate sink, median compression
  computation fell from 55.1 to 46.6 µs/frame and descent from 5.16 to 2.92 µs/frame.
- The matched shared-canvas Chromium command-submission benchmark measured
  compression at 0.255 → 0.2375 ms/frame and descent at 0.0775 → 0.075 ms/frame.
  These small browser differences are noisy and are not a whole-UI speedup claim.

An earlier two-canvas submission experiment had a stacking/visibility bias and
was discarded for timing conclusions. Its pixel comparisons remain valid.
Unchanged-phase variation and rejected tick-cache/courier experiments are not
counted as improvements. The captured UI performance/media above predates this
pixel-identical math refinement; the final production export is covered by the
final regression suite.

## Verification and evidence

All 50 frontend Node tests pass (including 8 historical fixture tests).
The new renderer tests cover DPR, deterministic drawing, all four transfer
orientations, exact descent/bar geometry, transparent final reveal and disposal.
Geometry tests exercise fresh nonces, equal-sized displays, stalled IPC/HTTP and
cancellation. Browser checks cover the actual production export with every bridge
and native command mocked, including reversals, hidden/offscreen behavior,
reduced motion and a 450 ms delayed dock acknowledgement.

`school-entry-evidence.py` separates unrecorded performance runs from screenshots
and video. Capture-only screenshots can hold a drawn phase; the motion recording
runs freely. A dark browser background represents the desktop behind the native
transparent window; it is not a production UI layer.

Reproduce from `jarvis_new/` after building the frontend:

```bash
node --test frontend/tests/*.test.cjs
uv run --no-sync python frontend/tests/school-entry-evidence.py --output /tmp/jarvis-entry-review
uv run --no-sync python frontend/tests/school-mode-smoke.py
```

The previous renderer exists only in `fixtures/school-transition-legacy.tsx` for
historical optimization evidence. Its eight golden tests are archival checks, not
coverage of the new entry design. The old renderer comparison scripts now require
explicit saved before/after sources.

Saved evidence: [browser report](entry-evidence/2026-10-01/report.json),
[uninterrupted preview](entry-evidence/2026-10-01/normal-to-school.webm),
[compression](entry-evidence/2026-10-01/entry-dpr2-compress.png),
[blueprint assembly](entry-evidence/2026-10-01/entry-dpr2-forge.png),
[reveal](entry-evidence/2026-10-01/entry-dpr2-reveal.png), and
[reveal/transparency diagnostic](entry-evidence/2026-10-01/reveal-report.json).
The video is encoded at 25 fps by Playwright; it is not a refresh-rate measurement.

## Final source/export freeze

The final production build/export passes, all 50 Node tests pass, and all seven
existing school-mode regression scenarios pass against that export. These cover
normal/reduced-motion round trips, delayed equal-size monitor moves, blocked-move
recovery, mic/menu interaction and early/late reversals. All 50 exported files
match `shell/ui` byte-for-byte (excluding the separately retained globe assets).
Source/export hashes are recorded in
[source-export-sha256.json](entry-evidence/2026-10-01/source-export-sha256.json).

Final logs: [build](entry-evidence/2026-10-01/build-final.txt),
[Node tests](entry-evidence/2026-10-01/node-tests-final.txt),
[round trips](entry-evidence/2026-10-01/school-smoke-final.jsonl).

The refinement baseline, raw measurements, rejected timing caveat and source
hashes are recorded in
[refinement-manifest.json](entry-evidence/2026-10-01/refinement-manifest.json).
Reproduce the renderer comparison without running the application:

```bash
uv run --no-sync python frontend/tests/school-entry-compare.py --before frontend/tests/entry-evidence/2026-10-01/school-entry-scene-before-refinement.ts.txt --output /tmp/school-entry-comparison
```

Implementation and optimization are complete. The coordinating chat owns the
separate independent bug-test pass and subsequent native build/activation.
This implementation pass did not restart or demonstrate the live application.
