# Voice latency implementation — 6 October 2026

The first implementation removes a measured wait-for-all search bottleneck,
caps independent inbox metadata reads, and extends the existing passive timing
observer with bounded p50/p95 summaries. No restart or live call was performed.

## Audit before changes

Installed `.venv`: `livekit-agents` 1.8.2, `livekit-plugins-google` 1.8.2,
`google-genai` 2.18.1. Code defaults to the realtime pipeline and the voice
chain `gemini-3.8-live`, `gemini-3.1-flash-live-preview`, then
`gemini-2.5-flash-native-audio-latest`, with Enceladus and 500 ms server silence.
Relevant overrides were absent in the inspected `.env.local` and `keys.env`.
This identifies installed code/configuration, not the model selected by a
currently connected call.

Already present: strict simple-command fastpath with compound-command fallback;
goal routes; installed optional Needle routing; specialist handoffs; tool timing;
passive session state/SDK metrics; action ownership/idempotency; deterministic
morning briefing and draft/revision/approval tools; cached OAuth tokens;
15-minute news and 30-minute weather caches; parallel weather/news and inbox
metadata fetches. No new local model, provider, credential, semantic router,
workflow tool, or cache was introduced. The existing read bundles and guarded
email workflows were retained rather than duplicated.

The installed SDK creates concurrent asyncio tasks for tools in a batch
(`livekit/agents/voice/generation.py`). That is separate from nonblocking Live
speech. The installed Google plugin exposes `tool_behavior`, default BLOCK,
and only sends response scheduling on NON_BLOCKING tools. Jarvis specifies
WHEN_IDLE but does not opt into NON_BLOCKING. No global behavior change was made:
Google's docs explicitly say 3.1 Flash Live does not support asynchronous
function calling. Multiple tool calls alone do not establish compatibility.

References checked: [Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling/),
[Live tools](https://ai.google.dev/gemini-api/docs/live-api/tools), and
[LiveKit events](https://docs.livekit.io/reference/agents/events/).
LiveKit docs MCP was unavailable; public official docs and installed source
were checked directly. No documentation CLI/MCP search was used.

## Changes and measurement boundaries

- `src/tools.py`: the three existing HTTP providers race for a valid result
  with a spoken summary and URL. Preference remains You, Brave, DDG among
  results ready together. An unfinished preferred provider no longer delays
  a valid fallback. Pending requests are cancelled and drained; all misses
  retain the existing browser fallback. A faster fallback can now win over
  a slower higher-priority source; this is an intentional selection change.
- `src/system/inbox.py`: at most three metadata GETs are active across inbox
  requests sharing an event loop, with three workers per batch and at most
  ten rows. Source order and failure numbering are retained. A cancelled
  urllib read keeps its slot until its thread finishes under the existing
  15-second HTTP timeout. Only independent reads use this limiter.
- `src/latency.py`: installing the observer twice is now a no-op. It keeps
  at most 256 numeric samples per series and computes nearest-rank p50/p95
  on demand/session close. There is no polling, extra inference, or storage
  of transcripts/tool bodies. Existing per-event timing logs are retained.
- Inbox reads explicitly mark a validated workflow only after every returned
  metadata ID matches its requested ID and headers are present, or a valid
  list response is empty. Partial reads/failures do not count. A token captured
  before the workflow rejects results from a superseded utterance. Only the
  first validated read workflow per detected utterance is sampled.

`vad_end_to_agent_speaking_s` measures a detected speech-end/state boundary to
SDK speaking, not acoustic speech end or the listener's speaker.
`vad_end_to_verified_workflow_s` currently measures a validated inbox read
result, not arbitrary action success, a whole multi-intent request, or its
spoken completion. Acknowledgements and ordinary successful returns never
count as verified completion. Negative/overlap/missing boundaries are excluded
from percentile series rather than replaced by zero. Empty series stay absent
in session summaries and appear as `n=0`/null in the offline report.

The existing explicit result-owned, no-tool main reentry still owns specialist
completion speech. Handoff recovery, bridge token/401 rediscovery/120-second
recovery, position/tray, microphone, school quiet rules, send approval,
recipient resolution, draft idempotency, and contact/permission/price handling
were not edited. No shared UI operation or dependent write was parallelized.
Existing cache policies and write invalidation paths were not changed.

## Evidence

The benchmark uses fake providers, fake Gmail metadata, and sleep-based I/O
fixtures. It never calls a model/account, opens a browser, captures audio, or
changes the computer. Results are empirical nearest-rank percentiles, not a
claim about overall Jarvis voice speed.

| Fixture | Before p50 / p95 | After p50 / p95 | Samples |
| --- | --- | --- | --- |
| Valid search 5 ms; unused providers 80 ms | 81.06 / 82.02 ms | 5.93 / 8.12 ms | 30 each |
| Ten metadata GETs, 5 ms each | 8.29 / 14.19 ms | 29.17 / 37.27 ms | 10 each |

Peak active metadata reads fell from 10 to 3. That resource cap intentionally
adds batch wait in this fixture; it is not a measured inbox speedup. Observer
processing for four events/turn measured 4.72 µs before and 3.91 µs after in a
10,000-turn microbenchmark. Timing noise means this is evidence of small
overhead, not an observer performance claim.

Source hashes and fixtures: `benchmarks/voice-paths-baseline.json` and
`benchmarks/voice-paths-after.json`. Validation summary:
`benchmarks/voice-paths-tests.json`: baseline 175 passed; staged 49 passed;
applied source 308 passed. Five newly written search/rollup tests failed on
the original source and passed after implementation. Added tests cover slow
provider cancellation, caller cancellation, invalid results, the metadata
concurrency cap including cancellation/replacement, partial/stale verification,
bounded samples, idempotent observer installation, and log-summary privacy.

The regression selection also covers simple fastpath/multi-intent fallback,
goal routing, result-owned handoff audio reentry, bridge-auth startup,
realtime startup, mic framing, guest mode, and email draft/approval behavior.
New telemetry/tests/scripts pass Ruff. Three pre-existing findings remain
in touched files: an unused `contextlib`, repeated `googledrive` key, and
existing launcher import order. Changed source compiles and diff checks pass.

Tests initially stalled because even `asyncio.to_thread(lambda: 1)` hangs
under the delegated sandbox; the identical approved outside-sandbox check
returned immediately. Offline tests passed outside that sandbox. This was
an execution-environment limitation, not a baseline audio-test failure.

Historical logs, summarized before changes, contained 32 non-overlap state
intervals (p50 0.959 s / p95 2.925 s), 411 overlap observations, and no
validated-workflow samples. Provider-generation samples have a different
origin and cannot substitute for voice end-to-end timing. These mixed-session
historical observations are not a controlled real-call baseline or an after
comparison; see `benchmarks/voice-observations-before.json`.

## Activation, next measurements, and rollback

Source changes are saved. The running worker needs a coordinated reload/restart
to use them; none was performed. No frontend build is required. Obtain parent
clearance before restarting the worker or creating a test call.

Offline commands from the repo:

```bash
PYTHONPATH=src uv run --no-sync --offline python scripts/bench_voice_paths.py --label after --output /tmp/voice-paths.json
uv run --no-sync --offline python scripts/summarize_voice_latency.py ~/.jarvis/actions.log
```

After coordinated activation, compare the same cold/warm utterances and read
workflows, keeping first response, verified result, and result-owned spoken
completion separate. Real end-to-first-audible p50/p95 remains unmeasured here.
The existing `scripts/voice_latency.py --live` creates real calls and measures
sender playout end to received voiced audio, including trailing silence/frame
padding; it still does not measure the physical listener speaker or correctness.
Do not run it without coordination. General action verification, latency across
all macro stages, and physical audio playout need additional explicit evidence.
No semantic-router hop or new cache is justified by this fixture.

Rollback must preserve the repo's pre-existing uncommitted edits. Exact prior
sources are stored at `/home/ripjk/Documents/Codex/2026-10-06/task-2/original/`.
The task's `rollback_latency.py` first checks current file hashes against this
implementation; it refuses if later changes exist. It prints a dry-run plan
by default and restores only this task's files with `--apply`. Review it and
obtain runtime clearance before any rollback/restart. Do not use a broad
`git checkout`, reset, or stash on this dirty checkout.
