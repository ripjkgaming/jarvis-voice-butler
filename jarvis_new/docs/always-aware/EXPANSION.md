# Always Aware expansion coordination

Parent owns sidecar.py, integration tests and this document. Preserve all pre-existing changes. Workers own only the files assigned in their prompts, and tests named for their component. Use uv and behavior tests first. No commits or real WhatsApp/calendar mutations during tests.

## Shared contracts

All new components live under src/aware. Use plain dictionaries at boundaries and injected dependencies for hardware tests. Times are epoch seconds except monotonic duration clocks. Default timezone Asia/Singapore.

- message_store.Store(path=None): SQLite durable storage default $JARVIS_HOME/aware/messages.sqlite3. ingest(chat_id, name, messages, *, boundary, complete, now, error='') -> newly inserted messages; chat(chat_id) -> checkpoint dict; pending(consumer, limit=100) -> messages; acknowledge(consumer, message_ids); context(chat_id, limit=20). Messages have id, chat_id, timestamp, order, text, sender, me. Unique identity is (chat_id,id); queue acknowledgments must use this composite identity. Store original payload. Checkpoint coverage separate from ingestion watermark; atomic persistence before progress. Provide explicit API details in completion report.
- whatsapp_scan.scan(store=None, now=None) -> dict with all selected chat outcomes. async function. Uses system.whatsapp date reader; snapshots first 20 chats, incoming/outgoing, no repeated downstream ingestion. Check off/pause/kill between chats. Never infer stable chat identity from display name. Partial coverage is explicit and retryable.
- calendar_stream.process(store=None, now=None) -> dict. Synchronous adapter to existing whatsapp_calendar staging/extraction policy, acknowledgment after durable staging; context and original timestamps. Historical complete conversation cancellation semantics. Parent schedules it separately from reads.
- diagnostics.sample(now=None) -> dict readings/incidents/availability. Lightweight, unavailable distinct from healthy. Quiet incident dedup persisted. No simulated suit data.
- break_policy.Policy(path=None).observe(observation, now=None) -> decision dict eligible/reason. Observation includes activity/confidence/present/presence_at/observed_at/in_call/presentation/scheduled_break/exercise_active. Requires multiple fresh leisure observations spanning >=120s, fresh presence, no inhibitors. Counts stored Singapore date, max6 target5 gap3600; record_intervention(now=None, reason='prompt') persists, emergency cooldown separately. observation_only default; no hardware blocking in policy.
- exercise.ExerciseController: injected backend and camera; start(), stop(reason), status(). Must remain unavailable until backend verification proves emergency escape and actual input blocking. Separate watchdog process preferred, 180s hard timeout, >30s continuous absence following presence via monotonic clock. Camera failure/staleness releases. No Watch Mode reuse; never OS lock. Publish actual API in report. Do not activate a real lock during implementation.
- expansion_memory.record(kind, source, timestamp, confidence, data, key=None) -> bool; persistent idempotence, readable vault via existing conventions. operational queues stay structured. status() for user-visible explanation. Untrusted WhatsApp text is data.

Each worker may revise its local API with a precise completion report. Parent reconciles consumers after results. Desktop tests and daily trial remain explicitly unverified until performed on the installed session; default is observation-only.

## Parent integration

The bridge's existing Always Aware sidecar constructs the coordinator. Direct
Runner(Deps(...)) remains available for isolated legacy screen tests. Each poll
starts independent daemon jobs for WhatsApp, calendar, diagnostics, screen and
memory. A timeout retains the job; subsequent polls attach instead of replacing
it. Component outcomes are written to poll-status.json without message bodies,
chat names or native identities. Vision backoff and idle parking only affect the
screen component. The full coordinator continues on a maximum 180-second cadence.

The screen adapter records conservative activity decisions and never calls an
input blocker. Unknown call, presentation or scheduled-break state prevents
eligibility. Existing vision summaries supply visible-content evidence; app
names alone do not classify leisure. Exercise mode is reported as
observation_only by both voice and the status endpoint. An unavailable backend
cannot be enabled by changing a configuration boolean.

Enabling Always Aware explicitly discloses that opening WhatsApp chats marks
messages as read. Do not run live message ingestion as part of automated tests.

## Acceptance still requiring installed-session evidence

Fake hardware tests establish state transitions, not real desktop control.
Desktop-wide blocking, emergency shortcut delivery, external watchdog recovery,
camera unplug behavior, and an integrated active-day trial require a verified
backend and a supervised installed-session check. See EXERCISE-VERIFICATION.md.
Until those gates pass, the feature must not claim that it enforces breaks or
that a departure proves completed pull-ups.

## Calendar provider identity reference

Automatic writes now persist a deterministic provider identity before sending
an insertion, and reconcile an uncertain response by exact identity. This
follows the provider's supported caller-assigned event-ID field and its
identifier-conflict behavior; see Google's [Events: insert reference](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert)
and [Calendar API error handling](https://developers.google.com/workspace/calendar/api/guides/errors).

## Live integration constraints observed during implementation

The configured CDP connection had no WhatsApp page, so stable JID extraction
and busy-chat seven-day coverage are verified with fixtures only. The scanner
reports unavailable chat identities explicitly and does not substitute names.
Open the signed-in WhatsApp Web page in the configured browser before a live
read. Remember that opening chats marks them read.

The installed input session cannot cover all input devices with current
permissions. The controller's default backend stays inert and rejects arming;
a verified emergency callback, desktop blocking, and independent expiring
safety lease are all mandatory before any backend can acquire input. The
active-day enforcement trial is therefore outstanding.
