# JARVIS MASTER SYSTEM REPORT
## End-to-end architecture, development history, defects, test evidence and operational readiness

**Audit:** 30 October 2026, Asia/Singapore  
**Source checkout:** `/mnt/data/jarvis-voice-butler/jarvis_new`  
**Git HEAD observed:** `45f5b61` (27 October 2026). Extensive modified and untracked work exists.  
**Methods:** Read-only code/repository exploration, archived reports and structured benchmark records, extracted tool endpoint inventory, and one new isolated offline regression run (147 passed, one warning).  
**Scope:** This is a cross-reference audit, not certification of all 233 distinct tool names or a live microphone-to-speaker field test. Sensitive environment files and private account contents were not copied.

## 1. Executive assessment

**VERIFIED:** The repository implements a multi-transport personal assistant spanning desktop/phone speech, LiveKit, Gemini Live, alternative Whisper/Piper and LiveKit inference pipelines, specialist function routing, tool governance, background jobs, data retrieval, mobile/WhatsApp/email/calendar systems, and a native desktop HUD. Its 28 October documented map is `artifacts/jarvis-pipeline-2026-10-28/GUIDE.md`, with a source-derived `tool-inventory.json` declaring **236 entries in 37 modules, 233 distinct names**. Source registrations are not proof that endpoints have been successfully used live.

**VERIFIED:** Recent targeted testing completed **147 passed / zero failed / one third-party warning** on 30 October. The prior comprehensive 29 October run recorded **6,916 passed, 69 failed, 7 skipped, 15 deselected** over 853.07 seconds, followed by a fresh final-source rerun of the 69 failed node IDs with **4 passed / 65 failed**. The 65 survivors represent 58 obsolete Friday/Ultron/character-switch expectations, six chess unmuted-commentary expectations and one deleted linked-turn method expectation. No all-green complete suite is claimed.

**VERIFIED:** The 29 October reliability hardening suite passed 89 targeted tests. The new backend pool has deterministic routing and controlled failover but no verified live failover in that report. The new Power Foundation adds scaffolded SQLite events, opt-in read-only HUD projections, guarded recovery and local MCP metadata handshake; its standalone permission model does not turn arbitrary MCP descriptors into executable tools.

**VERIFIED:** Voice performance and interruption evidence includes controlled synthetic benchmarks, but field latency remains **UNKNOWN**. On 30 October, an offline fast-barge simulation detected all loud/moderate/quiet simulated utterances and recovered onset PCM byte-exactly, but missed all very-quiet cases and fired for all loud background-voice cases. In the separate real Gemini Live overlapping-audio experiment, the first received 250-ms RTC silence gap followed the injected onset by 684 ms, while voiced frames continued afterward. That is neither verified hardware speaker mute nor provider cancellation.

**INFERRED:** The principal remaining risk is not lack of features but coordination across identity, consent, evidence, outstanding operations, voice interruptions and live deployment. Different task, chat, desktop and automation subsystems still maintain distinct lifecycle contracts.

## 2. Evidence classification and coverage methodology

- **VERIFIED:** directly observed repository facts, recorded test artifacts with explicit method, or fresh executed test results. Archived reports count as verified *reported test evidence*, not necessarily present-runtime behavior.
- **INFERRED:** behavior reasonably suggested by code, architecture or a design document but not demonstrated end-to-end in this audit.
- **UNKNOWN:** disabled, untested, undocumented, dependent on live accounts/hardware, or conflicting historical documentation.
- **Test-name match:** a test source mentions a declared function. Such a match is **NOT** equivalent to direct invocation, assertion coverage, safety validation, or successful production use.
- **Historical counts overlap** between focused runs; never sum pass counts as if they were unique test cases.
- **Activation boundary:** code present in the current working tree is not automatically the code inside an already-running process. For changes explicitly documented as awaiting restart, treat live application as unknown until supervised health and version verification.

## 3. Complete conceptual system pipeline

```text
DESKTOP MIC / PTT ──> Wake client ──> auth/token ──┐
PHONE / JarvisLink ──> phone gate ────────────────┤
HUD / bridge typed controls ──> auth + policy ───┤
Telegram / Mail Link / Whatsie ──> ingress gates ┤
Gmail / timers / calendar / sensors / files ─────┤
                                                  v
                                      BRIDGE / LIVEKIT / AGENT
                                           session identity
                                     echo / guest / consent / scope
                                                  |
                          +-----------------------+-----------------------+
                          |                       |                       |
                          v                       v                       v
                   REALTIME VOICE            DIRECT TEXT            CLOUD INFERENCE
                   Gemini Live               Silero/Whisper         AssemblyAI STT
                   native audio              Gemini text           Gemini text
                   native function call      Piper TTS             Piper/override TTS
                          |                       |                       |
                          +-----------------------+-----------------------+
                                                  |
                            trusted control / grammar / intent / goals
                             direct tools OR typed advisory decider
                                                  |
                  +-------------------------------+-------------------+
                  |                 |                  |              |
             direct tools      ask_backend         research        coding
                  |           ChatGPT/Claude       project        proposals
                  |                |                  |              |
               per-tool gate / exact recipient or target / fresh approval
                  |          action owner / cancellation / result checks
                  +-------------------------------+-------------------+
                                                  |
                     speech + captions + HUD + archive + persistence
                   notification / email / WA / docs / calendar / desktop
                       evidence-bearing status or deliberate no-op
```

**VERIFIED SOURCE ANCHORS:** `src/wake_client.py`, `src/mic_uplink.py`, `src/phone_session.py`, `src/bridge.py`, `src/agent.py`, `src/intent/fast_path.py`, `src/intent/needle_router.py`, `src/request_router.py`, `src/backend_pool.py`, `src/system/*`, `src/agent_tasks.py`, `src/hud_events.py`. Detailed source map: `artifacts/jarvis-pipeline-2026-10-28/GUIDE.md`.

### 3.1 Wake, transport and connection setup
**VERIFIED DESIGN:** Desktop wake phrase/PTT uses the wake client, denoising, room/token creation, microphone uplink and LiveKit. Authenticated phone calls use separate phone/private-call policy; typed bridge commands bypass STT. Local actions, observer signals and message polling do not necessarily enter the live model. Echo suppression and generation-scoped controls prevent untrusted content from directly granting authority. Runtime wake latency, actual room connection success in the present process and handset ringing are **UNKNOWN** without fresh connected-device testing.

### 3.2 Realtime model and speech
**VERIFIED SOURCE:** `JARVIS_PIPELINE` defaults to `realtime`, which uses Gemini Live native audio, native turn detection, function calling and speech generation. The documented voice fallback model chain and configurable voice-model updater live in `src/agent.py` and `docs/voice-model-upgrades.md`. Specific selected model for an existing open call is **UNKNOWN** without inspecting that active session. LiveKit handles transport; captions and HUD events form parallel output, not evidence of speaker playback.

### 3.3 Direct local STT + text model + Piper
**VERIFIED DESIGN:** `JARVIS_PIPELINE=direct` routes through local Silero/Whisper and Google Gemini text inference; Piper ONNX TTS renders audio locally and the playback gate handles speaker output. It is *offline-first for speech processing*, not a fully offline LLM. The old `local` pipeline uses LiveKit Cloud inference AssemblyAI STT and Gemini text plus local or overridden voice. Avoid conflating the three voice branches.

### 3.4 Turn handling and tool-selection sequence
**VERIFIED DESIGN:** Fresh trusted owner turn -> activity/echo/consent filtering -> safety and special controls -> strict local grammar on text branches -> optional Needle routing and goal resolution -> main Gemini handling or specialist transfer. `route_request` is advisory, while `ask_backend` enforces its own route and provider preference. Request routes: direct / backend / research / code. The decision label does not itself execute a tool. The larger Perplexity decider is independently used for mail event/urgency gates and WhatsApp persona selection.

### 3.5 Backend workers and handoff
**VERIFIED SOURCE/TEST REPORT:** `src/chatgpt_cli.py`, `src/backend_model.py`, `src/claude_backend.py`, `src/backend_pool.py`, `src/request_router.py`, `src/system/workspace_tools.py`. Default auto backend prefers ChatGPT; explicit provider requests stay authoritative. Cooldown and retry use remaining global deadlines, and ambiguous completion failures do not blindly replay. In the 29 October reliability report, 89 targeted cases passed and 32 new regression cases covered malformed replies, provider mismatch, quota keywords and deadline handling. Authentication, live quota, live failover and remote completion are **UNKNOWN**.

### 3.6 Consequential action execution and result delivery
**VERIFIED DESIGN:** Tool calls traverse module-specific guards (identity, exact target, action scope, single-use confirmation when required, cancellation and action ownership); file/screen/message content remains untrusted evidence. Statuses must distinguish prepared, attempted, verified, uncertain and stopped. Model prose cannot authorize an action. Specialist proposals, drafts and notification invitations are not equivalent to external completion. Relevant sources include `src/action_guard.py`, `src/action_outcome.py`, `src/system/action_verify.py`, `src/cancellation_control.py`, `src/wa_approvals.py` and `src/agent_tasks.py`.

## 4. Tool and integration domains

The source-derived endpoint appendix at the end lists **every declared occurrence** and source module. Core domains are:
1. Local desktop and power: window position/focus/layout, app launch, input, screenshot, file ops, CPU/media/display/network controls; monitored or approval-gated side effects.
2. Browser/web and shopping: search providers, visible browser browsing, fresh page inspection, single-use click/type/scroll, read-only backend research and product suggestions.
3. Gmail/calendar/Docs/Sheets/Notion/Drive: inbox reads, drafts/confirmation, structured event extraction, scheduling import, source grants, document generation and cloud updates.
4. WhatsApp: WhatSie integration, chat reads, approved outgoing sends, images, persona router, automatic replies and owner-style mimic; persona selection does not waive recipient approval.
5. Phone, Telegram, Mail Link: authenticated sessions, private phone scope, incoming call listener, secure sender alignment and replay protection for command mail.
6. Knowledge/memory: memory notes, prior activity, second-brain indexing, local/PDF/Drive librarian with reviewed source and content-disclosure grants.
7. Projects/coding: ChatGPT-based bounded research, durable archive, dedicated Assigner-generated proposals, validated source snapshots and code-review boundaries.
8. Tasks/workshops/schedules/proactivity: persistent one-shot scheduling, task leases, approved task steps, cancellation, event filters and delivery policies.
9. Vision/watch/media/games: foreground OCR, consent-gated screen observation, mobile photo inputs, download workflows, voice chess and media controls.
10. OSINT, pentest, smart devices, HUD and simulation: scoped security diagnostics, Home Assistant/KDE Connect hooks, system health and paper-trading simulations.

**INFERRED:** A function being declared in one source does not prove it is registered in every deployment, reachable from every caller, or safe for a private/guest session. The appendix therefore marks test references separately, not as per-tool acceptance certificates.

## 5. Development and build history reconstructed from records

| Date | Evidence / milestone | Reported verification and caution |
|---|---|---|
| 1–2 Oct | Wake/voice runtime profiling, HUD/entry animation, insights, paper ledger and native shell hardening | `docs/performance.md`: 3,073 Python passed, 13 skipped; 186 frontend and 79 native Rust passed. Separate QA snapshots have different counts. |
| 2 Oct | Independent final QA and integration | `benchmarks/final-qa-2026-10-02.md`: 2,609 passed / 11 skipped / 2 stale fixture failures, later corrected focused tests; no monolithic fresh green rerun. `benchmarks/integration-qa-2026-10-02.md`: 2,893 Python + 170 frontend passed at later integration snapshot. |
| 2 Oct | Optimized watcher, local Piper lifecycle, file search, subprocess cleanup | `benchmarks/runtime-optimization-2026-10-02.md` and JSON fixtures; synthetic, not physical audio performance. |
| 6 Oct | Search-race, inbox metadata cap, bounded latency observer | `docs/voice-latency.md`: baseline 175, staged 49, applied 308 tests passed. No live room microphone test. |
| 14 Oct | Reliability audit, Google Calendar reconnect, scheduler, Android call listener/fallback | `docs/audit-2026-10-14.md`: 4,533 Python passed / 17 skipped; 225 frontend, 32 Flutter, 89 Rust shell + 8 window capture, Android 314 then 318 cases, no real call success. |
| 27 Oct | Character, frontend/mobile skin, cinematics | Recent Git commits; working-tree modifications and deleted character YAMLs may invalidate historical character tests. |
| 28 Oct | End-to-end pipeline guide, decision model/WhatsApp comparison | `artifacts/jarvis-pipeline-2026-10-28` + `benchmarks/decisions-2026-10-28/main/REPORT.md`. 461 decision API top-level measurements, synthetic prompts. |
| 29 Oct | Backend reliability; voice queues; captions; memory recall; frontend polling; task Power Foundation passes 1–4 | `docs/JARVIS_RELIABILITY_UPGRADE.md`, `docs/OPTIMIZATION_PLAN_2026-10-29.md`, `docs/POWER_FOUNDATION_PASS*.md`, `work/optimization-2026-10-29`. Broad suite remains red. |
| 29 Oct | Bridge memory graph repair and safety watchdog | `docs/bridge-memory-guard.md`: 38 focused tests, repeated isolated index rebuild, post-restart memory observation. |
| 30 Oct | Fast barge-in offline simulation and Gemini RTC overlap | `benchmarks/barge-in-offline-2026-10-30.json`; `benchmarks/gemini-live-overlap-2026-10-30.json`. Not acoustic real-room hardware proof. |
| 30 Oct | This master audit | Fresh targeted 147 passed / 1 warning; no deployment, restart, secret read or external action. |

**Build process:** Source code uses uv/Python for voice/backend/tests, Next/TypeScript for frontend, Tauri/Rust for native desktop shell, and Android/Flutter clients for phone paths. Key startup modes in README: `uv run python src/agent.py console`, `dev`, `start`. Build/test artifacts are version- and environment-dependent. The 29 October optimization report warns that the frontend package build script can overwrite tracked `shell/ui` outputs and uses direct `pnpm exec next build` instead. Verify staged frontend build files before deployment. For safe regression use offline Python markers excluding `live_llm` and `slow`, isolated test homes, and account/mock transports. Real LiveKit model and phone checks require coordinated approval.

## 6. Engineering defect and fix register

| System | Original defect / symptom | Fix or containment | Evidence and remaining limitation |
|---|---|---|---|
| Bridge Second Brain | Duplicate `contains` graph edges, derived graph ~2.1 GB, bridge ~13 GB memory + swap | Deduplicate/index edges, cap growth, reject >32 MiB graph, PID-safe memory-guard supervisor | **VERIFIED** 38 tests; six deterministic 1,899-node/2,081-edge rebuilds; reported ~190 MiB bridge after recovery. A different memory leak is not ruled out. |
| Backend routing | Unknown explicit providers substituted, invalid timeouts, malformed responses and ambiguous Claude replay | Strong provider priority, deadline validation, precise failover and diagnostics | **VERIFIED** 89 cases / 32 new tests; **UNKNOWN** actual provider outage failover. |
| Decision API | Prior free Mercury route HTTP 404 | Comparison of non-free Mercury and Perplexity variants, bounded decision policy | **VERIFIED** synthetic benchmark, not future API availability. |
| Secondary microphone | Overflow silently lost opening words | Bounded capture buffer aborts incomplete turn | **VERIFIED** offline targeted coverage; real noisy environments unknown. |
| Local TTS | Cancelled native Piper work overlapped / leftover queued work | Single native owner, bounded queue and cancellation drain | **VERIFIED** fake inference peak parallel 6 -> 1, obsolete 4 -> 0; audio perceptual quality unknown. |
| Fast interruption | Provider-side stop can be slow and first phonemes lost | Independent local mute signal, pre-roll and continuous microphone forwarding | **VERIFIED** synthetic tests; very quiet speech missed and background voices trigger. Hardware stop latency unknown. |
| Search | Slow unused providers delayed valid fast search | Race valid responses, cancel remaining providers | **VERIFIED** 81.06 -> 5.93 ms search fixture median, not overall response time. |
| Inbox | Unbounded concurrent metadata reads | Shared concurrency cap of 3 | **VERIFIED** peak 10 -> 3; 5-ms-fixture median worsened from 8.29 to 29.17 ms intentionally. |
| Latency observer | Unbounded or duplicate installation concerns | Idempotent install, bounded 256 samples, p50/p95 | **VERIFIED** offline; measured VAD->SDK-speaking is not real physical speech. |
| Captions | Full-history reads, malformed UTF-8 and temp collisions | Reverse block read, bounded pruning, atomic unique writes, serialized writers | **VERIFIED** 20k-line median 20.6727 -> 0.0192 ms; isolated file read only. |
| Memory | Reparsed same paragraphs | Bounded validated paragraph cache | **VERIFIED** synthetic 1.5562 -> 1.0793 ms; accuracy unmeasured. |
| Frontend polls | Overlapping stale requests and hidden-view work | Visibility-aware serialized, abortable polling | **VERIFIED** 253 frontend tests / typecheck / export, not native FPS. |
| Calendar | Invalid/overnight duration, missing server ID, wrong import destination | Validated rollover, nonempty result ID, configured account and retryable failures | **VERIFIED** regression tests; OAuth read reconnected, actual event write untested in Oct 14 audit. |
| Phone calls | Listener failed to restore, live invitation `notification_failed` | Sticky service/boot restoration and fallback high-priority notification | **VERIFIED** Android tests; **UNKNOWN** physical handset ring and call audio. |
| Schedules | No general durable voice reminder/call tool | SQLite one-shot scheduling with idempotent dispatch and uncertain states | **VERIFIED** tests; due event delivery depends on machine/bridge. |
| Memory capture/delete | Failed writes advanced capture watermark or claimed removal | Commit outcomes only after successful write/archive | **VERIFIED** Oct 14 regression report; live persistence audit still needed. |
| Activity and proactivity | Failed output counted delivered | Consume budget only after success; retry while source exists | **VERIFIED** fixture tests; no durable event replay if source disappears. |
| Desktop and monitor | Failed monitor or window coordination | Dedicated monitor-window-move test module and KWin freshness/lease guards | **VERIFIED** relevant targeted tests; actual cross-monitor move in current desktop session not exercised. |
| WhatsApp images | Image forwarding not directly supported by older tool route | Image inspect/verify module and explicit recipient/image approval route | **VERIFIED** targeted code tests; external WhatSie live send not exercised today. |
| Computer use | Cancelled input could outlive ownership; stale screenshot could target changed surface | Process lease, region freshness, cancellation draining, fail-closed target checks | **VERIFIED** historical mocked tests; live model task success and KWin focus ambiguity still unknown. |
| Security subprocess | Dead child with inherited pipes prolonged timeout | Direct-child exit separation, 50-ms drain grace, transport close | **VERIFIED** one probe 1510.781 -> 152.873 ms and 26 tests, not a fleet percentile. |
| Chess/audio | Async muted commentary missing text stream | Separate text log callback from audible speech | **VERIFIED** focused cases; six stale unmuted speech expectations remain. |
| Revoked private call | Expired turn could continue to model after local operation error | Entry + error-path revocation checks | **VERIFIED** repaired final focused tests; historical broad source snapshot differed. |
| Power Foundation | Scaffolding separate from native task execution, uncertain restarts | Opt-in status, checkpoints, reconciliation, ticket fingerprints, loopback metadata handshake | **VERIFIED** unit tests only. No exactly-once external side effect, complete authenticated MCP transport or supervised production recovery. |

## 7. Quantitative performance and benchmark register

| Measurement (source) | Before / baseline | After / comparison | Interpretation |
|---|---|---|---|
| Denoiser 3-second seeded audio, `docs/performance.md` | DPDFNet 2.374 CPU-s (79.1% core) | GTCRN 1.005 CPU-s (33.5%) | **VERIFIED** simulated same audio, approximately 58% reduction; recognition parity not proven in all rooms. |
| HUD renderer, 15-second process samples | 73.1% CPU | 20.6% | **VERIFIED** observed restart samples, not controlled whole-system claim. |
| Wake client sample | 54.0% CPU | 19.6% | **VERIFIED** process samples; ambient load confounds. |
| Event-loop telemetry injection 120ms | Max lag median 119.315ms | 0.676ms | **VERIFIED** fixture, not first-audible latency. |
| 20k indexed filenames search | Query median ~504.594ms | ~269.536ms | **VERIFIED** synthetic graph + result parity; normal file sizes may differ. |
| Search race 5ms winner + 80ms others | p50 81.06ms / p95 82.02ms | p50 5.93ms / p95 8.12ms | **VERIFIED** mocked search provider; not web network benchmark. |
| Inbox 10 independent 5ms reads | p50 8.29ms | 29.17ms | **VERIFIED** resource cap trades latency for bounded concurrency. |
| Caption history 20k lines | p50 20.6727ms | 0.0192ms | **VERIFIED** file read fixture, not render. |
| Memory 24 synthetic notes | 1.5562ms cold | 1.0793ms warm | **VERIFIED** warmed cache (1.44x). |
| Search input-to-speaking historical state | p50 0.959s / p95 2.925s among 32 intervals | No controlled after comparison | **UNKNOWN** hardware; observations mixed sessions. |
| Small Whisper synthetic Piper 10 fixtures | Beam 1 mean normalized WER 0.0714 | Beam 5 0.0, median 1.207 -> 1.275s | **VERIFIED** tiny cached fixture, not generalized transcription accuracy. |
| Screen entrance static browser DPR1 | 4.169s median | 3.400s | **VERIFIED** private browser fixture, not KWin compositor FPS. |
| Selected Perplexity decision gate concurrent | 0.827s median for pplx-decider-v1.2 | Other model medians 0.691–0.770s | **VERIFIED** API request benchmark; model quality thresholds differ, not voice turn p50. |
| Barge offline 32-ms frames | Provider native mute delay unmeasured | 64ms synthetic mute p50 for loud/moderate/quiet | **VERIFIED** deterministic simulation, not hardware. |
| Gemini RTC injected overlap | No controlled old/new paired field baseline | 684ms to first received 250ms silence gap | **VERIFIED** recorded RTC frame observation, not cancellation or speaker mute. |

## 8. Interruption architecture: signal, preservation and hazards

```text
MIC FRAME (32 ms simulation) --> duplicate to protected rolling pre-roll
                  |                                |
             local VAD / threshold                  |
                  | triggered                      |
                  v                                |
       local output mute callback                preserve onset
                  |                                |
       provider cancel / sync event <---- continue ALL new mic PCM
                  |                                |
   suppress stale audio and queue       LiveKit source.capture_frame
                  |                                |
       resume after new generation        Gemini can hear entire turn
```

**VERIFIED CODE PATHS:** `src/fast_barge_in.py` contains `MicBargeIn.feed`, `note_user_audio`, `resume`; `src/voice_barge_signals.py` contains `BargeSync` and event encoding; `src/wake_client.py` attaches a fast-barge observer and continues forwarding audio without waiting on provider interruption. **IMPORTANT:** This model assumes the local mute/cancel functions are actually connected to the active speaker sink and remain activated in the running build. The tests do not prove this in physical conditions.

**Recorded synthetic test:** 80 trials each for loud, moderate, quiet, very quiet, noise click, loud background voice. 80/80 detection + byte-exact onset in each of first three groups; 0/80 very-quiet detection; 0/80 click; 80/80 loud background voice, representing false-positive risk. An offline generated Piper-to-Whisper test reports full sample preservation under fast barge and a sample-baseline fraction of 0.94452; recognizer output is not identical to script wording, so lexical correctness cannot be claimed perfect.

**Recorded real network test:** LiveKit RTC with synthesized Piper audio, not physical microphone or speaker; first reply preceded injected speech by 9.56s; injected speech 6.79s; 235 voiced frames received after onset; last voiced frame 10,501.7 ms after onset; 684ms to first 250ms silence gap; provider generation/cancel status not independently correlated.

**INFERRED NEXT WORK:** record monotonic mic onset, VAD vote, mute callback, PCM emission, provider interruption acknowledgement, received RTC frames, OS audio-device buffer flush and acoustic output with synchronized timestamps. A 250-ms PCM buffer cap is not itself a 250-ms barge-in latency guarantee. Add two-mic/echo spatial and false-positive scenarios plus very quiet detection retuning without sacrificing self-speech filtering. Compare the same utterance corpus on the same device before/after in paired random order.

## 9. Email / calendar / WhatsApp route detail

**Email:** Gmail poll -> bounded message parsing and spam/scam/urgency triage -> Perplexity probabilities (urgent 0.5, scam 0.8, event gate 0.05) -> ChatGPT extraction only for event candidates -> validated owner preview -> calendar write on specific confirmation. `benchmarks/decisions-2026-10-28/main/REPORT.md` reports 461 decision measurements and 48 synthetic label checks/model. Paid Mercury and pplx-decider-v1.2 each scored 48/48 on all three labels within that corpus; others were weaker on event gating. **UNKNOWN:** real unseen production precision/recall or account authorization at current time. The old free Mercury endpoint returned 404 during preflight.

**WhatsApp:** WhatSie/headless adapter -> eligible unread chats / photo interpretation -> self/blocklist/group/owner active/fingerprint checks -> persona decision -> ChatGPT writing or owner-style mimic -> policy validation -> dry evidence or approved transport send. Separate *explicit outgoing image* path includes image validation and recipient bind/confirmation; it must not use persona selection as permission. Verification of the actual recipient interface/receipt remains necessary.

**Calendar:** OAuth reconnect on 14 October fixed the observed read-only `invalid_grant` blocker; live creation remained unproven. The latest reports do not demonstrate successful live creation, update, duplicate suppression and readback across account destinations.

## 10. Power Foundation: capabilities vs real integration

- **PASS 1, 29 October (VERIFIED offline):** independent SQLite task/event scaffold, connector registry, denied-by-default executor, evidence metadata, proposal-only workers and snapshot; 65 tests passed. Existing native task system not replaced.
- **PASS 2 (VERIFIED offline):** opt-in native task read-only projection, sanitized checkpoint, bridge `GET /foundation/status`, HUD panel, manual loopback MCP metadata allowlist; 71 tests passed. Not an MCP execution transport.
- **PASS 3 (VERIFIED reconstruction):** source-level reconciler checks snapshots/leases; orphan active steps become `recovery_required`; recovered tasks never automatically dispatch; 76 pre-change tests reported. **UNKNOWN:** original Pass 3 historical report was missing, reconstructed document is not original.
- **PASS 4 (VERIFIED offline):** opt-in attempt IDs and checkpoints, fingerprinted expiring approvals, loopback JSON MCP protocol negotiation/list; baseline 76, recovery approval 14, MCP 6 targeted tests. Unknown actual authenticated remote server and exact downstream guarded external action binding.
- **PASS 5:** `docs/POWER_FOUNDATION_PASS5_HANDOFF.md` is a *handoff request*, not evidence implementation finished.
- **Feature flags:** foundation `JARVIS_POWER_FOUNDATION`; task audit `JARVIS_POWER_TASK_AUDIT`; recovery `JARVIS_POWER_TASK_RECOVERY`; checkpoints `JARVIS_POWER_TASK_CHECKPOINTS`; tickets `JARVIS_POWER_APPROVAL_TICKETS`. Default-off/opt-in descriptions are documented, but active process env is not inferred here.

## 11. Security, privacy and operational boundaries

**VERIFIED DESIGN:** Protect owner/guest/private state at ingress, keep credential values out of reports, use fresh consent grants for screen/file sources, restrict photo and OCR disclosure, screen side effects, separate proposal from execution, and retain request-scoped authorization for send/publish/delete/power/purchases. Computer-use screenshot evidence is bounded by freshness/geometry/foreground control and model-produced intent is untrusted. The desktop surface is NOT container-isolated on the ordinary Linux host. Actual same-window invisible focus change cannot always be detected by screenshots alone.

**Risk tiering (INFERRED):** Low risk: status, search and explicit read-only observation. Medium: read documents, inspect screens with consent, draft messages. High: sends, persistent writes, app input, OS settings, device actions. Critical: privileged system changes, destructive deletion, security testing and transactions; must be separately scoped. This ranking is analysis, not a claim that all tools currently enforce one identical policy engine.

**UNKNOWN:** exhaustive prompt-injection resistance, secrets absence in all historical logs, real-world authorization on every endpoint, and tool-by-tool independent side effect verification across all 233 names. A source inspection or unit test cannot prove those globally.

## 12. Test coverage and outstanding blockers

**Fresh audit 30 Oct:** `PYTHONPATH=.:src .venv/bin/python -m pytest -q -m 'not live_llm and not slow' tests/test_fast_barge_in.py tests/test_voice_barge_signals.py tests/test_voice_upgrade.py tests/test_backend_pool.py tests/test_backend_reliability_upgrade.py tests/test_request_router.py tests/test_power_foundation.py tests/test_power_foundation_pass2.py tests/test_power_foundation_pass3.py tests/test_power_foundation_pass4.py tests/test_power_foundation_mcp_transport.py tests/test_monitor_window_move.py tests/test_wa_image.py tests/test_wa_reply_router.py tests/test_screen_video.py`. Outcome 147 passed, one google/genai third-party deprecation warning, runtime 10.06s. Log: `/tmp/jarvis-master-audit-2026-10-30-tests.log`. No model inference, live WhatsApp send, microphone, hardware or service restart.

**29 Oct Python suite:** 6,916 passed/69 failed/7 skipped/15 deselected in 853.07s. First audit snapshot did not include all subsequent fixes. Fresh failure rerun on final files: 4 fixed, 65 still failed. The rest correspond to stale functionality or old policy. The suite is not green; fix or explicitly retire tests using reviewed user requirements instead of mass-skipping. **29 Oct frontend:** 253 Node tests passed, TypeScript check and production Next export passed with five pre-existing warnings. These are historical reports, not newly rerun frontend tests in this audit.

**Test-proof taxonomy per tool:** (a) source declared; (b) name found in matching test module; (c) focused assertion executed successfully; (d) simulated integration; (e) authorized live end-to-end; (f) independent side-effect receipt. The per-tool appendix covers (a) and (b), and the narrative/cited reports support selected (c)/(d); few routes have (e), and universal (f) is not established.

## 13. Roadmap and acceptance criteria

1. **P0 reliability and baseline:** reconcile the 65 remaining stale expectations with the now single-Jarvis contract, audit orphaned failure cases, pin a clean reproducible checkout snapshot and run the broad suite serial and in isolated batches. Explicitly report flaky timeouts and exclusions.
2. **P0 voice interruption:** hardware-timestamp paired tests covering normal/quiet/far/background user speech, echo, every start phoneme, interruption during tool calls, first local audible silence and provider turn segmentation. Targets are *proposed*: p95 audible stop under 250–300 ms and preserved full words, with false positive/negative bounds measured, not assumed.
3. **P0 action safety:** uniform operation IDs, exact binding between preview/approver/identity/tool invocation; independent receipt verification; no automatic replay after ambiguous send/write.
4. **P1 voice instrumentation:** bounded telemetry for trigger, VAD, model, first packet, speaker, cancellation; split cold/warm models, room and phone. Avoid injecting blocking metrics into the audio loop.
5. **P1 deploy/verify:** controlled versioned restart (only with owner authorization), bridge status, wake hardware readiness, phone incoming ring and answered audio, WhatsApp image sent to a dedicated test recipient with visual confirmation.
6. **P1 integration:** supervised native task recovery, source-anchored correctable memory, authenticated MCP allowlist with metadata sanitization, secure device pairing and real action outcome receipts.
7. **P2 usability:** HUD shows live verified operations vs proposals, voice notes and permission requests; expose endpoints/connector health with timestamps, user-safe rollback, and failed/unknown states.
8. **P2 performance:** after functional acceptance, repeat isolated denoiser, memory, UI, backend and file-search benchmarks under controlled machine load; never infer whole-turn latency from component microbenchmarks.

## 14. Evidence index: primary files

- Canonical topology and 37-module inventory: `artifacts/jarvis-pipeline-2026-10-28/GUIDE.md`, `tool-inventory.json`, `connections.json`, `JARVIS-FULL-PIPELINE.png`.
- October 2 independent and integration: `benchmarks/final-qa-2026-10-02.md`, `integration-qa-2026-10-02.md`, `runtime-optimization-2026-10-02.md`, `insights-qa-2026-10-02.md`.
- Early voice inputs: `docs/performance.md`, `docs/voice-latency.md`, `benchmarks/voice-paths-baseline.json`, `voice-paths-after.json`, `voice-paths-tests.json`.
- October 14 longitudinal audit: `docs/audit-2026-10-14.md`, `benchmarks/audit-small-whisper-2026-10-14.json`.
- October 28 decision API and fake WhatsApp: `benchmarks/decisions-2026-10-28/main/REPORT.md`, `summary.json`, `calls.jsonl`, `whatsapp-*.json`.
- October 29 optimization and QA: `docs/OPTIMIZATION_PLAN_2026-10-29.md`, `work/optimization-2026-10-29/summary.json`, `python-suite.xml`, `audit-failures-rerun.xml`, `frontend-tests.log`, `caption-benchmark.json`, `memory-benchmark.json`.
- October 29 backend and foundation: `docs/JARVIS_RELIABILITY_UPGRADE.md`, `docs/POWER_FOUNDATION_PASS1.md`, `PASS2.md`, `PASS3.md`, `PASS4.md`, `PASS5_HANDOFF.md`.
- October 29 bridge memory: `docs/bridge-memory-guard.md`.
- October 30 interruption: `benchmarks/barge-in-offline-2026-10-30.json`, `gemini-live-overlap-2026-10-30.json`.
- Domain constraints: `docs/computer-use.md`, `assigner-routing.md`, `feature-session-consent.md`, `event-monitoring.md`, `paper-market.md`, `personal-document-librarian.md`.
- Execution log generated by this audit: `/tmp/jarvis-master-audit-2026-10-30-tests.log`.

## Appendix A: Per-tool source declaration and potential test references

The following inventory is generated mechanically from the existing `tool-inventory.json` and the current `tests/test_*.py` text files. **UNKNOWN:** functions with no textual test reference may still have indirect tests. A textual match is neither a successful test nor proof of runtime registration.


### `src/agent.py` (8 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `transfer_back_to_main` | `src/agent.py` | `test_handoff_completion.py`, `test_prompts.py`, `test_handoff.py`, `test_agent.py` | DECLARED; test mention located |
| `transfer_back_to_main` | `src/agent.py` | `test_handoff_completion.py`, `test_prompts.py`, `test_handoff.py`, `test_agent.py` | DECLARED; test mention located |
| `system_health_incident` | `src/agent.py` | `test_health_incident_tool.py` | DECLARED; test mention located |
| `latency_report` | `src/agent.py` | None located | DECLARED; test mention unknown |
| `transfer_to_deep_research` | `src/agent.py` | `test_handoff.py`, `test_handoff_task.py` | DECLARED; test mention located |
| `transfer_to_system_control` | `src/agent.py` | `test_launch_chess.py`, `test_handoff.py`, `test_agent.py`, `test_handoff_recovery.py`, `test_intent.py` | DECLARED; test mention located |
| `launch_chess` | `src/agent.py` | `test_launch_chess.py` | DECLARED; test mention located |
| `transfer_back_to_main` | `src/agent.py` | `test_handoff_completion.py`, `test_prompts.py`, `test_handoff.py`, `test_agent.py` | DECLARED; test mention located |

### `src/draft_tools.py` (8 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `draft_list` | `src/draft_tools.py` | `test_draft_tools.py` | DECLARED; test mention located |
| `draft_read` | `src/draft_tools.py` | `test_draft_tools.py` | DECLARED; test mention located |
| `draft_revise` | `src/draft_tools.py` | `test_draft_tools.py` | DECLARED; test mention located |
| `draft_discard` | `src/draft_tools.py` | `test_draft_tools.py` | DECLARED; test mention located |
| `draft_send` | `src/draft_tools.py` | `test_draft_tools.py` | DECLARED; test mention located |
| `draft_approve_all` | `src/draft_tools.py` | `test_draft_tools.py`, `test_claim_guard.py` | DECLARED; test mention located |
| `open_drafts_ui` | `src/draft_tools.py` | `test_draft_tools.py`, `test_claim_guard.py` | DECLARED; test mention located |
| `close_drafts_ui` | `src/draft_tools.py` | `test_draft_tools.py` | DECLARED; test mention located |

### `src/event_monitor/voice.py` (4 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `monitor_create` | `src/event_monitor/voice.py` | `test_event_monitor.py`, `test_event_monitor_registration.py` | DECLARED; test mention located |
| `monitor_list` | `src/event_monitor/voice.py` | `test_event_monitor_registration.py` | DECLARED; test mention located |
| `monitor_status` | `src/event_monitor/voice.py` | `test_event_monitor_registration.py` | DECLARED; test mention located |
| `monitor_stop` | `src/event_monitor/voice.py` | `test_event_monitor.py`, `test_event_monitor_registration.py` | DECLARED; test mention located |

### `src/goodnight_tools.py` (2 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `goodnight_status` | `src/goodnight_tools.py` | None located | DECLARED; test mention unknown |
| `goodnight_cancel` | `src/goodnight_tools.py` | None located | DECLARED; test mention unknown |

### `src/notify_tools.py` (1 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `notify` | `src/notify_tools.py` | `test_wizard.py`, `test_scheduled_tasks.py`, `test_mail_watch_job.py`, `test_phone.py`, `test_wa_rules.py` | DECLARED; test mention located |

### `src/system/alternatives_tools.py` (1 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `find_cheaper_alternative` | `src/system/alternatives_tools.py` | `test_phone_watch_session.py`, `test_alternatives_tools.py` | DECLARED; test mention located |

### `src/system/background.py` (4 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `usage_stats` | `src/system/background.py` | `test_background.py` | DECLARED; test mention located |
| `crash_report` | `src/system/background.py` | `test_background.py` | DECLARED; test mention located |
| `webcam_snapshot` | `src/system/background.py` | `test_presence.py`, `test_background.py` | DECLARED; test mention located |
| `idle_status` | `src/system/background.py` | `test_background.py` | DECLARED; test mention located |

### `src/system/code_tools.py` (4 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `run_tests` | `src/system/code_tools.py` | `test_code_assist.py`, `test_phone_watch_session.py` | DECLARED; test mention located |
| `explain_failure` | `src/system/code_tools.py` | None located | DECLARED; test mention unknown |
| `confirm_open_pr` | `src/system/code_tools.py` | `test_code_assist.py` | DECLARED; test mention located |
| `open_pr` | `src/system/code_tools.py` | `test_code_assist.py` | DECLARED; test mention located |

### `src/system/computer_use.py` (3 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `computer_use` | `src/system/computer_use.py` | `test_computer_model_routing.py`, `test_wa_private_effects.py`, `test_computer_use.py`, `test_agent.py` | DECLARED; test mention located |
| `computer_use_status` | `src/system/computer_use.py` | `test_computer_use.py`, `test_agent.py` | DECLARED; test mention located |
| `computer_use_cancel` | `src/system/computer_use.py` | `test_computer_use.py`, `test_agent.py`, `test_feature_sessions.py` | DECLARED; test mention located |

### `src/system/core.py` (31 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `tell_time` | `src/system/core.py` | `test_system.py`, `test_needle_router.py`, `test_handoff.py`, `test_turn_manager.py`, `test_agent.py` | DECLARED; test mention located |
| `do_math` | `src/system/core.py` | `test_system.py`, `test_bridge.py` | DECLARED; test mention located |
| `read_clipboard` | `src/system/core.py` | `test_system.py`, `test_watch_guest.py`, `test_phone_watch_session.py` | DECLARED; test mention located |
| `take_os_screenshot` | `src/system/core.py` | `test_system.py`, `test_desktop.py` | DECLARED; test mention located |
| `read_screen_text` | `src/system/core.py` | `test_system.py` | DECLARED; test mention located |
| `set_volume` | `src/system/core.py` | `test_system.py`, `test_school.py`, `test_agent.py`, `test_verified_action_guards.py`, `test_system_action_cancellation.py` | DECLARED; test mention located |
| `media_control` | `src/system/core.py` | `test_tool_usage.py`, `test_system.py`, `test_system_action_cancellation.py`, `test_claim_guard.py`, `test_action_guard.py` | DECLARED; test mention located |
| `now_playing` | `src/system/core.py` | `test_system.py` | DECLARED; test mention located |
| `play_media` | `src/system/core.py` | `test_media_integration.py`, `test_system.py`, `test_school.py`, `test_handoff_recovery.py`, `test_play_media.py` | DECLARED; test mention located |
| `set_brightness` | `src/system/core.py` | `test_system.py`, `test_taskbar.py` | DECLARED; test mention located |
| `battery_status` | `src/system/core.py` | `test_system.py`, `test_quotes.py` | DECLARED; test mention located |
| `disk_space` | `src/system/core.py` | `test_system.py`, `test_school.py`, `test_quotes.py` | DECLARED; test mention located |
| `find_files` | `src/system/core.py` | `test_system.py`, `test_files_tools.py`, `test_file_search_responsiveness.py` | DECLARED; test mention located |
| `list_dir` | `src/system/core.py` | `test_full_local_access.py`, `test_system.py`, `test_hud_events.py` | DECLARED; test mention located |
| `read_file` | `src/system/core.py` | `test_system.py` | DECLARED; test mention located |
| `write_file` | `src/system/core.py` | `test_system.py`, `test_verified_action_guards.py`, `test_outcome_adapters.py`, `test_verified_tools.py` | DECLARED; test mention located |
| `move_file` | `src/system/core.py` | `test_system.py` | DECLARED; test mention located |
| `copy_file` | `src/system/core.py` | `test_system.py` | DECLARED; test mention located |
| `delete_file` | `src/system/core.py` | `test_system.py` | DECLARED; test mention located |
| `recent_downloads` | `src/system/core.py` | `test_system.py` | DECLARED; test mention located |
| `manage_todo` | `src/system/core.py` | `test_system.py`, `test_intent.py` | DECLARED; test mention located |
| `remember_alias` | `src/system/core.py` | `test_system.py` | DECLARED; test mention located |
| `run_alias` | `src/system/core.py` | `test_system.py` | DECLARED; test mention located |
| `list_aliases` | `src/system/core.py` | `test_system.py` | DECLARED; test mention located |
| `open_app` | `src/system/core.py` | `test_tool_usage.py`, `test_launch_ownership.py`, `test_handoff_completion.py`, `test_launcher.py`, `test_system.py` | DECLARED; test mention located |
| `close_app` | `src/system/core.py` | `test_system_action_cancellation.py`, `test_closer.py`, `test_action_guard.py` | DECLARED; test mention located |
| `window_action` | `src/system/core.py` | `test_system.py`, `test_prompts.py`, `test_taskbar.py`, `test_handoff_desktop.py` | DECLARED; test mention located |
| `confirm_power_action` | `src/system/core.py` | `test_system.py`, `test_handoff.py`, `test_power_actions.py` | DECLARED; test mention located |
| `power_control` | `src/system/core.py` | `test_system.py`, `test_handoff.py`, `test_power_actions.py`, `test_routing_dryrun.py` | DECLARED; test mention located |
| `confirm_command_action` | `src/system/core.py` | `test_system.py` | DECLARED; test mention located |
| `run_command` | `src/system/core.py` | `test_full_local_access.py`, `test_system.py`, `test_wa_private_effects.py`, `test_turn_manager.py`, `test_hud_events.py` | DECLARED; test mention located |

### `src/system/daily.py` (15 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `exam_times` | `src/system/daily.py` | `test_exam_timetable.py`, `test_goal_routes.py`, `test_daily.py` | DECLARED; test mention located |
| `school_day` | `src/system/daily.py` | `test_daily.py` | DECLARED; test mention located |
| `daily_briefing` | `src/system/daily.py` | `test_daily.py` | DECLARED; test mention located |
| `study_plan` | `src/system/daily.py` | `test_daily.py` | DECLARED; test mention located |
| `study_tick` | `src/system/daily.py` | `test_daily.py` | DECLARED; test mention located |
| `build_slides` | `src/system/daily.py` | `test_daily.py` | DECLARED; test mention located |
| `build_document` | `src/system/daily.py` | `test_daily.py` | DECLARED; test mention located |
| `whatsapp_status` | `src/system/daily.py` | `test_whatsapp.py`, `test_handoff.py`, `test_daily.py` | DECLARED; test mention located |
| `whatsapp_chats` | `src/system/daily.py` | `test_whatsapp.py`, `test_daily.py` | DECLARED; test mention located |
| `whatsapp_read` | `src/system/daily.py` | `test_whatsapp.py`, `test_intent.py`, `test_daily.py` | DECLARED; test mention located |
| `whatsapp_draft` | `src/system/daily.py` | `test_whatsapp.py`, `test_handoff.py`, `test_daily.py` | DECLARED; test mention located |
| `whatsapp_drafts` | `src/system/daily.py` | None located | DECLARED; test mention unknown |
| `whatsapp_review` | `src/system/daily.py` | None located | DECLARED; test mention unknown |
| `whatsapp_discard` | `src/system/daily.py` | None located | DECLARED; test mention unknown |
| `whatsapp_mimic` | `src/system/daily.py` | `test_daily.py` | DECLARED; test mention located |

### `src/system/desktop.py` (7 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `desktop_screenshot` | `src/system/desktop.py` | `test_prompts.py`, `test_desktop.py`, `test_computer_surface.py` | DECLARED; test mention located |
| `desktop_locate_text` | `src/system/desktop.py` | `test_desktop.py` | DECLARED; test mention located |
| `confirm_desktop_action` | `src/system/desktop.py` | `test_desktop.py`, `test_computer_surface.py` | DECLARED; test mention located |
| `desktop_click` | `src/system/desktop.py` | `test_computer_use_ownership.py`, `test_wa_private_effects.py`, `test_desktop.py` | DECLARED; test mention located |
| `desktop_type` | `src/system/desktop.py` | `test_computer_use_ownership.py`, `test_wa_private_effects.py`, `test_desktop.py` | DECLARED; test mention located |
| `desktop_key` | `src/system/desktop.py` | `test_computer_use_ownership.py`, `test_wa_private_effects.py`, `test_desktop.py` | DECLARED; test mention located |
| `desktop_scroll` | `src/system/desktop.py` | `test_computer_use_ownership.py`, `test_desktop.py` | DECLARED; test mention located |

### `src/system/devices.py` (10 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `wifi_control` | `src/system/devices.py` | `test_devices.py` | DECLARED; test mention located |
| `bluetooth_control` | `src/system/devices.py` | `test_devices.py` | DECLARED; test mention located |
| `audio_output` | `src/system/devices.py` | `test_handoff_completion.py`, `test_devices.py`, `test_voice_transport_recovery.py`, `test_screen_session_holder.py` | DECLARED; test mention located |
| `keyboard_light` | `src/system/devices.py` | `test_devices.py` | DECLARED; test mention located |
| `monitor_setup` | `src/system/devices.py` | `test_devices.py` | DECLARED; test mention located |
| `open_on_monitor` | `src/system/devices.py` | `test_launch_ownership.py`, `test_devices.py` | DECLARED; test mention located |
| `usb_devices` | `src/system/devices.py` | `test_devices.py` | DECLARED; test mention located |
| `phone_devices` | `src/system/devices.py` | `test_devices.py` | DECLARED; test mention located |
| `home_control` | `src/system/devices.py` | `test_devices.py` | DECLARED; test mention located |
| `play_game` | `src/system/devices.py` | `test_devices.py`, `test_school.py` | DECLARED; test mention located |

### `src/system/exam_tools.py` (1 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `exam_schedule` | `src/system/exam_tools.py` | `test_goal_routes.py`, `test_exams.py`, `test_recall_tools.py` | DECLARED; test mention located |

### `src/system/files_tools.py` (7 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `find_files` | `src/system/files_tools.py` | `test_system.py`, `test_files_tools.py`, `test_file_search_responsiveness.py` | DECLARED; test mention located |
| `open_path` | `src/system/files_tools.py` | `test_files_tools.py` | DECLARED; test mention located |
| `describe_folder` | `src/system/files_tools.py` | `test_files_tools.py` | DECLARED; test mention located |
| `organize_folder` | `src/system/files_tools.py` | `test_files_tools.py` | DECLARED; test mention located |
| `confirm_organize` | `src/system/files_tools.py` | `test_files_tools.py` | DECLARED; test mention located |
| `apply_organize_plan` | `src/system/files_tools.py` | `test_files_tools.py` | DECLARED; test mention located |
| `undo_last_organize` | `src/system/files_tools.py` | `test_files_tools.py` | DECLARED; test mention located |

### `src/system/focus_tools.py` (4 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `lock_on` | `src/system/focus_tools.py` | `test_focus_tools.py` | DECLARED; test mention located |
| `focus_status` | `src/system/focus_tools.py` | `test_focus_tools.py` | DECLARED; test mention located |
| `end_focus` | `src/system/focus_tools.py` | `test_focus_tools.py` | DECLARED; test mention located |
| `review_my_work` | `src/system/focus_tools.py` | `test_focus_tools.py` | DECLARED; test mention located |

### `src/system/inbox.py` (10 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `gmail_status` | `src/system/inbox.py` | `test_inbox.py`, `test_agent.py` | DECLARED; test mention located |
| `confirm_email_action` | `src/system/inbox.py` | `test_inbox.py`, `test_draft_tools.py`, `test_power_foundation_outbound.py` | DECLARED; test mention located |
| `gmail_send` | `src/system/inbox.py` | `test_agent_turn_integration.py`, `test_inbox.py`, `test_power_foundation_outbound.py`, `test_turn_manager.py`, `test_power_foundation_status5.py` | DECLARED; test mention located |
| `gmail_reply` | `src/system/inbox.py` | `test_inbox.py` | DECLARED; test mention located |
| `gmail_inbox` | `src/system/inbox.py` | `test_inbox.py`, `test_goal_routes.py`, `test_agent.py`, `test_intent.py`, `test_inbox_concurrency.py` | DECLARED; test mention located |
| `gmail_read` | `src/system/inbox.py` | `test_inbox.py`, `test_goal_routes.py`, `test_agent.py` | DECLARED; test mention located |
| `gmail_thread` | `src/system/inbox.py` | None located | DECLARED; test mention unknown |
| `news_digest` | `src/system/inbox.py` | `test_inbox.py`, `test_goal_routes.py`, `test_agent.py` | DECLARED; test mention located |
| `weather_now` | `src/system/inbox.py` | `test_inbox.py`, `test_goal_routes.py`, `test_agent.py`, `test_intent.py`, `test_quotes.py` | DECLARED; test mention located |
| `morning_briefing` | `src/system/inbox.py` | `test_needle_router.py`, `test_briefing_job.py`, `test_inbox.py`, `test_agent.py`, `test_quotes.py` | DECLARED; test mention located |

### `src/system/look_tools.py` (2 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `look_at_screen` | `src/system/look_tools.py` | `test_look.py`, `test_screen_understanding.py` | DECLARED; test mention located |
| `look_through_camera` | `src/system/look_tools.py` | `test_look.py` | DECLARED; test mention located |

### `src/system/memory_tools.py` (5 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `recall_memory` | `src/system/memory_tools.py` | `test_memory.py` | DECLARED; test mention located |
| `what_was_i_doing` | `src/system/memory_tools.py` | `test_memory.py` | DECLARED; test mention located |
| `pick_up_where_left_off` | `src/system/memory_tools.py` | `test_memory.py` | DECLARED; test mention located |
| `remember_that` | `src/system/memory_tools.py` | `test_memory.py` | DECLARED; test mention located |
| `forget_that` | `src/system/memory_tools.py` | `test_memory.py` | DECLARED; test mention located |

### `src/system/muse_tools.py` (2 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `delegate_to_muse` | `src/system/muse_tools.py` | `test_muse.py` | DECLARED; test mention located |
| `confirm_muse_send` | `src/system/muse_tools.py` | `test_wa_private_effects.py`, `test_muse.py` | DECLARED; test mention located |

### `src/system/osint.py` (6 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `dns_recon` | `src/system/osint.py` | `test_osint.py` | DECLARED; test mention located |
| `whois_lookup` | `src/system/osint.py` | `test_osint.py` | DECLARED; test mention located |
| `crt_enumerate` | `src/system/osint.py` | `test_osint.py` | DECLARED; test mention located |
| `email_footprint` | `src/system/osint.py` | `test_osint.py` | DECLARED; test mention located |
| `shodan_lookup` | `src/system/osint.py` | `test_osint.py` | DECLARED; test mention located |
| `censys_lookup` | `src/system/osint.py` | `test_osint.py` | DECLARED; test mention located |

### `src/system/panel_tools.py` (4 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `show_panel` | `src/system/panel_tools.py` | `test_hud_panels.py`, `test_phone_watch_session.py` | DECLARED; test mention located |
| `hide_panel` | `src/system/panel_tools.py` | `test_hud_panels.py` | DECLARED; test mention located |
| `open_insights` | `src/system/panel_tools.py` | None located | DECLARED; test mention unknown |
| `close_insights` | `src/system/panel_tools.py` | None located | DECLARED; test mention unknown |

### `src/system/pentest.py` (12 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `confirm_pentest_action` | `src/system/pentest.py` | `test_pentest.py` | DECLARED; test mention located |
| `nmap_scan` | `src/system/pentest.py` | `test_latency.py`, `test_intent.py`, `test_hud_events.py`, `test_pentest.py` | DECLARED; test mention located |
| `nikto_sweep` | `src/system/pentest.py` | `test_pentest.py` | DECLARED; test mention located |
| `gobuster_dir` | `src/system/pentest.py` | `test_pentest.py` | DECLARED; test mention located |
| `hashcat_crack` | `src/system/pentest.py` | `test_pentest.py` | DECLARED; test mention located |
| `msf_aux` | `src/system/pentest.py` | `test_pentest.py` | DECLARED; test mention located |
| `msf_exploit` | `src/system/pentest.py` | `test_pentest.py` | DECLARED; test mention located |
| `scope_allow` | `src/system/pentest.py` | `test_pentest.py` | DECLARED; test mention located |
| `scope_revoke` | `src/system/pentest.py` | `test_pentest.py` | DECLARED; test mention located |
| `scope_list` | `src/system/pentest.py` | `test_pentest.py` | DECLARED; test mention located |
| `pentest_report` | `src/system/pentest.py` | `test_intent.py`, `test_pentest.py` | DECLARED; test mention located |
| `freeze_testing` | `src/system/pentest.py` | `test_intent.py`, `test_pentest.py` | DECLARED; test mention located |

### `src/system/projects_tools.py` (5 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `research_project` | `src/system/projects_tools.py` | `test_projects_tools.py`, `test_prompts.py`, `test_phone_watch_session.py` | DECLARED; test mention located |
| `code_project` | `src/system/projects_tools.py` | `test_projects_tools.py`, `test_assigner_voice.py`, `test_prompts.py`, `test_phone_watch_session.py` | DECLARED; test mention located |
| `open_projects` | `src/system/projects_tools.py` | `test_projects_tools.py`, `test_prompts.py`, `test_phone_watch_session.py` | DECLARED; test mention located |
| `list_archive` | `src/system/projects_tools.py` | `test_nav_tools.py` | DECLARED; test mention located |
| `navigate_archive` | `src/system/projects_tools.py` | `test_nav_tools.py` | DECLARED; test mention located |

### `src/system/quotes_tools.py` (1 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `quote_action` | `src/system/quotes_tools.py` | `test_school.py`, `test_latency.py`, `test_quotes.py` | DECLARED; test mention located |

### `src/system/recall_tools.py` (3 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `whatsapp_replies` | `src/system/recall_tools.py` | `test_recall_tools.py` | DECLARED; test mention located |
| `email_activity` | `src/system/recall_tools.py` | `test_recall_tools.py` | DECLARED; test mention located |
| `catch_me_up` | `src/system/recall_tools.py` | `test_recall_tools.py` | DECLARED; test mention located |

### `src/system/reddit.py` (1 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `reddit_front` | `src/system/reddit.py` | `test_reddit.py`, `test_agent.py` | DECLARED; test mention located |

### `src/system/schedule_tools.py` (2 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `schedule_task` | `src/system/schedule_tools.py` | `test_schedule_tools.py`, `test_schedule_wiring.py` | DECLARED; test mention located |
| `scheduled_tasks` | `src/system/schedule_tools.py` | `test_scheduled_tasks.py`, `test_schedule_tools.py`, `test_schedule_wiring.py` | DECLARED; test mention located |

### `src/system/school_tools.py` (2 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `set_school_mode` | `src/system/school_tools.py` | `test_school.py`, `test_bridge.py` | DECLARED; test mention located |
| `confirm_loud_action` | `src/system/school_tools.py` | None located | DECLARED; test mention unknown |

### `src/system/suit_tools.py` (1 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `set_suit_diagnostics` | `src/system/suit_tools.py` | `test_suit_diagnostics.py`, `test_action_guard.py` | DECLARED; test mention located |

### `src/system/task_tools.py` (8 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `start_task` | `src/system/task_tools.py` | `test_agent_tasks.py`, `test_phone_watch_session.py` | DECLARED; test mention located |
| `task_status` | `src/system/task_tools.py` | `test_task_cancellation_tools.py`, `test_agent_tasks.py`, `test_feature_sessions.py` | DECLARED; test mention located |
| `approve_task` | `src/system/task_tools.py` | `test_agent_tasks.py`, `test_phone_watch_session.py` | DECLARED; test mention located |
| `cancel_task` | `src/system/task_tools.py` | `test_task_cancellation_tools.py`, `test_feature_sessions.py` | DECLARED; test mention located |
| `cancel_tool` | `src/system/task_tools.py` | `test_cancellation_adapters.py`, `test_feature_sessions.py` | DECLARED; test mention located |
| `redirect_task` | `src/system/task_tools.py` | `test_task_cancellation_tools.py`, `test_feature_sessions.py` | DECLARED; test mention located |
| `start_workshop` | `src/system/task_tools.py` | `test_workshop.py`, `test_phone_watch_session.py` | DECLARED; test mention located |
| `workshop_status` | `src/system/task_tools.py` | `test_workshop.py` | DECLARED; test mention located |

### `src/system/vision_tools.py` (5 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `look_at_me` | `src/system/vision_tools.py` | `test_vision_tools.py` | DECLARED; test mention located |
| `start_posture_watch` | `src/system/vision_tools.py` | `test_vision_tools.py` | DECLARED; test mention located |
| `stop_posture_watch` | `src/system/vision_tools.py` | `test_vision_tools.py` | DECLARED; test mention located |
| `posture_report` | `src/system/vision_tools.py` | `test_vision_tools.py` | DECLARED; test mention located |
| `set_hand_gestures` | `src/system/vision_tools.py` | `test_vision_tools.py` | DECLARED; test mention located |

### `src/system/watch_tools.py` (5 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `start_watch_mode` | `src/system/watch_tools.py` | `test_full_local_access.py`, `test_computer_watch_process_control.py`, `test_watch_routing.py` | DECLARED; test mention located |
| `stop_watch_mode` | `src/system/watch_tools.py` | `test_computer_watch_process_control.py`, `test_watch_routing.py` | DECLARED; test mention located |
| `watch_mode_status` | `src/system/watch_tools.py` | `test_computer_watch_process_control.py`, `test_watch_routing.py` | DECLARED; test mention located |
| `watch_mode_briefing` | `src/system/watch_tools.py` | None located | DECLARED; test mention unknown |
| `start_guard_mode` | `src/system/watch_tools.py` | `test_watch_routing.py` | DECLARED; test mention located |

### `src/system/window_tools.py` (4 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `list_windows` | `src/system/window_tools.py` | `test_window_tools.py` | DECLARED; test mention located |
| `focus_window` | `src/system/window_tools.py` | `test_window_ctl.py`, `test_games_server.py`, `test_window_tools.py` | DECLARED; test mention located |
| `arrange_windows` | `src/system/window_tools.py` | `test_window_tools.py` | DECLARED; test mention located |
| `undo_layout` | `src/system/window_tools.py` | `test_window_tools.py` | DECLARED; test mention located |

### `src/system/workspace_tools.py` (20 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `drive_find` | `src/system/workspace_tools.py` | `test_workspace_tools.py` | DECLARED; test mention located |
| `read_google_doc` | `src/system/workspace_tools.py` | `test_workspace_tools.py` | DECLARED; test mention located |
| `create_google_doc` | `src/system/workspace_tools.py` | `test_workspace_tools.py` | DECLARED; test mention located |
| `read_sheet` | `src/system/workspace_tools.py` | `test_workspace_tools.py` | DECLARED; test mention located |
| `confirm_workspace_action` | `src/system/workspace_tools.py` | `test_workspace_tools.py` | DECLARED; test mention located |
| `add_sheet_rows` | `src/system/workspace_tools.py` | `test_workspace_tools.py` | DECLARED; test mention located |
| `notion_find` | `src/system/workspace_tools.py` | `test_workspace_tools.py` | DECLARED; test mention located |
| `notion_read` | `src/system/workspace_tools.py` | `test_workspace_tools.py` | DECLARED; test mention located |
| `notion_add_note` | `src/system/workspace_tools.py` | `test_workspace_tools.py` | DECLARED; test mention located |
| `generate_document` | `src/system/workspace_tools.py` | `test_workspace_tools.py` | DECLARED; test mention located |
| `import_schedule_to_calendar` | `src/system/workspace_tools.py` | `test_workspace_tools.py` | DECLARED; test mention located |
| `email_to_calendar` | `src/system/workspace_tools.py` | None located | DECLARED; test mention unknown |
| `confirm_calendar_import` | `src/system/workspace_tools.py` | `test_audit_calendar.py`, `test_workspace_tools.py`, `test_event_suggest.py`, `test_event_extractor.py` | DECLARED; test mention located |
| `exams_to_calendar` | `src/system/workspace_tools.py` | `test_event_extractor.py` | DECLARED; test mention located |
| `email_events` | `src/system/workspace_tools.py` | `test_event_extractor.py` | DECLARED; test mention located |
| `confirm_email_event` | `src/system/workspace_tools.py` | `test_event_extractor.py` | DECLARED; test mention located |
| `dismiss_email_event` | `src/system/workspace_tools.py` | `test_event_extractor.py` | DECLARED; test mention located |
| `calendar_upcoming` | `src/system/workspace_tools.py` | `test_goal_routes.py` | DECLARED; test mention located |
| `route_request` | `src/system/workspace_tools.py` | `test_request_router.py` | DECLARED; test mention located |
| `ask_backend` | `src/system/workspace_tools.py` | `test_request_router.py`, `test_backend_reliability_upgrade.py`, `test_workspace_tools.py` | DECLARED; test mention located |

### `src/tools.py` (25 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `open_link` | `src/tools.py` | None located | DECLARED; test mention unknown |
| `open_visible_url` | `src/tools.py` | `test_visible_url.py` | DECLARED; test mention located |
| `shopping_search` | `src/tools.py` | `test_shopping_tool.py` | DECLARED; test mention located |
| `search_the_web` | `src/tools.py` | `test_prompts.py`, `test_acks.py`, `test_web_search.py`, `test_character_agent_wiring.py`, `test_browser.py` | DECLARED; test mention located |
| `open_url` | `src/tools.py` | `test_launch_ownership.py`, `test_needle_router.py`, `test_latency.py`, `test_handoff.py`, `test_agent.py` | DECLARED; test mention located |
| `open_google_app` | `src/tools.py` | `test_visible_url.py` | DECLARED; test mention located |
| `dismiss_popups` | `src/tools.py` | `test_wa_private_effects.py`, `test_browser.py` | DECLARED; test mention located |
| `read_page` | `src/tools.py` | `test_latency.py`, `test_workspace_tools.py`, `test_notion_api.py`, `test_web_search.py`, `test_browser.py` | DECLARED; test mention located |
| `inspect_page` | `src/tools.py` | `test_browser.py` | DECLARED; test mention located |
| `go_back` | `src/tools.py` | None located | DECLARED; test mention unknown |
| `take_screenshot` | `src/tools.py` | None located | DECLARED; test mention unknown |
| `click` | `src/tools.py` | `test_chess_voice_engine.py`, `test_power_foundation.py`, `test_computer_freshness.py`, `test_keyword_spot.py`, `test_wa_private_effects.py` | DECLARED; test mention located |
| `confirm_browser_action` | `src/tools.py` | None located | DECLARED; test mention unknown |
| `type_text` | `src/tools.py` | `test_wa_private_effects.py`, `test_desktop.py`, `test_computer_surface.py`, `test_browser.py` | DECLARED; test mention located |
| `scroll` | `src/tools.py` | `test_nav_tools.py`, `test_computer_freshness.py`, `test_prompts.py`, `test_computer_use.py`, `test_projects.py` | DECLARED; test mention located |
| `auto_scroll` | `src/tools.py` | `test_prompts.py`, `test_browser.py` | DECLARED; test mention located |
| `press_key` | `src/tools.py` | `test_wa_private_effects.py` | DECLARED; test mention located |
| `open_helper_google` | `src/tools.py` | `test_quotes.py`, `test_browser.py` | DECLARED; test mention located |
| `read_helper` | `src/tools.py` | `test_browser.py` | DECLARED; test mention located |
| `close_helper` | `src/tools.py` | `test_quotes.py`, `test_browser.py` | DECLARED; test mention located |
| `list_tabs` | `src/tools.py` | `test_browser.py` | DECLARED; test mention located |
| `switch_tab` | `src/tools.py` | `test_browser.py` | DECLARED; test mention located |
| `start_deep_research` | `src/tools.py` | `test_prompts.py`, `test_browser.py` | DECLARED; test mention located |
| `read_research_page` | `src/tools.py` | `test_browser.py` | DECLARED; test mention located |
| `close_deep_research` | `src/tools.py` | `test_browser.py` | DECLARED; test mention located |

### `src/youtube_download.py` (3 declared occurrences)

| Tool | Source | Potential textual test refs (max 5) | Evidence level |
|---|---|---|---|
| `download_youtube_video` | `src/youtube_download.py` | `test_tool_usage.py`, `test_youtube_download.py`, `test_youtube_extended.py`, `test_routing_dryrun.py` | DECLARED; test mention located |
| `confirm_youtube_download` | `src/youtube_download.py` | None located | DECLARED; test mention unknown |
| `cancel_youtube_download` | `src/youtube_download.py` | `test_tool_usage.py` | DECLARED; test mention located |

## Appendix B: All indexed Python test modules

Total: 367 current `tests/test_*.py` files. The following is an exact filename inventory, not a fresh test pass.

- `tests/test_acks.py`
- `tests/test_action_guard.py`
- `tests/test_action_outcome.py`
- `tests/test_action_verify.py`
- `tests/test_active_window.py`
- `tests/test_activity.py`
- `tests/test_activity_watch.py`
- `tests/test_agent.py`
- `tests/test_agent_tasks.py`
- `tests/test_agent_turn_integration.py`
- `tests/test_alternatives_tools.py`
- `tests/test_announce_call.py`
- `tests/test_assigner_bridge.py`
- `tests/test_assigner_context.py`
- `tests/test_assigner_headless.py`
- `tests/test_assigner_projects.py`
- `tests/test_assigner_runtime.py`
- `tests/test_assigner_voice.py`
- `tests/test_audio_aec.py`
- `tests/test_audio_ctl.py`
- `tests/test_audio_dsp.py`
- `tests/test_audio_playback.py`
- `tests/test_audit_calendar.py`
- `tests/test_audit_mail_log.py`
- `tests/test_audit_persistence.py`
- `tests/test_audit_proactive_delivery.py`
- `tests/test_backend_model.py`
- `tests/test_backend_pool.py`
- `tests/test_backend_reliability_upgrade.py`
- `tests/test_background.py`
- `tests/test_bedtime_calls.py`
- `tests/test_bridge.py`
- `tests/test_bridge_auth_startup.py`
- `tests/test_bridge_characters.py`
- `tests/test_bridge_execution.py`
- `tests/test_bridge_memory_guard.py`
- `tests/test_bridge_polling.py`
- `tests/test_bridge_setting_outcomes.py`
- `tests/test_bridge_turn_protocol.py`
- `tests/test_bridge_turns.py`
- `tests/test_briefing_job.py`
- `tests/test_browser.py`
- `tests/test_budget.py`
- `tests/test_build_monitor.py`
- `tests/test_build_monitor_bridge.py`
- `tests/test_build_monitor_tools.py`
- `tests/test_calendar_reconnect.py`
- `tests/test_camera_hub.py`
- `tests/test_cancellation_adapters.py`
- `tests/test_cancellation_control.py`
- `tests/test_caption_storage.py`
- `tests/test_character_agent_wiring.py`
- `tests/test_character_authority.py`
- `tests/test_character_persona.py`
- `tests/test_character_prewarm.py`
- `tests/test_character_runtime.py`
- `tests/test_character_voice.py`
- `tests/test_characters.py`
- `tests/test_chatgpt_cli.py`
- `tests/test_chess_cli.py`
- `tests/test_chess_commands.py`
- `tests/test_chess_commands_new.py`
- `tests/test_chess_commentary.py`
- `tests/test_chess_dialogue_controls.py`
- `tests/test_chess_docs.py`
- `tests/test_chess_game_flow.py`
- `tests/test_chess_history.py`
- `tests/test_chess_hookup.py`
- `tests/test_chess_integration.py`
- `tests/test_chess_intro.py`
- `tests/test_chess_isolation.py`
- `tests/test_chess_levels.py`
- `tests/test_chess_on_demand.py`
- `tests/test_chess_review.py`
- `tests/test_chess_router.py`
- `tests/test_chess_series.py`
- `tests/test_chess_state.py`
- `tests/test_chess_stockfish_strength.py`
- `tests/test_chess_store.py`
- `tests/test_chess_takeover.py`
- `tests/test_chess_text_output.py`
- `tests/test_chess_tts_lifecycle.py`
- `tests/test_chess_tui.py`
- `tests/test_chess_ui_wiring.py`
- `tests/test_chess_verify_a.py`
- `tests/test_chess_verify_b.py`
- `tests/test_chess_verify_fixes.py`
- `tests/test_chess_voice_engine.py`
- `tests/test_chess_voice_hold.py`
- `tests/test_chess_voice_moves.py`
- `tests/test_chess_wake_priority.py`
- `tests/test_chess_wizard.py`
- `tests/test_cinematic.py`
- `tests/test_claim_guard.py`
- `tests/test_claude_backend.py`
- `tests/test_closer.py`
- `tests/test_code_assist.py`
- `tests/test_codex_worker.py`
- `tests/test_computer_focus.py`
- `tests/test_computer_freshness.py`
- `tests/test_computer_model_routing.py`
- `tests/test_computer_surface.py`
- `tests/test_computer_use.py`
- `tests/test_computer_use_ownership.py`
- `tests/test_computer_watch.py`
- `tests/test_computer_watch_adversarial.py`
- `tests/test_computer_watch_process_control.py`
- `tests/test_computer_watch_visual_lifecycle.py`
- `tests/test_daily.py`
- `tests/test_decision_budget.py`
- `tests/test_desktop.py`
- `tests/test_devices.py`
- `tests/test_diagnostics.py`
- `tests/test_direct_perf.py`
- `tests/test_dnd.py`
- `tests/test_docgen.py`
- `tests/test_draft_engine.py`
- `tests/test_draft_notify.py`
- `tests/test_draft_tools.py`
- `tests/test_drafts_ui.py`
- `tests/test_dragfx_field.py`
- `tests/test_echo_guard.py`
- `tests/test_english_whisper.py`
- `tests/test_event_extractor.py`
- `tests/test_event_monitor.py`
- `tests/test_event_monitor_host.py`
- `tests/test_event_monitor_registration.py`
- `tests/test_event_monitor_summary.py`
- `tests/test_event_suggest.py`
- `tests/test_exam_timetable.py`
- `tests/test_exams.py`
- `tests/test_eyes.py`
- `tests/test_fast_barge_in.py`
- `tests/test_fast_path.py`
- `tests/test_feature_sessions.py`
- `tests/test_file_search_responsiveness.py`
- `tests/test_files_tools.py`
- `tests/test_focus.py`
- `tests/test_focus_tools.py`
- `tests/test_follow_up_state.py`
- `tests/test_full_local_access.py`
- `tests/test_games_server.py`
- `tests/test_gemini_manual_turn_patch.py`
- `tests/test_gemini_watchdog.py`
- `tests/test_gestures.py`
- `tests/test_goal_routes.py`
- `tests/test_goodnight.py`
- `tests/test_goodnight_detached.py`
- `tests/test_goodnight_evidence.py`
- `tests/test_goodnight_host.py`
- `tests/test_goodnight_ingress.py`
- `tests/test_goodnight_runtime.py`
- `tests/test_goodnight_supported.py`
- `tests/test_goodnight_v2.py`
- `tests/test_google_api.py`
- `tests/test_google_apps.py`
- `tests/test_google_token_cache.py`
- `tests/test_greeting_retry.py`
- `tests/test_handoff.py`
- `tests/test_handoff_completion.py`
- `tests/test_handoff_desktop.py`
- `tests/test_handoff_recovery.py`
- `tests/test_handoff_task.py`
- `tests/test_health_incident_tool.py`
- `tests/test_health_recorder.py`
- `tests/test_health_recorder_bridge.py`
- `tests/test_health_recorder_client.py`
- `tests/test_hud_events.py`
- `tests/test_hud_panels.py`
- `tests/test_hud_voice_state.py`
- `tests/test_inbox.py`
- `tests/test_inbox_concurrency.py`
- `tests/test_input_activity_lifecycle.py`
- `tests/test_input_idle.py`
- `tests/test_insights_proxy.py`
- `tests/test_intent.py`
- `tests/test_jarvis_requested_improvements.py`
- `tests/test_jobs.py`
- `tests/test_keyword_spot.py`
- `tests/test_kwin_windows.py`
- `tests/test_latency.py`
- `tests/test_latency_log_summary.py`
- `tests/test_latency_rollup.py`
- `tests/test_launch_chess.py`
- `tests/test_launch_ownership.py`
- `tests/test_launcher.py`
- `tests/test_librarian.py`
- `tests/test_librarian_pdf.py`
- `tests/test_librarian_source_selection.py`
- `tests/test_librarian_sources.py`
- `tests/test_librarian_tools.py`
- `tests/test_linked_turn.py`
- `tests/test_local_stt.py`
- `tests/test_local_voice.py`
- `tests/test_local_voice_lifecycle.py`
- `tests/test_look.py`
- `tests/test_mail_link.py`
- `tests/test_mail_watch_job.py`
- `tests/test_media_integration.py`
- `tests/test_media_selection.py`
- `tests/test_memory.py`
- `tests/test_memory_retrieval_cache.py`
- `tests/test_mercury_decide.py`
- `tests/test_mic_clean.py`
- `tests/test_mic_uplink_framing.py`
- `tests/test_mint_token.py`
- `tests/test_monitor_window_move.py`
- `tests/test_muse.py`
- `tests/test_nav_tools.py`
- `tests/test_needle_router.py`
- `tests/test_notify.py`
- `tests/test_notify_tools.py`
- `tests/test_notion_api.py`
- `tests/test_on_demand_without_background.py`
- `tests/test_openrouter_chat.py`
- `tests/test_osint.py`
- `tests/test_outcome_adapters.py`
- `tests/test_outcome_worker_ownership.py`
- `tests/test_paper_market_atomic.py`
- `tests/test_paper_market_calendar.py`
- `tests/test_paper_market_engine.py`
- `tests/test_paper_market_feed.py`
- `tests/test_paper_market_intraday.py`
- `tests/test_paper_market_replay.py`
- `tests/test_paper_market_service.py`
- `tests/test_paper_market_worker.py`
- `tests/test_pdf_fetch.py`
- `tests/test_pentest.py`
- `tests/test_pentest_cancellation.py`
- `tests/test_persona.py`
- `tests/test_phone.py`
- `tests/test_phone_announce_helper.py`
- `tests/test_phone_api.py`
- `tests/test_phone_calls.py`
- `tests/test_phone_monitor_lifecycle.py`
- `tests/test_phone_watch_session.py`
- `tests/test_play_media.py`
- `tests/test_playback_gate.py`
- `tests/test_power_actions.py`
- `tests/test_power_foundation.py`
- `tests/test_power_foundation_allowlist.py`
- `tests/test_power_foundation_mcp_transport.py`
- `tests/test_power_foundation_memory.py`
- `tests/test_power_foundation_outbound.py`
- `tests/test_power_foundation_pass2.py`
- `tests/test_power_foundation_pass3.py`
- `tests/test_power_foundation_pass4.py`
- `tests/test_power_foundation_pr.py`
- `tests/test_power_foundation_review.py`
- `tests/test_power_foundation_status5.py`
- `tests/test_presence.py`
- `tests/test_proactive.py`
- `tests/test_proactive_engine.py`
- `tests/test_proactive_watcher_responsiveness.py`
- `tests/test_projects.py`
- `tests/test_projects_tools.py`
- `tests/test_prompts.py`
- `tests/test_quotes.py`
- `tests/test_realtime_native_turns.py`
- `tests/test_realtime_startup.py`
- `tests/test_recall_tools.py`
- `tests/test_reddit.py`
- `tests/test_remote_session.py`
- `tests/test_request_router.py`
- `tests/test_robot_arm.py`
- `tests/test_routing_dryrun.py`
- `tests/test_sandbox_computer_use.py`
- `tests/test_sandbox_core.py`
- `tests/test_schedule_tools.py`
- `tests/test_schedule_wiring.py`
- `tests/test_scheduled_phone_delivery.py`
- `tests/test_scheduled_tasks.py`
- `tests/test_school.py`
- `tests/test_school_monitors.py`
- `tests/test_school_shell_invariants.py`
- `tests/test_screen_control_socket.py`
- `tests/test_screen_host.py`
- `tests/test_screen_occlusion.py`
- `tests/test_screen_session_holder.py`
- `tests/test_screen_understanding.py`
- `tests/test_screen_video.py`
- `tests/test_search_latency.py`
- `tests/test_second_brain.py`
- `tests/test_secondary_wake_removed.py`
- `tests/test_session_launcher.py`
- `tests/test_setting_outcomes.py`
- `tests/test_shopping_bridge.py`
- `tests/test_shopping_tool.py`
- `tests/test_sidecars.py`
- `tests/test_single_jarvis.py`
- `tests/test_stt_quality.py`
- `tests/test_suit_diagnostics.py`
- `tests/test_summons.py`
- `tests/test_switch_conductor.py`
- `tests/test_switch_parse.py`
- `tests/test_switch_prewarm.py`
- `tests/test_switch_state.py`
- `tests/test_system.py`
- `tests/test_system_action_cancellation.py`
- `tests/test_talk_gate.py`
- `tests/test_task_cancel_races.py`
- `tests/test_task_cancellation_tools.py`
- `tests/test_taskbar.py`
- `tests/test_taskbar_sandbox.py`
- `tests/test_telegram_bot.py`
- `tests/test_telemetry.py`
- `tests/test_tool_usage.py`
- `tests/test_turn_manager.py`
- `tests/test_urgency_judge.py`
- `tests/test_verified_action_guards.py`
- `tests/test_verified_tools.py`
- `tests/test_visible_url.py`
- `tests/test_vision_tools.py`
- `tests/test_voice_barge_signals.py`
- `tests/test_voice_fixtures.py`
- `tests/test_voice_fx.py`
- `tests/test_voice_job_cleanup.py`
- `tests/test_voice_latency_probe.py`
- `tests/test_voice_matrix.py`
- `tests/test_voice_regressions.py`
- `tests/test_voice_transport_recovery.py`
- `tests/test_voice_upgrade.py`
- `tests/test_wa_approval_transport.py`
- `tests/test_wa_approvals.py`
- `tests/test_wa_autoreply.py`
- `tests/test_wa_complete_readback.py`
- `tests/test_wa_headless.py`
- `tests/test_wa_image.py`
- `tests/test_wa_mimic.py`
- `tests/test_wa_private_effects.py`
- `tests/test_wa_reply_router.py`
- `tests/test_wa_rules.py`
- `tests/test_wa_vision.py`
- `tests/test_wake_client.py`
- `tests/test_wake_connection_recovery.py`
- `tests/test_wake_events.py`
- `tests/test_wake_events_character.py`
- `tests/test_wake_join_audio.py`
- `tests/test_wake_recovery.py`
- `tests/test_watch_ascii.py`
- `tests/test_watch_capture_package.py`
- `tests/test_watch_control.py`
- `tests/test_watch_eyes.py`
- `tests/test_watch_guest.py`
- `tests/test_watch_mode.py`
- `tests/test_watch_native_adversarial.py`
- `tests/test_watch_particles.py`
- `tests/test_watch_routing.py`
- `tests/test_watch_visual.py`
- `tests/test_web_search.py`
- `tests/test_whatsapp.py`
- `tests/test_whatsapp_session.py`
- `tests/test_window_ctl.py`
- `tests/test_window_layout.py`
- `tests/test_window_monitor.py`
- `tests/test_window_sampler.py`
- `tests/test_window_tools.py`
- `tests/test_wizard.py`
- `tests/test_workshop.py`
- `tests/test_workspace_tools.py`
- `tests/test_youtube_direct.py`
- `tests/test_youtube_download.py`
- `tests/test_youtube_download_bridge.py`
- `tests/test_youtube_download_site.py`
- `tests/test_youtube_extended.py`
- `tests/test_youtube_history.py`
- `tests/test_youtube_resolve.py`

## Appendix C: Source and results catalog

- `benchmarks/final-qa-2026-10-02.md`
- `benchmarks/insights-qa-2026-10-02.md`
- `benchmarks/integration-qa-2026-10-02.md`
- `benchmarks/runtime-optimization-2026-10-02.md`
- `benchmarks/transcription-recovery-2026-10-02.md`
- `benchmarks/audit-small-whisper-2026-10-14.json`
- `benchmarks/barge-in-offline-2026-10-30.json`
- `benchmarks/character-voices.json`
- `benchmarks/file-search-after-2026-10-02.json`
- `benchmarks/file-search-before-2026-10-02.json`
- `benchmarks/final-qa-source-sha256-2026-10-02.json`
- `benchmarks/gemini-live-overlap-2026-10-30.json`
- `benchmarks/integration-source-sha256-2026-10-02.json`
- `benchmarks/pentest-pipe-cleanup-2026-10-02.json`
- `benchmarks/piper-lifecycle-2026-10-02.json`
- `benchmarks/proactive-watcher-after-2026-10-02.json`
- `benchmarks/proactive-watcher-before-2026-10-02.json`
- `benchmarks/runtime-optimization-source-sha256-2026-10-02.json`
- `benchmarks/startup-import-2026-10-02.json`
- `benchmarks/switch-ready.json`
- `benchmarks/voice-observations-before.json`
- `benchmarks/voice-paths-after.json`
- `benchmarks/voice-paths-baseline.json`
- `benchmarks/voice-paths-tests.json`
- `benchmarks/voice-runtime-2026-10-01.json`
- `docs/JARVIS_RELIABILITY_UPGRADE.md`
- `docs/OPTIMIZATION_PLAN_2026-10-29.md`
- `docs/PHONE_API.md`
- `docs/POWER_FOUNDATION_NEXT_AGENT_PROMPT.md`
- `docs/POWER_FOUNDATION_PASS1.md`
- `docs/POWER_FOUNDATION_PASS2.md`
- `docs/POWER_FOUNDATION_PASS3.md`
- `docs/POWER_FOUNDATION_PASS4.md`
- `docs/POWER_FOUNDATION_PASS5_HANDOFF.md`
- `docs/assigner-routing.md`
- `docs/audit-2026-10-14.md`
- `docs/bridge-memory-guard.md`
- `docs/computer-use.md`
- `docs/decision-model.md`
- `docs/event-monitor-host.md`
- `docs/event-monitoring.md`
- `docs/feature-session-consent.md`
- `docs/goodnight-detached.md`
- `docs/goodnight-v2.md`
- `docs/insights-integration.md`
- `docs/on-demand-feature-flags.md`
- `docs/paper-market.md`
- `docs/performance.md`
- `docs/personal-document-librarian.md`
- `docs/robot-arm-hardware.md`
- `docs/robot-arm-software.md`
- `docs/screen-session-holder.md`
- `docs/system-health-recorder.md`
- `docs/voice-build-monitor.md`
- `docs/voice-latency.md`
- `docs/voice-model-upgrades.md`
- `docs/youtube-download.md`
- `work/optimization-2026-10-29/caption-benchmark.json`
- `work/optimization-2026-10-29/memory-benchmark.json`
- `work/optimization-2026-10-29/summary.json`
- `artifacts/jarvis-pipeline-2026-10-28/connections.json`
- `artifacts/jarvis-pipeline-2026-10-28/tool-inventory.json`

**End of master report.**
