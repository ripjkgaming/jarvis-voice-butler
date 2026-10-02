# Cinematic school entry — 2 October 2026

Normal → school now folds the actual frozen HUD around its reactor, carries that
core through the monitor transfer and descent, then unfolds two articulated wings
into the school bar. Six reactor vanes establish the mechanical shape at capture;
they accompany the core in flight. The dock's beveled panels hinge down in a
centre-out cascade, with copper pins marking the joints. Scanning seams expose
the live bar after the panels seat.

This supersedes the entry choreography in [SCHOOL_ENTRY_REMAKE.md](SCHOOL_ENTRY_REMAKE.md).
The normal HUD, suit, school controls, return design, shared dashboard styles,
bridge and native shell are outside this change.

## Timing and rendering

| Phase | Previous drawn time | New drawn time |
| --- | ---: | ---: |
| Capture iris | 560 ms | 420 ms |
| HUD fold/capture | 820 ms | 720 ms |
| Transfer out, when needed | 640 ms | 520 ms |
| Transfer in, when needed | 720 ms | 600 ms |
| Descent | 960 ms | 760 ms |
| Wing assembly | 1,100 ms | 900 ms |
| Live bar reveal | 620 ms | 480 ms |
| Same-monitor total | **4,060 ms** | **3,280 ms** |

The same-monitor choreography is 780 ms (19.2%) shorter. Native acknowledgement
waits are additional. The clamped shared frame clock still prevents long frames
from skipping large sections of motion.

The HUD stays one immutable, full-resolution DOM/canvas capture. Its only animated
properties are transform and opacity. One affine matrix combines orthographic
tilt, a slight bank and unequal X/Y scale to fold it into a plane before capture.
The matrix starts at identity, keeps the captured reactor pivot fixed and has a
positive determinant throughout. There are no per-frame layout reads.

The first perspective version produced a material frame-pacing regression, also
confirmed in a private production build. The retained affine projection removes
the perspective division while preserving content, motion timing, rotation,
foreshortening and opacity. It uses parallel edges instead of a trapezoid. No
detail was hidden, raster density reduced, phase removed or duration increased.

The scene retains full, including fractional, device-pixel ratio. All seeded
fine particles remain as batched sharp filaments; one in six carries a soft glow.
The wings use one combined face path and one fill/stroke. Copper edges provide
assembly detail in place of the former weld-spark shower. This reduces texture
blending while adding larger, legible moving forms. The DPR-matched glow atlas
remains cached across viewport-only resizes. No renderer-owned RAF, frame-time
gradient construction, particle allocation or additional full-screen layer was
introduced.

## Lifecycle validation

The existing geometry and native handshake are retained: original normal-window
geometry is saved before reduced-motion shortcuts; fresh nonce acknowledgements
and matching viewport/output geometry gate cross-monitor arrival. Offscreen
entry suspends RAF; hidden/reduced entry completes through docking. Reversal
removes the snapshot, RAF, listeners and observer and suppresses late callbacks.
The bar remains revealed during a delayed dock acknowledgement. Existing IPC,
geometry and overall deadlines remain bounded.

One newly reproduced defect was fixed: a thrown canvas construction error used
to escape before the effect could install recovery. Scene creation is now guarded,
so it follows the same geometry-capture/direct-dock path as a missing context.

The focused Node suite passes **53 tests**, including **17 new production-component
lifecycle tests** and **8 renderer tests**. New coverage checks the affine
fold's initial pose, fixed pivot, bank, positive determinant and final contraction
without relayout; raised/locked wing geometry, history-independent poses,
missing/throwing canvas, stale/equal-size monitor reports, full DPR, initial and
dock IPC stalls, hidden/offscreen/reduced behavior, return-to-school, early/late
reversal, delayed dock visibility, and zero retained work. These tests execute
production code against private mocked time, canvas and native/bridge APIs.

Real-canvas checks also compare the last capture frame with the first compression
frame and the last assembly frame with the first reveal frame. All four new-scene
comparisons (DPR 1 and 2) match every RGBA byte across **14.4 million pixels**.
This verifies continuity at those boundaries; the new design intentionally does
not match the old renderer's pixels.

The isolated TypeScript check passes. Final combined integration checking and
the integrated production export belong to the coordinating chat.

The exact final affine source also passes a private production build and twelve
browser validation groups: eight lifecycle/reveal checks, an uninterrupted video,
two sets of DPR phase stills and a six-pose sequence. Full-resolution intermediate
poses were inspected and the affine shape accepted. The final production artifacts
are separate from the historical perspective trial:

- [Final motion contact sheet](entry-evidence/2026-10-02-cinematic/production/final-validation/contact-sheet.png)
- [Final uninterrupted animation](entry-evidence/2026-10-02-cinematic/production/final-validation/media/normal-to-school.webm)
- [Perspective versus affine poses](entry-evidence/2026-10-02-cinematic/production/pose-comparison/contact-sheet.png)
- [Production lifecycle/media report](entry-evidence/2026-10-02-cinematic/production/final-validation/report.json)
- [Final focused Node suite](entry-evidence/2026-10-02-cinematic/production/node-tests-final.txt)
- [Complete-fold matrix assertions](entry-evidence/2026-10-02-cinematic/production/fold-lifecycle-final.txt)

## Renderer cost

The matched Chromium benchmark uses the same visible 1600 × 900 canvas with
independent scene state, 80 warmup draws per renderer/phase, and 12 samples of
20 draws per renderer in alternating ABBA/BAAB order. Values are median
**CPU-side canvas submission milliseconds per frame**, excluding the DOM HUD
transform, GPU completion, native IPC and layout. Small deltas are noisy.

| DPR | Phase | Before ms/frame | After ms/frame |
| --- | --- | ---: | ---: |
| 1 | Capture | 0.1275 | 0.0600 |
| 1 | Compression | 0.1400 | 0.0775 |
| 1 | Transfer out | 0.0350 | 0.0500 |
| 1 | Transfer in | 0.0450 | 0.0400 |
| 1 | Descent | 0.0625 | 0.0725 |
| 1 | Assembly | 0.0550 | 0.0350 |
| 1 | Reveal | 0.0550 | 0.0150 |
| 2 | Capture | 0.1200 | 0.0550 |
| 2 | Compression | 0.1300 | 0.0650 |
| 2 | Transfer out | 0.0400 | 0.0400 |
| 2 | Transfer in | 0.0675 | 0.0400 |
| 2 | Descent | 0.0450 | 0.0500 |
| 2 | Assembly | 0.0600 | 0.0250 |
| 2 | Reveal | 0.0550 | 0.0200 |

Capture, compression, assembly and reveal submit less work. The flight vanes
increase descent cost and DPR 1 transfer-out cost slightly. These results are
separate from total transition duration and do not claim a universal speedup.
Raw samples, source hashes and boundary pixels:
[renderer-comparison.json](entry-evidence/2026-10-02-cinematic/renderer-comparison.json).

## Final production duration and scheduling

The final comparison uses immutable private production exports with all other
UI sources held fixed. Three samples per source/case alternate baseline/final
order by case and repetition, for **18 unrecorded samples**. The viewport is
1600 × 900; cross-monitor movement includes the same mocked 900 ms delay.
Samples began after the coordinator's backend test process exited. Normal system
and application load remained. Builds, screenshots and videos were not running
during the acceptance samples.

| Case | Baseline median duration | Final median duration | Baseline frame p95 | Final frame p95 |
| --- | ---: | ---: | ---: | ---: |
| Same monitor, DPR 1 | 4,168.87 ms | **3,399.86 ms** | 16.7–16.8 ms | **16.8 ms** |
| Same monitor, DPR 2 | 4,234.39 ms | **3,449.06 ms** | 16.8 ms | **16.8 ms** |
| Cross monitor, DPR 1 | 6,500.91 ms | **5,474.18 ms** | 16.7–16.8 ms | **16.7–16.8 ms** |

The affine fold removes the perspective trial's material 33.3 ms p95 regression.
Frame pacing is not identical to the original: estimated missed 60 Hz intervals
over the three runs were **0/0/0 → 2/4/3** for same-monitor DPR 1,
**1/2/1 → 5/5/8** for DPR 2, and **0/0/0 → 2/2/3** for cross-monitor DPR 1.
Two final DPR 2 runs each had one roughly 50 ms gap; the third peaked at 33.4 ms.
There were no gaps over 34 ms in the other final cases. These residual outliers
remain disclosed; no universal smoothness or native FPS claim is made.

The renderer submission costs above are unchanged by this DOM-only refinement
and remain separate from transition duration and frame scheduling.

- [Final paired metrics, order and limitations](entry-evidence/2026-10-02-cinematic/production/final-metrics-summary.json)
- [All eighteen raw samples](entry-evidence/2026-10-02-cinematic/production/final-balanced/report.json)
- [Private production build](entry-evidence/2026-10-02-cinematic/production/final-build.txt)
- [Seven final production school scenarios](entry-evidence/2026-10-02-cinematic/production/school-smoke-final.jsonl)
- [Source/export/cleanup verification](entry-evidence/2026-10-02-cinematic/production/final-verification.json)

The final shared transition and renderer match the checked private sources.
All **173 source hashes** and **55 private exported file hashes** are verified.
All owned private HTTP servers and browser sessions are closed. The coordinator
accepted the affine poses and final comparison; implementation is refrozen.

## Historical perspective measurements (superseded)

Three unrecorded runs per case use a 1600 × 900 viewport and the fixed private
development app. The table reports median elapsed time from the entry signal to
settled school mode, including mocked native work. Cross-monitor runs inject a
900 ms native move delay.

| Case | Before median | After median |
| --- | ---: | ---: |
| Same monitor, DPR 1 | 4,210.59 ms | 3,446.51 ms |
| Same monitor, DPR 2 | 4,273.89 ms | 3,568.25 ms |
| Cross monitor, DPR 1 | 6,482.87 ms | 5,587.39 ms |

The sequence completes sooner, but the perspective DOM fold has a measurable
scheduling cost in this headless development setup. Same-monitor frame p95 rose
from 16.7–16.8 ms to 33.3–33.4 ms. The candidate also has more estimated missed
60 Hz intervals; baseline DPR 1 counts were 1/0/0 versus 15/14/28 after, and DPR 2
counts were 2/2/5 versus 31/27/27 after. These observations are retained, not
reclassified as a frame-rate improvement. Some baseline work overlapped an
unrelated CPU benchmark; timing isolation is therefore imperfect. The separate
alternating renderer benchmark above helps distinguish drawing submission from
the full DOM/compositor path.

Raw runs: [before](entry-evidence/2026-10-02-cinematic/baseline/report.json) and
[after](entry-evidence/2026-10-02-cinematic/after/report.json). Native compositor
motion remains unverified in this session.

A bounded private-page experiment added paint containment, isolation, hidden
backfaces and disabled descendant `will-change` hints. Two control/variant runs
per DPR retained a roughly 33.3 ms p95; the ordinary DPR 2 control already matched
the variant's missed-interval count. The hints were discarded because they did
not show a consistent improvement. No production styling change came from that
experiment. [Raw control/variant runs](entry-evidence/2026-10-02-cinematic/containment-experiment.json).

## Evidence and scope

Evidence is in [entry-evidence/2026-10-02-cinematic](entry-evidence/2026-10-02-cinematic).
The before and after app snapshots differ only in the two entry implementation
files. All background UI and integration sources are held fixed. Browser runs
use a disposable Next development copy, headless Chromium, mocked native/bridge
operations, and a network allowlist. Screenshot/video runs are separate from
performance runs. Fractional still captures deliberately freeze the drawn pose;
videos play without that freeze.

The initial perspective browser pass completed 20 groups: nine unrecorded performance runs,
eight lifecycle/reveal checks, one uninterrupted video and two sets of DPR phase
stills. All seven existing school-mode smoke scenarios pass against this private
URL. The smoke harness hides only the Next development toolbar because its portal
intercepted a bar click after the mocked window shrank; the initial obstruction
is retained in the evidence. The development badge visible in some stills is not
part of the production transition.

- [Before contact sheet](entry-evidence/2026-10-02-cinematic/baseline-contact-sheet.png)
- [Perspective trial contact sheet](entry-evidence/2026-10-02-cinematic/after-contact-sheet.png)
- [Before uninterrupted video](entry-evidence/2026-10-02-cinematic/baseline/normal-to-school.webm)
- [Perspective trial video](entry-evidence/2026-10-02-cinematic/after/normal-to-school.webm)
- [Node test log](entry-evidence/2026-10-02-cinematic/node-tests.txt)
- [School smoke results](entry-evidence/2026-10-02-cinematic/school-smoke.jsonl)
- [App metrics and limitations](entry-evidence/2026-10-02-cinematic/metrics-summary.json)
- [Frozen before sources](entry-evidence/2026-10-02-cinematic/baseline-source-sha256.json)
- [Frozen after sources](entry-evidence/2026-10-02-cinematic/after-source-sha256.json)

The refinement uses private production builds through the direct Next CLI in a
temporary frontend copy. This session did not write the workspace's `out` or
`shell/ui`, run a native build, restart the live app, open a desktop browser,
commit or push. Final integrated build and activation remain with the coordinating
chat. Headless Chromium timings do not establish native WebKit/KWin or GPU frame rate.

Reproduce focused source tests from `frontend/`:

```bash
node --test tests/school-entry-scene.test.cjs tests/school-entry-lifecycle.test.cjs tests/school-lifecycle.test.cjs tests/school-return-lifecycle.test.cjs tests/animation-clock.test.cjs
```

From `jarvis_new/`, point the browser harness at an already running **private copy**
of the development app. It never starts or builds the application:

```bash
uv run --no-sync python frontend/tests/school-entry-cinematic-evidence.py --url http://127.0.0.1:4059 --stage after --output /tmp/jarvis-cinematic-evidence
uv run --no-sync python frontend/tests/school-entry-cinematic-compare.py --before frontend/tests/entry-evidence/2026-10-02-cinematic/school-entry-scene-before.ts.txt --after frontend/lib/school-entry-scene.ts --output /tmp/jarvis-cinematic-renderer
```
