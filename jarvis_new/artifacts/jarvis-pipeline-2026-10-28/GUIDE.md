# Jarvis: input to every output

Functional architecture and declared tool inventory, not a line-by-line Python call graph. Optional/experimental paths are explicitly labelled. No credentials or private conversations included.

## 01. THE COMPLETE SYSTEM AT A GLANCE

Three parallel worlds share the same services: live conversation, asynchronous owner jobs, and automated incoming-message/background processing.

### 01.1 — INPUT CHANNELS

Microphone / phone / HUD controls / Telegram / Mail Link / WhatsApp / email / timers / sensors / files.
Details: Section 02.

Source: `wake_client.py; bridge.py`

### 01.2 — INGRESS & PERMISSIONS

Authentication, owner/guest/private-call policy, echo filtering, activity and consent. Some inputs become commands; others become evidence.
Details: 03-04, 10.

Source: `phone_session.py; action_guard.py`

### 01.3 — ROUTE, THINK, EXECUTE

Gemini conversation; existing tool grammar; Perplexity at specific decision gates; backend/research/code workers.
Details: 04-09.

Source: `agent.py; request_router.py`

### 01.4 — OUTPUT FAN-OUT

Voice, captions, UI, notifications, drafts, sent messages, calendar/doc changes, desktop/device effects, archives, status or deliberate silence.
Details: 11-12.

Source: `notify.py; speak.py; tools`

## 02. EVERY INPUT FAMILY

These are separate ingress paths. They do not all pass through speech recognition or through Perplexity.

### 02.1 — DESKTOP MICROPHONE

Wake phrase / push-to-talk -> wake client -> room creation -> microphone uplink -> LiveKit audio.
Continue at 03.

Source: `wake_client.py; mic_uplink.py`

### 02.2 — PHONE / JARVISLINK

Authenticated phone session or scheduled invitation -> private room and mobile audio. Phone telemetry/photos are separate data paths.
Continue at 03 / 10 / 11.

Source: `phone_api.py; phone_session.py`

### 02.3 — HUD / DESKTOP CONTROLS

Tauri frontend buttons, tray, taskbar, typed commands and /talk audio -> authenticated bridge endpoints.
Direct controls -> 04 / 05; audio -> 03.

Source: `shell; frontend; bridge.py`

### 02.4 — TELEGRAM / MAIL LINK

Approved chat text/voice notes or authenticated command email -> text command routing / chat / bounded task controls. Mail Link checks sender alignment and replay.
Continue at 04.

Source: `telegram_bot.py; mail_link.py`

### 02.5 — UNREAD EMAIL

Gmail poll + parsed headers/body -> filters, triage, drafts and event suggestions. This is not the command-mail channel.
Continue at 09.

Source: `mail_watch_job.py; system/inbox.py`

### 02.6 — WHATSAPP CHAT INPUT

WhatSie/headless adapter reads eligible unread chats; optional photos become descriptions. Per-chat permissions select available personas.
Continue at 08.

Source: `wa_autoreply.py; wa_headless.py`

### 02.7 — FILES / VISUAL EVIDENCE

Explicitly supplied paths, selected active window, camera frames, phone photos and consented observations. Evidence is not permission.
Continue at 05 / 10.

Source: `librarian_sources.py; camera_hub.py`

### 02.8 — TIME / EVENTS / SENSORS

Reminders, exams, calendar, jobs, system health, presence, input idle, public event feeds and paper-market quotes.
Continue at 11.

Source: `scheduled_tasks.py; proactive/*`

## 03. VOICE TRANSPORT AND THE THREE AUDIO PIPELINES

JARVIS_PIPELINE selects one branch. Realtime is the code default. The new request decider is a tool-boundary decision, not a replacement for this audio pipeline.

### 03.1 — WAKE / PHONE / PTT

Accepted audio session -> authenticated room/token. Owner/guest/private-session identity stays attached to the session.

Source: `wake_client.py; mint_token.py`

### 03.2 — LIVEKIT TRANSPORT

Microphone audio in; agent and phone session connect; captions/data-channel events travel beside audio.

Source: `agent.py; linked_turn.py`

### 03.3 — REALTIME: GEMINI LIVE

Audio -> Gemini native activity/turn detection -> native conversation + function calls -> native speech audio. No separate Whisper/Silero gate on this branch.

Source: `agent.py:_voice_model`

### 03.4 — SPOKEN / TEXT OUTPUT

Audio -> laptop/phone speakers. Text/transcripts -> captions and HUD. Tool results re-enter the live model; interruptions use the realtime adapter.

Source: `gemini_watchdog.py; hud_events.py`

### 03.5 — DIRECT BRANCH

LiveKit audio + local Silero endpointing. Uses local-server transport; Gemini reasoning still uses a cloud API.

Source: `agent.py:_session_for_pipeline`

### 03.6 — LOCAL WHISPER STT

Faster-Whisper converts speech to text. Strict commands may run through the fast path before the text model.
Continue at 04.

Source: `local_stt.py; whisper_config.py`

### 03.7 — GEMINI TEXT BRAIN

Default gemini-3.8-flash via direct Google client. Existing text-model fallback chain; tool results feed back into conversation.

Source: `agent.py; prompts.py`

### 03.8 — PIPER TTS

Sentence streaming -> local ONNX voice -> playback gate -> speakers/captions. Optional configured cloud TTS override exists.

Source: `local_voice.py; audio_playback.py`

### 03.9 — LOCAL / CLOUD-INFERENCE BRANCH

Despite its name, this branch uses LiveKit Cloud Inference for STT and the text model.

Source: `agent.py:JARVIS_PIPELINE=local`

### 03.10 — ASSEMBLYAI STT

assemblyai/universal-streaming -> transcript; local turn/command handling remains a separate branch from realtime native audio.

Source: `agent.py:CHEAP_STT_MODEL`

### 03.11 — GEMINI VIA INFERENCE

Gemini text LLM and its tool collection -> model response. Specialist agents use the branch-appropriate shared LLM.

Source: `agent.py:_brain_llm`

### 03.12 — LOCAL VOICE OR OVERRIDE

Piper by default; configured JARVIS_TTS can choose an inference voice. Output converges with the common playback/HUD path.

Source: `agent.py:_session_tts`

## 04. OWNER TURN -> DIRECT ACTION OR MODEL HANDOFF

Obvious local controls and conversation do not require an extra cloud decision. Perplexity is consulted for uncertain routing and before ask_backend starts a reasoning job.

### 04.1 — FRESH OWNER TURN

Transcript/text plus allowed context: recent dialogue, memory, telemetry and tool results. Guest/private-call rules restrict available capabilities.

Source: `context/injector.py; phone_session.py`

### 04.2 — EARLY TRUSTED CONTROLS

Echo/consent checks -> visual-control commands -> supported goodnight ingress -> feature sessions -> protocols. Realtime audio remains owned by Gemini.

Source: `agent.py:on_user_turn_completed`

### 04.3 — TEXT FAST PATH

Only on text pipelines: strict grammar -> intent resolver / optional Needle -> read-goal routing -> existing verified tool. Unmatched text falls through to Gemini.

Source: `intent/fast_path.py; intent/needle_router.py`

### 04.4 — DIRECT JARVIS

Conversation / quick explanations / arithmetic -> answer. Current quick facts -> search. Volume/media/apps/calendar etc. -> own tools.
Tools: 05; outputs: 12.

Source: `agent.py; prompts.py`

### 04.5 — UNCLEAR / DEEP REQUEST

route_request(request) is advisory only. ask_backend(task) independently checks the route before creating a background job.

Source: `system/workspace_tools.py`

### 04.6 — PERPLEXITY DECIDER

Larger pplx-decider-v1.2 -> typed destination + explicit provider preference. Below 0.8 / failure -> direct, except an anchored explicit backend request is retained.

Source: `request_router.py; mercury_decide.py`

### 04.7 — FOUR DESTINATIONS

direct -> Jarvis
backend -> ask_backend / Section 06
research -> research_project / 07
code -> code_project / 07.
A label itself executes nothing.

Source: `request_router.py; workspace_tools.py`

### 04.8 — RETURN TO CONVERSATION

Direct advice returns without spawning work. Confirmed background start returns an acknowledgement; later completion feeds the result back to the owner.

Source: `WorkspaceTools._in_background`

## 05. ALL TOOL / INTEGRATION DOMAINS

Parallel capability branches off Jarvis's tool dispatch. Calls use each tool's own guards and return evidence to the model. Exact declared endpoint names are listed in Section 14.

### 05.1 — DESKTOP & WINDOWS

App launch/close, visible URLs, focus/list/arrange windows, keyboard/mouse automation, file operations and power actions.
Outputs: desktop effects + verified/unverified receipts.

Source: `system/core.py; window_tools.py; tools.py`

### 05.2 — MEDIA & DOWNLOADS

YouTube resolution/playback in the system browser; media keys; direct video downloads and local output files. Backend browser is separate from visible playback.

Source: `youtube_download.py; system/audio_ctl.py`

### 05.3 — DEVICES & SMART HOME

Wi-Fi, Bluetooth, audio sinks, monitors, keyboard lighting, USB inspection, KDE Connect and optional Home Assistant.
Outputs: device state, transfers or control effects.

Source: `system/devices.py`

### 05.4 — WEB / SHOPPING / REDDIT

Helper Google / search APIs / DuckDuckGo; read-only backend browsing; structured shopping results; Reddit tools. Purchases and posting retain approvals.

Source: `tools.py; browser.py; shopping.py; system/reddit.py`

### 05.5 — MAIL / WHATSAPP

Read threads, draft and review responses, inspect prior activity, approved mimic lists and exact-recipient send workflows. Automation paths also run independently.

Source: `system/inbox.py; system/whatsapp.py; 08-09`

### 05.6 — DOCS / SHEETS / DRIVE / NOTION

Read/search/export; new documents and invoices; guarded updates to existing content; Google sharing and returned URLs.
Outputs: cloud document changes + previews.

Source: `system/workspace_tools.py; google_apps.py; docgen.py`

### 05.7 — CALENDAR & EXAMS

Read upcoming events; schedule-file/email extraction -> preview -> confirm import; manage stored exam timetable and reminders.

Source: `event_extractor.py; exam_tools.py; workspace_tools.py`

### 05.8 — FILES & LIBRARIAN

Second-brain index, document retrieval, local/Drive source search, PDFs, exact-location source hits and opening files. Evidence feeds answers or draft work.

Source: `system/files_tools.py; librarian*.py; second_brain.py`

### 05.9 — MEMORY & ACTIVITY

Explicit remember/forget, recall decisions and prior sessions; activity tracking; pick up where left off.
Outputs: answer/context or vault/state updates.

Source: `memory.py; activity.py; memory_tools.py`

### 05.10 — FOCUS / SCHOOL / PROTOCOLS

Focus window/time sessions; school quiet/feature policy; protocol commands, suit/HUD controls and context-specific behaviour.

Source: `focus.py; school.py; protocols.py; suit_tools.py`

### 05.11 — VISION / SCREEN / WATCH

On-demand visual questions; foreground OCR; shared camera; scoped observation, posture/gestures and monitor summaries. Consent restrictions apply.
Details: 10.

Source: `look.py; screen_understanding.py; watch_tools.py`

### 05.12 — CODE / TESTS / PROJECTS

Dedicated coding proposals; background tests; failure explanation; reviewed PR opening; archive navigation.
Coding proposal is not patch application.

Source: `projects.py; system/code_tools.py; 07`

### 05.13 — TASKS / WORKSHOP / SCHEDULE

Multi-step plans, read-only research/code/ops workshop, timers, reminders, scheduled phone calls, cancellation and status.
Details: 07 / 11.

Source: `agent_tasks.py; workshop.py; scheduled_tasks.py`

### 05.14 — PUBLIC INTELLIGENCE / SECURITY

Passive OSINT and bounded network/system information; active pentest tools behind the dedicated specialist/scope gates.

Source: `system/osint.py; pentest.py; system/scope.py`

### 05.15 — GAMES / CHESS / ROBLOX

Launch game windows, voice chess commands and game speech/UI state; desktop launcher for supported game clients.

Source: `games/*; system/devices.py`

### 05.16 — HEALTH / HUD / INSIGHTS

Measured latency and incidents, system stats, monitor lifecycle, panels, AI-usage tracker and paper-market dashboards.
Outputs: visual status and diagnostic evidence.

Source: `health_recorder*.py; hud_panels.py; insights_proxy.py`

## 06. BACKGROUND REASONING: CHATGPT <-> CLAUDE

This pool applies to ask_backend reasoning/drafting only. It does not replace the separate research, coding, WhatsApp or extraction adapters.

### 06.1 — ASK_BACKEND GUARD

Perplexity must return backend; direct/research/code decisions return guidance and backend_started=false. Original full task and provider preference are preserved.

Source: `workspace_tools.py; request_router.py`

### 06.2 — PROVIDER POOL

Auto defaults to ChatGPT first; explicit Claude/ChatGPT preference sets first attempt. Known limit-hit providers rest for 300 seconds. Actual remaining quota is not guessed.

Source: `backend_pool.py`

### 06.3 — ISOLATED TEXT WORKER

ChatGPT: existing headless Codex backend model.
Claude: subscription-authenticated CLI, no tools/MCP/skills, no saved session, neutral temporary cwd. Both get only the supplied text task.

Source: `chatgpt_cli.py; claude_backend.py`

### 06.4 — ANSWER / LIMIT / ERROR

Success -> return answer to Jarvis.
Recognised limit/unavailability -> other provider within time budget.
Ambiguous timeout/error -> report failure, not blind replay.

Source: `backend_pool.py; backend_model.py`

## 07. DURABLE PROJECTS, TASKS AND WORKSHOP

Different job types retain different adapters and permissions; Claude limit fallback from Section 06 does not silently widen their capabilities.

### 07.1 — IN-DEPTH RESEARCH

Explicit research request -> research_project -> authenticated bridge /projects -> durable research metadata/progress.

Source: `system/projects_tools.py; projects.py`

### 07.2 — CHATGPT RESEARCH WORKER

Existing research model with WebSearch/WebFetch and bounded execution. Reads public sources and writes a sourced report.

Source: `projects.py:start_research`

### 07.3 — PROJECT ARCHIVE

report.md + sources.md + summary/status/progress. Completion/cancel/error are persisted. Live caller can poll completion.

Source: `~/.jarvis/projects/ (state)`

### 07.4 — OWNER DELIVERY

Spoken summary when live; archive UI/report file and existing notification policy otherwise. No claim of a report until completion.

Source: `projects_tools.py; notify.py`

### 07.5 — CODING REQUEST

code_project -> private/guest checks -> authenticated bridge -> explicitly activated dedicated Assigner queue.

Source: `projects_tools.py; projects.py`

### 07.6 — ASSIGNER + WORKERS

Trusted bounded source inventory and dependency context -> validated coordinator plan -> isolated no-tools generate/review workers. Existing fixed worker model allowlist.

Source: `assigner_context.py; assigner_headless.py`

### 07.7 — VALIDATED PROPOSAL

Strict patch-hunk validation, hashes, manifests, raw results and proposal.patch in archive. Block if context/settings/proposal are invalid.

Source: `assigner_headless.py; projects.py`

### 07.8 — REVIEW, NOT AUTO-APPLY

Owner reviews proposal. Applying patches, running tests, merging or deployment remain separate actions. Existing run_tests/open_pr tools have their own workflows.

Source: `docs/assigner-routing.md; code_tools.py`

### 07.9 — MULTI-STEP TASK / WORKSHOP

start_task plans think/research/write/consequential steps. Workshop chooses scoped research/code/ops specialists; these are read-only roles.

Source: `agent_tasks.py; workshop.py`

### 07.10 — BOUNDED EXECUTION

Task state/revision/lease locks; background model steps and prior-result context. Consequential actions are separated and paused for owner approval.

Source: `agent_tasks.py; cancellation_control.py`

### 07.11 — MERGE / PREPARE

Task outputs or workshop reports -> stored status and write-up. Cancellation has settlement receipts; a speech interruption is not an external undo.

Source: `workshop.py; cancellation_adapters.py`

### 07.12 — READ BACK / HAND BACK

Jarvis reports progress/results. Approved consequential steps return to the normal guarded tools; workshop never modifies the world by itself.

Source: `system/task_tools.py; workshop.py`

## 08. WHATSAPP: INCOMING -> PERSONA -> DRAFT -> SEND

WhatsApp's persona names are reply modes, not a global character switch. Rude mode remains off in the current deployment unless the owner enables it.

### 08.1 — UNREAD CHAT / PHOTOS

Wait gate -> read recent chat -> optional photo description. Self/blocklist, group mention, owner-active, muted, one-note and fingerprint checks run first.

Source: `wa_autoreply.py; wa_vision.py`

### 08.2 — PERPLEXITY PERSONA

Allowed choices built by code:
Jarvis always; owner_style only approved mimic chats; rude_jarvis only enabled + not trusted. Latest messages distinguished from history.

Source: `wa_reply_router.py`

### 08.3 — PERSONA-SPECIFIC WRITER

Jarvis / rude Jarvis: existing ChatGPT fast writer.
Owner style: optional free style analyst + real-owner examples + existing mimic writer. Writer failure uses existing OpenRouter fallback.

Source: `wa_autoreply.py; wa_mimic.py; wa_analyst.py`

### 08.4 — VALIDATE & DELIVER

Uncertainty/failure -> polite Jarvis. Bot-identity / sensitive mimic requests -> owner handoff, no reply. Disallowed rude reply suppressed. Live auto-reply -> WhatsApp text + digest; dry -> saved evidence only.

Source: `wa_autoreply.py:_send_reply`

### 08.5 — EXPLICIT OUTGOING MESSAGE

Owner asks to send/reply to a recipient -> canonical draft record. This separate workflow is not permission granted by the persona model.

Source: `system/whatsapp.py; wa_approvals.py`

### 08.6 — EXACT RECIPIENT REVIEW

Open/read back the selected recipient and message. The private owner phone workflow requires a fresh exact spoken challenge.

Source: `wa_approvals.py; system/whatsapp.py`

### 08.7 — SINGLE-USE AUTHORIZATION

Recheck identity, exact draft, recipient and current channel; reject stale/generic approvals and model-invented confirmation.

Source: `wa_approvals.py; action_guard.py`

### 08.8 — MESSAGE / RECEIPT / REFUSAL

Confirmed transport send returns its receipt. Refusal or uncertainty is reported honestly. No simulated click is allowed to replace the approval gate.

Source: `system/whatsapp.py; wa_headless.py`

## 09. EMAIL: TRIAGE, DRAFTS AND CALENDAR SUGGESTIONS

Incoming email is untrusted content. Polling with no new qualifying mail makes no Perplexity inference call.

### 09.1 — GMAIL POLL + FILTERS

Unread IDs -> bounded fetch/parse -> seen/retry state. Skip own/undeliverable noise; distinguish bulk/human; keyword/classroom triage.

Source: `mail_watch_job.py; system/inbox.py`

### 09.2 — PERPLEXITY URGENCY / SCAM

Two yes/no questions in one request. Urgent >=0.5; scam >=0.8 overrides urgency. Failure/incomplete response -> existing ChatGPT judge -> keyword fallback if unavailable.

Source: `urgency_judge.py; mercury_decide.py`

### 09.3 — MAIL-SPECIFIC POLICY

High/normal/skip outcome; dedupe and sender/thread acknowledgement limits. Draft engine creates bounded replies; configured auto-ack is a distinct preauthorised path.

Source: `draft_engine.py; mail_watch_job.py`

### 09.4 — MAIL OUTPUTS

Draft queue / relaxed or urgent acknowledgement when enabled / urgent notification or owner message / logged normal mail. Explicit reviewed mail sends retain confirmation.

Source: `draft_notify.py; inbox.py; notify.py`

### 09.5 — EMAIL / SCHEDULE FILE

Dated content in mail, a supplied PDF/image/text schedule, or an explicit conversation import.

Source: `event_extractor.py; backend_model.py`

### 09.6 — EVENT GATE / EXTRACTION

Incoming email gate: p(event)<0.05 skips ChatGPT. Otherwise existing ChatGPT extracts structured dates/times; validators reject bad/past/uncertain entries.

Source: `event_extractor.py; backend_model.py`

### 09.7 — PENDING SUGGESTION

Future event candidates and previews are saved. Read back event details; owner must confirm the exact import or suggestion.

Source: `event_suggest.py; workspace_tools.py`

### 09.8 — CALENDAR OR NO CHANGE

Confirmed candidate -> Google Calendar event + receipt. Decline -> dismissed state. No event / invalid extraction -> no calendar write.

Source: `google_api.py; confirm_email_event`

## 10. VISUAL INPUT, WATCH MODE AND PRIVATE CONTEXT

A transcript, model call or on-screen instruction cannot create visual consent. Screen OCR and image-question paths have different scopes.

### 10.1 — EXPLICIT SCREEN REQUEST

Fresh trusted owner turn + supported host/session lifecycle. Bind capture permission to the selected foreground window and current generation.

Source: `feature_sessions.py; screen_session_holder.py`

### 10.2 — FOREGROUND OCR

Foreground-only capture/OCR with byte/pixel/text/age bounds. No ambient capture; raw screen images are not uploaded by this OCR path.

Source: `screen_understanding.py; screen_host.py`

### 10.3 — TEXT EXPLANATION

Tool-less explanation adapter receives bounded untrusted OCR text + the owner's question, not execution instructions.

Source: `screen_understanding.py:explanation_input`

### 10.4 — PRIVATE ANSWER / CLEANUP

Scoped speech/context handling; fresh provider/session generation where required; close/cancel clears evidence. No retained screenshot unless explicitly authorised.

Source: `screen_session_holder.py; feature_sessions.py`

### 10.5 — CAMERA / VISION REQUEST

On-demand object/window question; shared camera lease or permitted active-window image path. Phone photos are a separate explicit ingest source.

Source: `look.py; camera_hub.py; phone_api.py`

### 10.6 — VISION ADAPTER

Configured vision client/fallbacks answer the specific question. WhatsApp photo descriptions feed the text conversation. Camera consumers share the hub.

Source: `look.py; eyes.py; wa_vision.py`

### 10.7 — OPTIONAL WATCHERS

Watch mode, appearance/posture/phone/gesture checks, window metadata and registered activity/build/event monitoring. Each has its own consent/flag/scope.

Source: `computer_watch*; focus.py; eyes.py`

### 10.8 — OBSERVATIONS, NOT AUTHORITY

Answer, watch briefing, draft suggestion or notification -> Section 11/12. Observation does not authorize clicking, sending, coding or arbitrary remediation.

Source: `watch_control.py; build_monitor.py`

## 11. BACKGROUND SERVICES, TIMERS AND RELIABILITY

The bridge hosts long-lived sidecar threads so many jobs survive the end of a voice call. Features can be disabled or require explicit setup.

### 11.1 — SIGNAL SOURCES

Calendar/deadlines/mail/jobs/system telemetry, exam schedule, due reminders/calls, focus drift and allowlisted public event feeds.

Source: `proactive/sources/*; scheduled_tasks.py`

### 11.2 — TRIGGER / MONITOR ENGINE

Thresholds, time windows, dedupe, job state and public evidence. Optional event triage uses a separate free-router adapter, not the Perplexity persona router.

Source: `proactive/engine.py; event_monitor/*`

### 11.3 — DELIVERY POLICY

Quiet hours, daily caps, batching, active/idle/unknown presence, mute/school/call conditions and queue TTL. Scheduled calls require supported phone readiness and valid invitations.

Source: `proactive/policy.py; notify.py; phone_calls.py`

### 11.4 — NOTIFY / QUEUE / CALL

Immediate permitted speech + caption/toast, silent desktop notification, queued announcement after activity, scheduled reminder or phone invitation. Queue/invitation is not proof of delivery.

Source: `notify.py; speak.py; scheduled_tasks.py`

### 11.5 — REGISTERED JOB / SERVICE

Bridge supervisors and Tauri sidecars, agent worker, wake/microphone, local LiveKit, task/assigner jobs and supported goodnight inventory.

Source: `bridge.py; shell/src-tauri/src/manager.rs`

### 11.6 — HEALTH & CANCELLATION

Liveness/readiness; Gemini reconnect/failover; tool latency; opted-in health recorder; explicit job cancellation and settled outcomes. Failures return bounded errors.

Source: `gemini_watchdog.py; cancellation_control.py`

### 11.7 — SUPPORTED GOODNIGHT V2

Fresh owner audio ingress -> registered-scope activity / busy checks and fencing -> ten-minute idle policy -> session lock with readback, when eligible. Old watcher is retired.

Source: `goodnight_host.py; goodnight_runtime.py`

### 11.8 — STATE / DIAGNOSTIC OUTPUT

Recovered job state, truthful failure/cancel receipt, health/latency report, archive status or verified lock. No global claim over unrelated external coding apps.

Source: `action_outcome.py; health_recorder*.py`

### 11.9 — PAPER-MARKET INPUT

Quote feeds, calendar and persisted paper portfolio / replay data. Dedicated paper-market services run separately from voice.

Source: `paper_market/feed.py; service.py`

### 11.10 — SIMULATION / OPTIONAL TA

Paper engine/strategy/replay and separately configured TradingAgents analysis. These are not live broker trade execution paths.

Source: `paper_market/engine.py; ta_runner.py`

### 11.11 — PAPER STATE / INSIGHTS

Local positions, fills, charts, replay metrics, reports and AI-usage tracker data are exposed through read-only dashboard proxies.

Source: `paper_market/*; insights_proxy.py`

### 11.12 — DASHBOARD OUTPUT

HUD/Insights paper-market panels and usage view. Optional notifications retain delivery policy. Paper fills are simulations, not real trades.

Source: `frontend/markets; bridge.py`

## 12. EVERY OUTPUT FAMILY AND WHAT COUNTS AS SUCCESS

Several outputs can happen for one request. Evidence and delivery receipts must distinguish prepared, launched, sent, verified, failed and deliberately skipped states.

### 12.1 — AUDIO / CONVERSATION

Native Gemini audio; Piper/inference TTS; laptop or phone speakers; spoken handoff/completion/error; interruption-aware playback.

Source: `agent.py; local_voice.py; speak.py`

### 12.2 — TEXT / VISUAL UI

Live captions, HUD state/cards/panels, archive viewer, watch briefing, game windows, desktop toasts, usage/paper dashboards and visible browser pages.

Source: `hud_events.py; frontend; shell`

### 12.3 — MESSAGES / CALLS

Gmail replies/acknowledgements, WhatsApp auto-replies or confirmed sends, configured Telegram responses, owner handoff alerts and bounded phone invitations.

Source: `inbox.py; whatsapp.py; telegram_bot.py`

### 12.4 — FILES / CLOUD OBJECTS

Drafts, reports, source lists, code proposals, downloaded video, generated documents/invoices, Drive/Docs/Sheets/Notion changes and confirmed calendar entries.

Source: `projects.py; docgen.py; google_api.py`

### 12.5 — DESKTOP / DEVICE EFFECTS

Open/focus/arrange apps and windows; volume/media/network/display/lighting changes; optional smart-home/KDE Connect effects; approved input or power operations.

Source: `system/*; tools.py; power_actions.py`

### 12.6 — PERSISTENCE / EVIDENCE

JSON/SQLite state, memory vault, indexes, drafts, consent records, job revisions, action logs, captions, monitor events and cancellation/verification receipts.

Source: `~/.jarvis/ (private runtime state)`

### 12.7 — GUARDED CONSEQUENCES

Send/post/pay/book/delete/deploy/update actions require their existing exact-context approvals where applicable. Prepared content or a model's yes is not approval.

Source: `action_guard.py; confirmation tools`

### 12.8 — NO OUTPUT IS A VALID BRANCH

Filtered mail/chat; no new input; duplicate; blocked/guest/private restriction; mute/quiet queue; uncertain evidence; declined preview; handoff; unsupported feature.

Source: `per-feature gates; action_outcome.py`

## 13. SHARED STATE, SECURITY AND THE THREE PERPLEXITY CONNECTIONS

Only these three decision boundaries use the newly integrated larger Perplexity model. The rest of the system retains its existing adapters.

### 13.1 — PERPLEXITY / OPENROUTER

1. Email urgency/phishing + event gate (09).
2. WhatsApp persona selection (08).
3. Direct/backend/research/code advice + backend guard (04/06).
Typed probabilities; no generated response text.

Source: `mercury_decide.py (legacy module name)`

### 13.2 — CREDENTIAL / CONFIG BOUNDARY

Dedicated decision key in private env/keys.env; model/feature switches; existing OAuth/subscription logins. Never include key values in prompts, logs or this guide.

Source: `openrouter_chat.py; keys.env`

### 13.3 — CONTEXT / ACTION BOUNDARY

Memory, files, web pages, messages, OCR and tool replies are evidence. Application-owned session identity and fresh owner approvals authorize effects.

Source: `action_guard.py; feature_sessions.py`

### 13.4 — PERSIST / RETURN / RETRY

Archive/state feeds status and future context. Specific provider quota errors may trigger bounded failover. Timers/queues can defer delivery; error/unknown stays explicit.

Source: `backend_pool.py; notify.py; jobs.py`

## 14. COMPLETE DECLARED TOOL-ENDPOINT INVENTORY

236 @function_tool endpoints across 37 source files, extracted from this checkout. This appendix lists declarations, including optional/specialist tools; it does not assert that every endpoint is enabled in every session.

### 14.1 — agent.py

transfer_back_to_main • transfer_back_to_main • system_health_incident • latency_report • transfer_to_deep_research • transfer_to_system_control • launch_chess • transfer_back_to_main

Source: `src/agent.py`

### 14.2 — draft_tools.py

draft_list • draft_read • draft_revise • draft_discard • draft_send • draft_approve_all • open_drafts_ui • close_drafts_ui

Source: `src/draft_tools.py`

### 14.3 — event_monitor/voice.py

monitor_create • monitor_list • monitor_status • monitor_stop

Source: `src/event_monitor/voice.py`

### 14.4 — goodnight_tools.py

goodnight_status • goodnight_cancel

Source: `src/goodnight_tools.py`

### 14.5 — notify_tools.py

notify

Source: `src/notify_tools.py`

### 14.6 — system/alternatives_tools.py

find_cheaper_alternative

Source: `src/system/alternatives_tools.py`

### 14.7 — system/background.py

usage_stats • crash_report • webcam_snapshot • idle_status

Source: `src/system/background.py`

### 14.8 — system/code_tools.py

run_tests • explain_failure • confirm_open_pr • open_pr

Source: `src/system/code_tools.py`

### 14.9 — system/computer_use.py

computer_use • computer_use_status • computer_use_cancel

Source: `src/system/computer_use.py`

### 14.10 — system/core.py

tell_time • do_math • read_clipboard • take_os_screenshot • read_screen_text • set_volume • media_control • now_playing • play_media • set_brightness • battery_status • disk_space • find_files • list_dir • read_file • write_file • move_file • copy_file • delete_file • recent_downloads • manage_todo • remember_alias • run_alias • list_aliases • open_app • close_app • window_action • confirm_power_action • power_control • confirm_command_action • run_command

Source: `src/system/core.py`

### 14.11 — system/daily.py

exam_times • school_day • daily_briefing • study_plan • study_tick • build_slides • build_document • whatsapp_status • whatsapp_chats • whatsapp_read • whatsapp_draft • whatsapp_drafts • whatsapp_review • whatsapp_discard • whatsapp_mimic

Source: `src/system/daily.py`

### 14.12 — system/desktop.py

desktop_screenshot • desktop_locate_text • confirm_desktop_action • desktop_click • desktop_type • desktop_key • desktop_scroll

Source: `src/system/desktop.py`

### 14.13 — system/devices.py

wifi_control • bluetooth_control • audio_output • keyboard_light • monitor_setup • open_on_monitor • usb_devices • phone_devices • home_control • play_game

Source: `src/system/devices.py`

### 14.14 — system/exam_tools.py

exam_schedule

Source: `src/system/exam_tools.py`

### 14.15 — system/files_tools.py

find_files • open_path • describe_folder • organize_folder • confirm_organize • apply_organize_plan • undo_last_organize

Source: `src/system/files_tools.py`

### 14.16 — system/focus_tools.py

lock_on • focus_status • end_focus • review_my_work

Source: `src/system/focus_tools.py`

### 14.17 — system/inbox.py

gmail_status • confirm_email_action • gmail_send • gmail_reply • gmail_inbox • gmail_read • gmail_thread • news_digest • weather_now • morning_briefing

Source: `src/system/inbox.py`

### 14.18 — system/look_tools.py

look_at_screen • look_through_camera

Source: `src/system/look_tools.py`

### 14.19 — system/memory_tools.py

recall_memory • what_was_i_doing • pick_up_where_left_off • remember_that • forget_that

Source: `src/system/memory_tools.py`

### 14.20 — system/muse_tools.py

delegate_to_muse • confirm_muse_send

Source: `src/system/muse_tools.py`

### 14.21 — system/osint.py

dns_recon • whois_lookup • crt_enumerate • email_footprint • shodan_lookup • censys_lookup

Source: `src/system/osint.py`

### 14.22 — system/panel_tools.py

show_panel • hide_panel • open_insights • close_insights

Source: `src/system/panel_tools.py`

### 14.23 — system/pentest.py

confirm_pentest_action • nmap_scan • nikto_sweep • gobuster_dir • hashcat_crack • msf_aux • msf_exploit • scope_allow • scope_revoke • scope_list • pentest_report • freeze_testing

Source: `src/system/pentest.py`

### 14.24 — system/projects_tools.py

research_project • code_project • open_projects • list_archive • navigate_archive

Source: `src/system/projects_tools.py`

### 14.25 — system/quotes_tools.py

quote_action

Source: `src/system/quotes_tools.py`

### 14.26 — system/recall_tools.py

whatsapp_replies • email_activity • catch_me_up

Source: `src/system/recall_tools.py`

### 14.27 — system/reddit.py

reddit_front

Source: `src/system/reddit.py`

### 14.28 — system/schedule_tools.py

schedule_task • scheduled_tasks

Source: `src/system/schedule_tools.py`

### 14.29 — system/school_tools.py

set_school_mode • confirm_loud_action

Source: `src/system/school_tools.py`

### 14.30 — system/suit_tools.py

set_suit_diagnostics

Source: `src/system/suit_tools.py`

### 14.31 — system/task_tools.py

start_task • task_status • approve_task • cancel_task • cancel_tool • redirect_task • start_workshop • workshop_status

Source: `src/system/task_tools.py`

### 14.32 — system/vision_tools.py

look_at_me • start_posture_watch • stop_posture_watch • posture_report • set_hand_gestures

Source: `src/system/vision_tools.py`

### 14.33 — system/watch_tools.py

start_watch_mode • stop_watch_mode • watch_mode_status • watch_mode_briefing • start_guard_mode

Source: `src/system/watch_tools.py`

### 14.34 — system/window_tools.py

list_windows • focus_window • arrange_windows • undo_layout

Source: `src/system/window_tools.py`

### 14.35 — system/workspace_tools.py

drive_find • read_google_doc • create_google_doc • read_sheet • confirm_workspace_action • add_sheet_rows • notion_find • notion_read • notion_add_note • generate_document • import_schedule_to_calendar • email_to_calendar • confirm_calendar_import • exams_to_calendar • email_events • confirm_email_event • dismiss_email_event • calendar_upcoming • route_request • ask_backend

Source: `src/system/workspace_tools.py`

### 14.36 — tools.py

open_link • open_visible_url • shopping_search • search_the_web • open_url • open_google_app • dismiss_popups • read_page • inspect_page • go_back • take_screenshot • click • confirm_browser_action • type_text • scroll • auto_scroll • press_key • open_helper_google • read_helper • close_helper • list_tabs • switch_tab • start_deep_research • read_research_page • close_deep_research

Source: `src/tools.py`

### 14.37 — youtube_download.py

download_youtube_video • confirm_youtube_download • cancel_youtube_download

Source: `src/youtube_download.py`
