# Additional Python runtime optimization — 2 October 2026

Baseline: `29112f4029c84e0d9eb7b283883964672da1cb1b` on `jarvis-voice-fixes`.
The changes below are frozen for coordinator integration. No commit, push,
frontend/native build, export, live service restart, desktop interaction,
microphone, speaker, paid model call, or whole Python suite was performed.

## Implemented changes and evidence

| Path / behavior | Before | After | Evidence limits |
| --- | --- | --- | --- |
| `src/proactive/watcher.py`: synchronous power polling immediately after session start and every 15 seconds | A slow `upower` could block the voice event loop for its entire 3-second subprocess timeout | One retained, shielded worker keeps collection off the loop; cancelled/restarted ticks cannot overlap collection or publish its abandoned result | Ten injected 120 ms sensor reads; median per-trial maximum 2 ms heartbeat lag **119.315 → 0.676 ms**; sensor wall time remains approximately 120 ms |
| `src/local_voice.py`: interrupted Piper work | Async lock released while native work continued; new utterances could overlap on one voice and use more shared workers; simultaneous preloads could load two models | One private worker serializes load/render/release, skips cancelled queue entries, and stops old iteration at the next available chunk; close drains before releasing the model | Fake native model: peak simultaneous inference **6 → 1**, inference calls **6 → 2**, obsolete queued calls **4 → 0**, duplicate loads **2 → 1** |
| `src/agent.py`: per-job Piper disposal | Installed LiveKit session teardown closes streams but does not dispose the caller-owned TTS plugin | Job shutdown explicitly closes its owned Piper plugin, including startup-failure paths | Isolated job-startup failure test verifies model release and actual dedicated-thread exit; no room connection |
| `src/system/files_tools.py`, `src/second_brain.py`: indexed lookup | Read/parse/fuzzy score ran on the voice event loop; each positive candidate was scored twice | Read-only file/folder search runs in a worker; each candidate is scored once | Private generated 20,000-node graph, five rounds per query; exact output hashes unchanged; additional 225 seeded ranking comparisons against the baseline passed |
| `src/system/pentest.py`: subprocess ownership after cancellation/error | Cancellation could discard tracking while the child remained alive; timeout interrupted pipe draining; communication failure also abandoned the child | Kill and drain/reap before dropping identity, including repeated cancellation; after direct-child exit, allow 50 ms more draining then close our pipe handles so descendants cannot retain cleanup | Nine fake-child cases, three harmless private fork cases and existing module: **26 passed**; no scanner, network or desktop action |

File-search measurements (milliseconds; each p95 is the largest of five samples):

| Query | Median wall before → after | Median maximum loop lag before → after | p95 maximum loop lag before → after |
| --- | ---: | ---: | ---: |
| `engineering notes` | 504.594 → 269.536 | 502.679 → 15.877 | 577.495 → 59.796 |
| `engineering-notes-00001.md` | 640.067 → 331.727 | 638.124 → 10.414 | 692.938 → 60.638 |
| `md` | 37.382 → 30.403 | 35.431 → 11.849 | 58.829 → 17.886 |

The graph is intentionally large and includes many similar labels. This exposes
the production fuzzy-scoring path; it is not a sample of the user's files.
The fixture and result hashes in the two reports are identical. Pure Python
scoring still contends for the GIL, and JSON parsing can retain it briefly;
offloading does not promise zero loop jitter. Runs were serial within this chat
on a shared machine, without controlling other applications' CPU load.

No model selection, synthesis settings, sample rate conversion, VAD thresholds,
endpoint timing, visual rendering, school-mode policy, or tool catalogue changed.
Both Piper stream paths preserve fake-model PCM bytes, sample rate, speaker ID,
sentence order and length scale. This establishes unchanged plumbing/settings,
not a new perceptual audio-quality evaluation.

## Startup/join and polling inspection

Five fresh-interpreter `import agent` runs with `-X importtime` had median process
wall time **1,765.727 ms** (including interpreter startup/exit and profiler
overhead; filesystem caches warm). In the first run, `agent` cumulative import
time was 1,322.805 ms, `livekit.agents` 745.935 ms, and `google.genai` 347.978 ms.
Cumulative times overlap and must not be summed. The probe used a private
`JARVIS_HOME`, disabled dotenv/Needle/local actions, and removed credentials,
display variables and the live D-Bus address. Raw top-module samples and the
reproduction command are in `startup-import-2026-10-02.json`.

The existing warm standby processes already avoid paying all imports per wake.
No import deferral or model reduction was justified from this profile alone.
Read-only join inspection found serial `list_rooms` / `list_participants` checks
before authentication/connect. A four-room fake API at 50 ms per request took
250.98 ms, with peak request concurrency one. Also, Silero initialization and
Assistant construction complete before `ctx.connect()`; the existing comment
about constructor/connect overlap is inaccurate. These are further candidates,
not measured live savings, and remain unchanged. Broadening room concurrency or
reordering initialization needs separate failure/cancellation coverage.

Recurring activity polling was inspected but left unchanged: there is no
measurement here that warrants changing its polling cadence or visible freshness.
The confirmed periodic voice-loop stall is the power watcher above. The separate
`freeze_testing()` tracking-clear race and subprocess descendant/process-group
handling remain outside this patch; only `_run_tracked`'s owned direct child is
covered by the cleanup guarantee.

## Verification and reproduction

The initial combined focused run passed **131 tests**, with **7 deselected** and
7 existing dependency/fixture warnings, in 2.61 seconds. The deselections are
three live-model cases and four real-weight Piper cases. New tests use private
files, mocked API/sensors/models/children and no browser. Regression cases were
demonstrated failing before their corresponding fixes. A final test-only exception
name lint correction was then rerun successfully (one test).

```sh
uv run pytest -q \
  tests/test_agent.py tests/test_voice_job_cleanup.py \
  tests/test_local_voice.py tests/test_local_voice_lifecycle.py \
  tests/test_proactive.py tests/test_proactive_watcher_responsiveness.py \
  tests/test_files_tools.py tests/test_second_brain.py tests/test_file_search_responsiveness.py \
  tests/test_pentest.py tests/test_pentest_cancellation.py \
  -m 'not live_llm' \
  -k 'not faster_scale_shortens_audio and not real_voice_renders_audio and not stream_adapter_path_renders_audio and not interruption_closes_stream_promptly'
```

Run this with `PYTHON_DOTENV_DISABLED=1`, display/credential variables removed,
and `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`, as in this pass. The JUnit
record is `/tmp/jarvis-runtime-optimization-focused-2026-10-02.xml`.

Thirteen changed/new Python files pass Ruff lint and formatting. `src/agent.py`
retains exactly its two baseline `I001` import-order diagnostics (lines 1 and 49),
verified against `git show HEAD:jarvis_new/src/agent.py`; no unrelated import
reformat was applied. Scoped `git diff --check` passes. Independent subagent
reviews found no actionable issue in file-search parity/responsiveness,
Piper shutdown integration or watcher stop/restart cancellation.

Reproducible probes:

```sh
uv run python scripts/file_search_benchmark.py --label after --rounds 5 --output /tmp/file-search.json
uv run python scripts/proactive_watcher_benchmark.py --label after --rounds 10 --stall-ms 120 --output /tmp/watcher.json
uv run python scripts/piper_lifecycle_benchmark.py --baseline-ref 29112f4 --output /tmp/piper.json
```

The first two scripts run the current checkout; reproduce a baseline in a separate
checkout, without overwriting shared files. The Piper probe explicitly loads its
baseline source from Git into an isolated module. Benchmark outputs are diagnostic measurements, not
fragile timing assertions in the test suite.

Current official [LiveKit testing documentation](https://docs.livekit.io/testing/overview/)
and [speech/audio documentation](https://docs.livekit.io/agents/multimodality/audio/)
were consulted; plugin ownership was checked against the installed SDK source.
No network inference, native FPS, live join, physical audio latency or perceptual
quality result is claimed. Coordinator integration/full-suite QA and activation
remain outstanding.

### Independent-review correction: inherited pipe EOF

The coordinator's independent final review found an introduced P2 regression
after the initial freeze: `_kill_and_reap` waited for captured-pipe EOF even
after its direct child exited, so a descendant retaining stdout/stderr could
extend a timeout indefinitely. This was not detected by the initial fake-child
coverage. Three new private Python/fork cases failed before the correction:
timeout after parent exit, cancellation after parent exit, and repeated
cancellation while the parent was still alive.

Cleanup now retains its shielded owner/reaper, observes direct-child termination
independently of EOF, allows a final 50 ms draining grace, closes the owned
subprocess transport, consumes the communication task, and awaits the original
reaper before releasing tracking. The transport close is deliberately narrow:
asyncio's high-level `Process` has no public method for closing captured read
pipes. It does not signal descendants. An early `Process.wait()` can itself
wait for pipe disconnection, so merely timing out `communicate()` or adding
another early `wait()` would not fix this case.

The reviewer's exact reproduction, copied unchanged to
`/tmp/jarvis-pentest-pipe-fix-pm32x3h0/repro.py`, now reports both baseline and
fixed owners finished at its 250 ms checkpoint. A separate completion-timed
run using the same harmless 1.5-second descendant and 100 ms timeout measured
the reviewed patch at **1,510.781 ms** and the corrected patch at **152.873 ms**.
Both return 124 with no tracked direct child remaining. This is one controlled
sample, not a general latency percentile. Source hashes and measurements are
saved in `pentest-pipe-cleanup-2026-10-02.json`.

After formatting, `uv run --offline --no-sync pytest -q
tests/test_pentest_cancellation.py tests/test_pentest.py` passed **26 tests in
1.39 seconds**. Ruff lint/format and scoped diff checks pass. A second read-only
review found no actionable issue in direct-child/EOF separation or repeated
cancellation. No other production paths changed during this correction; the
manifest was refreshed for these exact source/test/report/evidence changes.
The initial 131-test combined result is retained as historical evidence and
was not represented as a fresh combined run after this fix.

## Exact changed paths owned by this run

Production:

- `src/agent.py`
- `src/local_voice.py`
- `src/proactive/watcher.py`
- `src/second_brain.py`
- `src/system/files_tools.py`
- `src/system/pentest.py`

New tests:

- `tests/test_voice_job_cleanup.py`
- `tests/test_local_voice_lifecycle.py`
- `tests/test_proactive_watcher_responsiveness.py`
- `tests/test_file_search_responsiveness.py`
- `tests/test_pentest_cancellation.py`

New probes and evidence:

- `scripts/file_search_benchmark.py`
- `scripts/piper_lifecycle_benchmark.py`
- `scripts/proactive_watcher_benchmark.py`
- `benchmarks/file-search-before-2026-10-02.json`
- `benchmarks/file-search-after-2026-10-02.json`
- `benchmarks/proactive-watcher-before-2026-10-02.json`
- `benchmarks/proactive-watcher-after-2026-10-02.json`
- `benchmarks/piper-lifecycle-2026-10-02.json`
- `benchmarks/pentest-pipe-cleanup-2026-10-02.json`
- `benchmarks/startup-import-2026-10-02.json`
- `benchmarks/runtime-optimization-source-sha256-2026-10-02.json`
- `benchmarks/runtime-optimization-2026-10-02.md`

Other checkout modifications belong to the other coordinated writers. This run
did not edit bridge, frontend, dashboard/markets or native files.
