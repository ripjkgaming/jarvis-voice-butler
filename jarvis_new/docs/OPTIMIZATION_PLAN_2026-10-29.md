# Jarvis optimization plan — 29 October 2026

## Objective and constraints

Improve responsiveness, steady-state resource use, and reliability in the existing
Jarvis application. Preserve the substantial pre-existing working-tree changes,
current model choices, UI design, privacy rules, and external-action authorization.
Use offline regression tests; do not exercise live microphones, send messages,
restart services, or spend inference quota during verification.

## Work streams and acceptance criteria

1. **Baseline and integration (primary agent)**
   - Inspect repository instructions and current architecture.
   - Run the offline Python suite, distinguish existing failures from regressions.
   - Review each implementation and run affected suites and frontend checks.
2. **Backend request budgets (backend subagent)**
   - Investigate model fallback/retry paths and cooldown handling.
   - Ensure exhausted credentials are skipped and fallbacks share a deadline.
   - Prove behavior with deterministic transport/clock tests.
3. **Voice backpressure (voice subagent)**
   - Bound in-call microphone and synthesis queues.
   - Preserve opening command words; explicitly abort capture on overflow.
   - Discard obsolete queued synthesis work on cancellation.
   - Verify ordering, queue limits, and lifecycle behavior without audio hardware.
4. **Frontend polling (frontend subagent)**
   - Find overlapping request loops and stale updates.
   - Serialize relevant polling; abort requests on teardown and pause hidden views.
   - Test slow requests, disposal, and visibility transitions; typecheck/build.
5. **Caption storage (primary agent)**
   - Avoid full-history reads in HUD captions and pruning.
   - Preserve Unicode, historical caption formats, and private-session isolation.
   - Prevent concurrent live-caption writers sharing a temporary filename.
   - Serialize append/prune transactions; verify bounded reads and concurrent writes.
6. **Evidence and handoff (primary agent)**
   - Record exact checks, failures, and local performance measurements.
   - Document remaining limitations and leave changes ready for review.

## Priority and rollout

Correctness and cancellation come first, then avoidable repeated work. Implement
small fixes at existing boundaries rather than replacing the application stack.
Add failing regression tests before behavioral changes. Build the frontend directly
with Next rather than the package build script, which overwrites tracked shell UI
assets. Source changes are reviewable locally; activating them in a running Jarvis
installation is outside this pass.

## Results

### Implemented changes

- **Backend retry budget** (`src/mercury_decide.py`): skip quota-exhausted keys
  during their cooldown; use one monotonic deadline across fallback attempts;
  discard late answers; still remember a late quota response; reject invalid
  timeout/input values without dispatching a request. A synthetic pair of calls
  now makes two exhausted-key requests rather than four. After a two-second
  failure, a three-second budget passes only its remaining second to the fallback.
- **Memory retrieval** (`src/memory.py`): reuse parsed note paragraphs and lowercase
  text. Filesystem identity, nanosecond modification/change timestamps and size
  detect edits and atomic replacements. Retain at most 64 files / 256 Ki source
  characters, bypass large files, and calculate query terms/recency once. A reproducible
  24-note synthetic benchmark measured 1.5562 ms cold versus 1.0793 ms warm
  (1.44x). This is not an end-to-end voice latency measurement.
- **Speech backpressure and cleanup** (`src/games/voice/tts.py`): retain at most two
  queued PCM chunks, interrupt blocked producers, serialize speaker turns, avoid
  idle queue reads consuming executor threads, reset playback state on failure,
  and propagate cancellation even when it arrives during native-worker cleanup.
- **Microphone overflow** (`src/wake_client.py`): reuse bounded capture buffering
  for the secondary in-call microphone. Overflow aborts the turn instead of
  silently forwarding a command with missing opening words.
- **Frontend requests** (`frontend/lib/visible-poll.ts`, project/draft clients and
  views): serialize polls, pause hidden windows, refresh on return, abort reads
  on hide/unmount, and reject stale results. Kept visual design unchanged. Fixed
  existing desk-test VM dependency stubs and formatted the existing Ops JSX block
  so production checks can complete.
- **Caption I/O** (`src/log_tail.py`, `src/hud_events.py`, `src/bridge.py`): share
  a reverse block reader; read only recent captions; prune bounded recent history;
  serialize append/prune across threads and POSIX processes; publish complete files
  atomically with unique temporary names; tolerate corrupt UTF-8 history. Private
  phone captions still never enter the shared desktop log.
- **Muted chess text** (`src/games/chess/game.py`, `src/games/tui.py`):
  separate text-log delivery from audible speech callbacks, keeping asynchronous
  commentary and results visible while preserving the existing mute default.
- **Private-call revocation** (`src/agent.py`): stop revoked turns at entry and
  after local operation errors, preventing an ended private call from falling
  through to a model response. Repaired a stale test fixture so it actually reaches
  the current routing path and added both demonstrated regressions.

### Regression evidence

Before their corresponding fixes, regression tests reproduced excessive retries,
multiplied timeouts, full-history caption reads, invalid-byte crashes, temporary
file collisions, unbounded synthesis, incomplete cancellation cleanup, and revoked
phone turns reaching the fallback path. Final review caught and tested two further
edges: recording quota cooldown after an expired response, and cancellation arriving
during native speech cleanup.

### Local caption benchmark

Reproduce with `uv run python scripts/bench_caption_storage.py`. The script uses
synthetic local files in a temporary directory, checks output parity, and measures
100 reads per workload. These wall-clock figures vary with machine load and cache
state. They compare file reads only, not UI frame rate or whole-turn latency.

| History | Bytes | Previous full read median | Tail reader median |
| --- | ---: | ---: | ---: |
| 250 lines | 126,750 | 0.1481 ms | 0.0184 ms |
| 5,000 lines | 2,535,000 | 4.1949 ms | 0.0521 ms |
| 20,000 lines | 10,140,000 | 20.6727 ms | 0.0192 ms |

Raw results: `work/optimization-2026-10-29/caption-benchmark.json`.
A separate instrumented regression proves reading one latest caption from a
2.5 MB history consumes at most one 4,096-byte block.

### Local memory benchmark

Reproduce with `uv run python scripts/bench_memory_recall.py`. The benchmark uses
24 synthetic notes, 48 paragraphs per note, and 50 cold/warm pairs in a temporary
vault. It asserts retrieval parity and accesses no user vault or model. Median
recall was 1.5562 ms cold and 1.0793 ms warm (1.44x). This workload fits in the
bounded cache; gains vary with note sizes, filesystem, query terms, and contention.
Raw results: `work/optimization-2026-10-29/memory-benchmark.json`.

### Verification

- Caption/HUD/bridge suites: **133 passed**.
- Backend/voice final focused suites: **53 passed**.
- Adjacent existing backend/voice suites: **672 passed**, 2 slow tests deselected
  (followed by the final focused cancellation regressions).
- Private-call suites: **103 passed**.
- Frontend Node suites: **253 passed**.
- `pnpm exec tsc --noEmit`: passed.
- `pnpm exec next build`: production build and static export passed. Five existing
  unrelated lint warnings remain; generated `shell/ui` assets were not overwritten.
- Ruff passes the new helper, benchmark, new regression tests, decision module,
  chess speech module and phone test file. Whole-file checks on the already dirty
  bridge/agent sources include pre-existing lint findings.
- Follow-up/input recovery suites: **17 passed**.
- Current chess command suites: **762 passed**.
- Wake responsiveness/non-slow voice engine suites: **17 passed**, 2 slow tests deselected.
- `git diff --check`: passed.

The broad offline Python audit finished in 853.07 seconds with **6,916 passed,
69 failed, 7 skipped, 15 deselected**. It began before the final phone and review
fixes completed, so its source snapshot differs from the final focused suites.
Detailed artifacts: `work/optimization-2026-10-29/python-suite.log` and
`work/optimization-2026-10-29/python-suite.xml`. Test counts above overlap and
must not be added together.

Failure triage:

- **58 character/switch tests** expect Friday/Ultron or character switching, which
  the pre-existing single-Jarvis changes intentionally removed. Those changes and
  deleted character files were preserved.
- **6 chess speech assertions** assume the old unmuted commentary default. The
  existing settings change makes commentary muted by default; restoring unsolicited
  speech merely to satisfy these assertions would reverse that existing choice.
- **1 linked-turn test** calls `_cancel_linked_turn`, whose state, socket route,
  dispatch and implementation were coherently removed before this pass. The stale
  integration coverage was left visible.
- **2 private-call regressions** in the broad process used its already-loaded
  agent implementation; both pass in the final focused phone suite after the fix.
- **1 source-introspection latency test** fails in the broad process but passes
  against freshly loaded current source. The broad run overlapped edits to the
  inspected source file; no product failure reproduced in isolation.
- **1 chess TUI test** exposed muted asynchronous text missing from the visible
  log. The text/voice callback separation fixes it without changing its existing
  test or the mute default. Five new regressions pass; the combined relevant
  suites finish with **73 passed and only the 6 known mute-default assertions
  failing**.

The final rerun of all **69 failed node IDs** against the final source produced
**4 passed / 65 failed** in 5.46 seconds: the two revoked-call cases, source
introspection check, and TUI check now pass. The remaining 65 are exactly the
58 character/switch, 6 old mute-default, and 1 removed linked-turn expectations
listed above. Rerun artifacts are `work/optimization-2026-10-29/audit-failures-rerun.log`
and `.xml`; `summary.json` stores the counts and benchmark results. The rerun uses
`uv run python -m pytest` so both the repository and `src` imports resolve.

The repository-wide audit is therefore not green. Remaining legacy behavior
expectations are documented rather than silently skipped or changed to hide them.
All planned optimization implementations and their focused verification are
complete. No active implementation or verification work remains in this pass.

### Operational limits

No live microphone, native-model performance tuning, inference-quota spending,
service restart or desktop activation was used as validation. An existing slow
Whisper benchmark measured 775 ms against a 700 ms threshold during an early probe;
slow/native-model benchmarks were excluded afterward. Source changes are ready for
the normal rebuild/restart workflow; no production latency or FPS claim is made.

