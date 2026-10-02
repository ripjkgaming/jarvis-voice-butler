# Jarvis optimization, Insights and paper-market integration — 2 October 2026

Base: `29112f4029c84e0d9eb7b283883964672da1cb1b` on `jarvis-voice-fixes`.
This report covers the subsequent combined implementation, rather than replacing
the earlier `final-qa-2026-10-02.md` baseline report.

## Result

Jarvis now includes a lazy Insights drawer for authoritative ccusage reports and
the seven-day paper-market ledger, available from the normal HUD and school mode.
The school-entry animation preserves the complete HUD and full device-pixel
ratio, collapsing through a banked plane into the reactor and articulated dock.
Python changes reduce event-loop blocking and bound interrupted work without
changing voice models or synthesis settings.

The coordinator rebuilt and activated the desktop app only after the frozen
source passed integration checks. The refresh verified there was exactly one
idle shell and no active/starting call, preserved microphone mute state, and
confirmed the replacement shell's authenticated health and microphone endpoints.
The settled read-only check at 161 seconds uptime found exactly one shell,
healthy endpoints and no active room, call or wake in progress.

## Verification

| Check | Result |
| --- | --- |
| Full Python suite | **2,893 passed, 11 skipped** in 228.56 s; 315 Python source/test hashes unchanged during the run. Credentials, display/D-Bus access and live-model tests were disabled; private test state was used. |
| All frontend Node tests | **170 passed**, Node 24.19.0. |
| TypeScript | `tsc --noEmit --incremental false` passed. |
| Production frontend export | Passed; all **60 exported files** match `shell/ui` byte-for-byte. Product source hashes unchanged during build. |
| Native desktop | Offline debug and release builds passed; existing compiler warnings remain. No native test window was launched. |
| Insights in production export | Normal 1600 px, narrow 520 px, school mode, all ranges, offline states, keyboard/focus and lazy closed-state behavior passed. |
| School mode in production export | Seven scenarios passed: regular/reduced motion, bar heights, delayed equal-size monitor moves, blocked moves and early/late reversal. |
| Suit in production export | Normal, school, narrow and DPR 2 checks passed; lazy asset load, native focus leases, close cleanup and expanded-school reload recovery passed. |
| Populated paper panel | Synthetic gains/losses, holdings, decisions, fills, P&L toggle, missing-mark chart gaps, stale valuation labels, and desktop/mobile scrolling passed. |
| Populated paper inside full app | 1600 px, 520 px and school mode passed: three holdings, P&L switching, chart gaps, bounded layout, Escape/focus restoration, no requests after close and no page/console errors. |
| Live authoritative sources | Today/week/month/all report totals, apps, models, timeline, range and source metadata match the ccusage upstream. The paper bridge matches its ledger service. Private usage figures were not copied into this report. |
| Publication review | No known private credential values or local state/ledger files in the changed files; whitespace check passed. |

The production browser pass fingerprinted 149 frontend/browser-test files and
observed no changes. All browser interaction used isolated headless Chromium,
mocked native IPC and synthetic reports. No real desktop navigation, calculator,
browser-tab launch, microphone recording or speaker playback was used for QA.
Logs and screenshots are under `/tmp/jarvis-integration-qa-2026-10-02/` on the
coordinator's machine. The committed source manifest identifies the final files.

## Review corrections

- Interrupted subprocess cleanup now bounds inherited-pipe draining after its
  direct child exits. The original 0.1 s timeout reproduction completes in about
  153 ms; 26 focused tests and 16 independent stress cases found no task,
  descriptor or direct-child leaks.
- Insights and suit controllers release their transition latch on explicit
  cancellation and invalidate delayed native replies. Independent real-React
  checks cover batched collapse/expand, already-open panels and stale focus work.
- Paper fills, account state, decisions and equity observations commit atomically;
  expiry is checked at every execution boundary. Independent review passed 201
  backend tests plus concurrency, restart, expiry and fractional-accounting probes.
- HTTP proxy deadlines cover trickled headers and bodies, including correctly
  completed HTTP/1.0 responses. Hidden paper panels abort requests and stop timers.
- A legacy canvas golden differed between Node 22 and 24 only by at most
  `3.55e-15` in opacity/width values. Hash inputs now normalize to nine decimal
  places; geometry, style and command-order mutation checks remain effective.
  Renderer/product code was not changed for this test correction.

## Measured improvements and limits

The interleaved private production comparison used three repetitions per source
and case. Median school-entry duration changed **4.169 → 3.400 s** at DPR 1,
**4.234 → 3.449 s** at DPR 2, and **6.501 → 5.474 s** with a mocked 900 ms monitor
move. Final frame p95 was 16.7–16.8 ms. A few extra missed intervals remain,
including two approximately 50 ms DPR 2 gaps; this is not a native WebKit/KWin FPS
claim. Full methodology, visual evidence and the discarded perspective variant
are in `frontend/tests/SCHOOL_ENTRY_CINEMATIC.md`.

Injected 120 ms telemetry work reduced median maximum voice-loop lag from
119.315 to 0.676 ms. A generated 20,000-file search fixture retained identical
results while median query time fell from roughly 505 to 270 ms. Serialized
Piper ownership prevents overlapping/obsolete work. These are controlled
responsiveness and lifecycle measurements, not physical speech-latency or new
perceptual-quality measurements. See `runtime-optimization-2026-10-02.md`.

## Running experiment

The private ledger began on **2 October 2026 at 08:33:52 Singapore time**, with
exactly **USD 1,000 paper cash**, and expires on **9 October at 08:33:52**. Its
worker is verified `gpt-6-astra` at **high** effort; the development chats used
Astra at **ultra** effort. One bounded live no-trade preflight passed. At activation
the market was closed, with zero holdings and zero fills.

This is a local paper exchange using timestamped, unofficial Yahoo Finance
quotes. It does not create a brokerage account or send live orders. The ledger
and read-only loopback service are separate from the UI; an active Codex heartbeat
invokes the sole trading command every 30 minutes and reports the frozen result
after expiry. Missing prices remain unknown. Completion of this implementation
does not claim seven days of trading results. See `docs/paper-market.md` for fill
rules, source limitations and the persistence contract.

The requested local input lock and bounded lid-close keep-awake/recovery units
were installed outside Git. Ordinary KDE keyboard/pointer devices restore at
10:00 Singapore time on 2 October; power/lid controls stay enabled and the normal
password lock remains. Fourteen isolated recovery tests, independent review and
an actual stop/restore check passed. Failures/session restarts intentionally
restore input early. The keep-awake deadline includes at most one hour after the
experiment for final reporting, then normal lid-close behavior resumes. Continued
local execution requires power, the user session and network connectivity.
