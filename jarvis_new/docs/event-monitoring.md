# Public event monitoring MVP

Implemented as an additive `src/event_monitor/` package. No visual changes,
service units, enabled worker, real monitors, calls, microphones or messages.
The collection worker is independent of voice session lifetime. Runtime
integration belongs to the existing release coordinator and is still pending.

## What works

SQLite persists explicit owner/scope/source/event/filter/output/expiry definitions,
source high-water cursors, deterministic evidence identities, outbox states,
fixed coalesced batches, activation requests and model quota/backoff. Atomic
insert and cursor/outbox commit prevent duplicate observations. Reads replay
bounded source windows so out-of-order and late events do not vanish behind a
cursor. Cancel/dispatch serialize through SQLite write transactions; cancellation
stops pending work, never the observed source or a session already started.

`Worker.collect()` persists activation demand even when no session host is
running. `Store.dispatch()` is the policy-controlled delivery/start interface.
An authenticated host must inject a unique active-owner session provider, a
bounded idempotent delivery sink, and an optional session starter. The default
starter is absent and activation is denied. No generic proactive engine polling
or phone `offer_signal()` path is used: that path can offer events to phone calls.

Existing `jobs.Registry` owns observation lifecycle (`event-monitor` owner,
`monitor_observation` scope); its cancellation requests settle these monitors.
Existing proactive `live_context` and `decide` apply DnD, focus, natural-pause and
speech-cap rules before starting or delivering. A hard quiet-hours check additionally
holds events even when the legacy engine's idle-gated quiet check would permit
speech. There is no critical-urgency override. Owner identity, scope, authenticated
session, call/watch state and a second session check gate delivery.

The explicit per-monitor alert budget counts coalesced deliveries. Events have
bounded freshness TTL; monitors require an explicit expiry within seven days.
There are at most 100 active monitors and ten due monitors per worker pass.
The singleton worker polls no more often than once per minute; OpenF1's two GETs
per monitor stay below its documented 30/minute free limit. Feed responses are
HTTPS host-allowlisted, redirect-refused, eight-second bounded, capped at 512 KiB,
and limited to 200 normalized events. Large historical windows are refused rather
than silently truncated; narrow the requested window. SQLite serializes races;
network requests occur outside the store transaction.

## Free F1 scope and current sources (verified 2026-10-10)

- [Jolpica docs](https://api.jolpi.ca/docs/) and its
  [official race endpoint contract](https://github.com/jolpica/jolpica-f1/blob/main/docs/endpoints/races.md):
  numeric season/round schedules and results, version-deduplicated. Initial available
  data is an explicit snapshot update. These are not dependable live incident feeds.
- [OpenF1 reference](https://openf1.org/docs/): historical race-control replay only;
  numeric session key and explicit UTC epoch start/end, at most one hour. Both the
  requested window and actual session end must precede now by over 30 minutes.
  No `latest`, credentials or live subscription are accepted. Historical incidents
  are always labeled `historical-replay`, not current incidents.
- [OpenF1 access terms](https://openf1.org/): historical data is free, live sponsorship
  is currently €9.90/month. No subscription was purchased or enabled.
- [OpenRouter free router](https://openrouter.ai/openrouter/free),
  [free variants](https://openrouter.ai/docs/guides/routing/model-variants/free) and
  [limits](https://openrouter.ai/docs/api/reference/limits): optional triage is OFF
  by default (`JARVIS_MONITOR_TRIAGE=0`). It uses only `openrouter/free` through the
  existing `openrouter_chat._one_model` client with its existing authorized key
  resolver. It deliberately avoids the environment-overridable `chat_reply`
  fallback chain. No silent paid fallback, tools or dispatch authority. Persistent
  quotas are 50/day and 20/minute; one eight-second attempt, 60-second error backoff.
  Missing credentials, quota, invalid JSON/schema or unavailable models retain
  deterministic monitoring. No credential was inspected or model contacted by tests.

Only newly inserted evidence batches reach triage. Prompt fields contain bounded
public adapter facts and evidence IDs, never session transcripts, documents,
owner/session metadata or secrets. Model output must be exactly `{"ids":[...]}`,
unique IDs drawn from the current batch. The model can only suppress observations;
it cannot invent facts, write spoken text, change outputs, start sessions or call
other tools. Every feed string is untrusted evidence. A delivery sink should use
plain speech of validated facts; never treat feed text as instructions.

## Basic control API / staged voice tools

`MonitorAPI.execute(operation, body)` supports create/list/status/stop. The host
supplies authenticated owner identity; body fields cannot grant authority. The
default guard reuses `require_local`, `build_monitor.owner_allowed` and the
current `phone_session` approval ticket/epoch checks. Guest, private-call and
revoked authority fail closed. Cross-owner IDs are inaccessible. An HTTP host
must additionally validate its normal auth token and loopback peer before calling
this API; do not mount it as an unauthenticated endpoint.

Creation body requires every field below (example is a schema illustration;
no actual monitor was created):

```json
{
  "source": "jolpica", "event": "results",
  "filters": {"season": 2025, "round": 1},
  "expires": 1800000600, "outputs": ["session", "activate"],
  "session_scope": "desktop", "request_id": "owner-turn-unique-id",
  "budget": 3, "ttl": 300
}
```

`outputs:["status"]` stores evidence only; `["session"]` permits delivery to an
existing owner session; `["session","activate"]` additionally requests activation.
These outputs never authorize ringing phones, WhatsApp, external sends, recording,
trading, or microphone joining. There is no endless/default monitor.
`monitor_create/list/status/stop` are in `event_monitor.voice.MonitorTools`.
The basic create tool deliberately requires a current owner `monitor {JSON}`
command matching the definition exactly; stop requires `stop monitor <exact ID>`.
This is a narrow first-function interface, not a polished natural-language parser.
API status includes event/outbox counts, collection backoff/error type, registry ID
and durable activation requests. No model-provided proof is accepted as a user turn.

## Integration gap and coordinator handoff

The worker and synthetic start interface are runnable. Actual unattended Jarvis
activation is **not integrated**. Existing `bridge.py` room mint/dispatch is tied to
mobile voice joining; `screen_session_holder.py` startup uses a voice session.
Neither has been verified as a safe unattended, no-microphone activation adapter.
Do not route pending requests through these paths just to claim activation works.
Without an approved adapter, requests remain durable and visible until canceled
or their TTL/monitor expiry elapses. Existing-session delivery alone is not full
completion of the user's activation requirement.

Coordinator integration sequence, to stage against the current dirty manifest:

1. Instantiate `MonitorAPI(Store(private_home / 'event_monitor/monitors.sqlite'))`
   and add `MonitorTools(api).tools` to the owner SystemAgent tool list only after
   its existing permission/privacy gates. Do not add to guest tools. Preserve the
   shared `agent.py` edits. The wrapper contract follows the in-repo
   `jarvis_rt` function-tool decorator (`src/jarvis_rt/tools.py`).
2. Mount create/list/status/stop behind existing bridge authorization/peer validation;
   owner identity is server-derived. Return concise validation errors and recheck
   authority before disclosure. Keep all existing guest/privacy checks.
3. Add a dispatcher to the existing host scheduler. Inject a provider returning
   one `Session(owner, scope, identity, authorized, call_active, watch_active)` or
   None. `identity` must change when that session's authority epoch changes. Unknown
   call/watch/privacy state must refuse activation. The activation guard must
   revalidate persisted per-monitor output consent and live owner privacy/call/watch
   state; worker collection by itself grants no owner-session authority.
4. Starter contract: `start_session(request_key, owner, scope)` must be bounded,
   durably idempotent by key, refuse a second active owner session, and publish its
   returned authenticated session to the provider. It must not ring, capture audio,
   join a microphone, or promote guests. Leave `live_effects=False` and no starter
   until this safe internal adapter exists and its coordinated review is complete.
5. Sink contract: `sink(request_key, session, signal) -> bool` acknowledges durable
   acceptance, checks current normal permissions and authority immediately at the
   side-effect boundary, and maintains idempotent receipts. It must not reenter the
   monitor SQLite store (dispatch holds its write lock). Update the existing
   proactive delivered/speech counters through the coordinator's normal mechanism.
   Bounded start/sink calls must return promptly; an async host should wrap this
   synchronous interface in its existing scheduler executor.

SQLite cannot make external session creation/speech exactly once without the
starter/sink honoring the durable key. A crash after external success and before
commit retries that same fixed key. This is a documented host integration contract,
not a claim that raw `session.say()` is durably idempotent.

## Verification and safe activation/rollback

Run the synthetic suite from repository root:

```sh
PYTHONPATH=src UV_CACHE_DIR=/tmp/jarvis-monitor-uv uv run --offline --no-sync pytest -q tests/test_event_monitor.py
uv run --offline --no-sync ruff check src/event_monitor tests/test_event_monitor.py
```

Tests use only temporary SQLite/registry files, injected HTTP/model/session clients
and synthetic evidence. No network, paid model, actual session, user data writes,
message, trade, audio recording or phone call. Coverage includes full adapter fixture
collect/triage/dispatch, reconnect/restart, out-of-order/duplicate data, invalid model
JSON/schema, persistent quotas, expiry/quiet/DnD, cancellation/collection and delivery
races, fixed retry keys, cross-owner boundaries, voice command matching, registry
cancellation, historical-only filtering and synthetic activation with default denial.

After coordinated review and notice to the user, manually start collection in the
existing private Jarvis home (the command itself does not create monitors):

```sh
PYTHONPATH=src uv run --offline --no-sync python -m event_monitor --home "$JARVIS_HOME"
```

`--once` runs one bounded pass. SIGTERM/SIGINT stops collection; no service unit was
installed. Do not run this against the real home during testing. Collection needs
no voice-hub connection. Real dispatch needs the completed host integration above.

Rollback: stop the manually started worker via its recorded PID, disable the
coordinator's dispatcher/start flag, remove its staged owner-tool/API registrations,
and retain `event_monitor/monitors.sqlite` and `job_registry` for review/recovery.
Do not delete or migrate unrelated existing state, restart other services, or erase
the dirty checkout. Pending observations become stale according to TTL; restarting
collection recovers definitions/cursors/dedup. There are no persistent credential
grants or paid-provider settings to revoke.
