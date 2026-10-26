# System-health recorder MVP

The recorder is volatile, numeric-only, and disabled unless `JARVIS_HEALTH_RECORDER` is explicitly `1`. Import/construction does not sample, open a socket, load a model, or start a worker. This candidate has not been activated on the live system. Coordinated activation/reload requires separate runtime clearance.

After opt-in, the bridge owns a single five-second sampler. Its two rings retain at most 360 system samples and 512 events, with a 30-minute age bound. CPU utilization excludes duplicated guest counters and reports iowait separately. Memory fields are total/available and swap total/free bytes. PSI values are interval percentages of `some` total stall time for CPU/memory/I/O. First/reset/long-gap observations are unknown. The five kernel reads are capped at 64 KiB and 256 lines each. No process enumeration, command lines, logs, window titles, screenshots, audio or transcription content enters the recorder.

Agent callbacks offer fixed metric/error enums to a 128-record nonblocking queue. One sender transmits at most 32 events every five seconds, with 500 ms socket timeouts and capped reconnect backoff. Dropped data is never retried. Disabled recording creates no sender. Error messages, tool names/arguments/results, IDs and provider bodies are excluded; timeout/cancellation/quota classification uses types or numeric status only. Private-phone, guest and revoked contexts fail closed before offers and before/after the report await. Existing general tool wrapper guards remain in place.

The incident tool and local CLI request a bounded deterministic report. Report text describes sustained resource pressure and observed error categories without claiming process/decoder causality. It includes sample counts, ages, gaps, event loss and exact metric scopes. An existing validated-workflow latency metric is a timing observation, not a new action verification predicate. No feature-2 `outcome` state is added or overwritten. No automatic remediation, notification, restart or driver change occurs.

## Interfaces

`install_session_timing(session, log, *, sink=None)` keeps its old return value, logging and idempotence. Optional `sink(metric, seconds, scope)` receives only bounded numeric observations; sink failure is swallowed. The agent owns session recorder installation and shutdown. Other features may offer fixed categories through the existing session `_jarvis_health[1]` sink; they must not pass action payloads or imply verified outcomes.

`HealthRecorder(enabled=False, clock=..., reader=...)` exposes `sample`, `record_batch`, `report(window_s=300)`, `clear`, explicit `start`, and `close`. Its normal bridge instance is `health_recorder_bridge.RECORDER`. The bridge service alone owns this canonical state; an agent imports client types, never the ring instance.

`HealthEventSender.offer(metric, value, scope, error=None)` performs no network/file I/O. `flush` is off the voice thread, `start` is explicit, and `close` discards pending events without a flush. A request already in flight can finish under its existing timeout after close; there is no retry backlog. `BridgeClient` uses only existing environment host/port/token configuration, disables redirects/proxies, and bounds response/request bodies at 16 KiB. It does not read token files or obtain credentials.

The original bridge handler authorization runs first. Each new route additionally requires a configured bearer token even on loopback and checks owner/guest state. One route operation at a time is admitted; the rest return 429. Responses use `Cache-Control: no-store`.

- `GET /health-recorder/incident?window_s=300`: integer window 30–1800; returns `ok`, `say`, `text`, `recording_enabled`, `evidence`, `metrics`, `coverage`, `limitations`. No raw ring.
- `POST /health-recorder/events`: exact `{version:1, events:[...]}`, ≤32 rows, ≤16 KiB, at most one accepted batch per five seconds globally. Rows have exactly `at`, `metric`, `value`, `scope`, `error`; the timestamp is host monotonic and must lie within retained history (at most one second future tolerance). No free text. Unknown fields and mismatched metric scopes are rejected atomically.
- `POST /health-recorder/clear`: exact empty object. Clears volatile rings, parsing baselines and ingest rate state.

Shared integration owns minimal hunks in `agent.py`, `latency.py`, `bridge.py`; no frontend, orb, Assigner, WhatsApp, visible URL behavior, audio pipeline or approval schema is changed. `_never_demote` gains only the new report tool.

## Compatibility limitations and draft reductions

Loopback and an environment-provided existing bearer token are required for voice/client transport in this MVP. Token-file-only and tailnet-bound deployments report unavailable; do not silently expose telemetry or change permissions to make them work. Existing token resolution differs across repository helpers, so canonical resolution remains an integration decision. Bridge system-only recording still works when event transport is unavailable.

PSI `full`/avg10, process/decoder attribution, hardware microphone/speaker counters, producer epoch/sequence deduplication and persistent incident export remain deferred. The MVP uses one global ingest quota rather than per-producer quotas; concurrent agent sessions can lose events. Sender losses are counted on that sender; report `dropped_events` counts canonical-ring overwrites only, not all transport losses. Coverage/age fields expose missing data but do not establish complete history. A report cannot establish precise temporal causality; the five-second sampler can miss brief spikes.

The current SDK event API was checked against [LiveKit events and error handling](https://docs.livekit.io/reference/agents/events/) on 2026-10-08. The code reuses existing event subscriptions and makes no provider/configuration changes. Documentation marks `metrics_collected` deprecated; the existing repository observer still uses it. State-derived timing remains the stable MVP path. No new model/API call is used for explanation. The standard voice provider may receive the concise tool result through ordinary reply handling; the raw ring does not.

## Validation and rollback

New tests use fake clocks, kernel readers, handlers, clients and sessions. Exact shared integration nodes are extracted with AST rather than importing/starting the live agent. Full agent-import integration and live CPU/RSS/audio acceptance are pending coordinated integration. Tests must not create production ports, call models or invoke real guards/desktop state. The staged focused result and synthetic memory/enqueue evidence are in the feature-8 delivery package.

Disable the feature through the coordinated configuration path and an approved reload if needed. This code provides no process/reload command. Volatile history clears on close and is lost across restart. For source rollback, reverse only reviewed feature-8 hunks and remove its new files. The delivery's rollback helper defaults to a dry run and refuses if any owned file differs from the candidate hash, preserving peer/uncommitted changes. Never use a broad reset/checkout on this shared repository.
