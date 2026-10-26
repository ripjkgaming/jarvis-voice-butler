# Goodnight detached bridge lifetime revision

Staged revision against the source-only installed Goodnight baseline. Default off. No revision apply, real arm/lock, flags, service reload, microphone/provider or compositor operation occurred.

## Functional ownership

Trusted final owner audio invokes a private internal handoff before acknowledging a pending plan. The bridge supervisor owns Controller/Wayland idle helper/registered task inventory/final admission fence independently of the call. It retains no AgentSession, microphone or speech object. Normal call close drains owned speech and dispatch threads, then records a content-free closed conversation receipt; the plan remains until lock/cancel/revocation/expiry. Ordinary idle hangup is no longer deferred by `Binding.armed()`; it returns False so voice lifetime is normal. New ordinary calls register before input/provider start, and fresh speech/activity postpones without destroying the plan or extending expiry. Closed receipts preserve final speech activity, even when a short utterance is missed between supervisor polls.

Status and cancel remain owner voice/tool/authenticated local bridge controls. Public HTTP/model tools cannot arm. The prior voice-owned Binding is no longer registered by agent `install`; only the detached binding is registered. Legacy class remains for regression/history; it owns no production plan. `goodnight_bridge.HOST` binds the supervisor only in bridge lifetime startup, and shutdown cancels/drains it. Source installation alone does not load this revision.

## Authentication and generation contract

Bridge startup takes an exclusive owner-private supervisor FD lease, generates a fresh random epoch/HMAC capability and starts DISARMED. No pending arm is restored from disk. Owner-only mode0600 private files and no-follow/owner/type/link/size checks protect internal messages. The trusted local SDK code signs epoch, unique request ID, operation, immutable UID/desktop identity, call ID, hashed trusted final-event identity, unique arm generation, original callback CLOCK_BOOTTIME issued time and issued+3600 expiry. Dispatch never restamps the callback. Bridge independently verifies HMAC, epoch, exact schema/identities, current owner/session, <=10sec handoff age and fresh allowed call coverage; arm transfer and task admission share the fence. Repeated arm preserves first expiry. Same request is idempotent; bounded replay ledger refuses overflow. Bridge restart rotates capability, so delayed/replayed old handoff cannot revive a plan.

Capability is for trusted local same-UID service code, never model/body/tool arguments or prompts. This does not isolate against malicious arbitrary same-UID executable code that can read private files. No such process isolation/speaker identity guarantee is claimed. Nearby voice/media limits of existing physical-owner ingress remain.

Guest entry writes a durable revocation marker BEFORE mode publication or a held admission fence can block it. Bridge authority checks and voice physical-owner checks fail closed on guest/lock/owner uncertainty. Restoring owner mode does not clear the marker or revive the plan; an explicit supervisor restart starts a new disarmed epoch, followed by a fresh trusted arm. Closing an ordinary call does not treat FeatureSession's normal `_closed` flag as a guest transition.

## Cancellation and final request

Voice/tool cancellation does NOT fetch status through serialized inbox first. It reads the protected epoch-matched local status only to target an exact generation, then immediately publishes a signed urgent cancel mailbox before waiting for acknowledgement. A stale target is refused; it never silently broadens to a newer plan. Urgent cancellation is processed at supervisor loop entry and under final fence after slow probes, independent of handoff ledger guard. Direct HTTP cancel updates a cancellation watermark and Controller under its short mutex without waiting for the admission fence. Original handoffs issued before cancellation cannot become a later revived arm. Delayed older-generation cancel cannot cancel a newer plan. Unconfirmed delivery stays explicitly unconfirmed.

All slow SessionLock identity preparation and final input/task/conversation probes occur BEFORE commit. Core commit rechecks original expiry, poll gap, continuity and current token at the fixed request boundary, then consumes generation and invokes prepared loginctl request/read-back. Input/speech changes in final probe postpone, not lock-unverified consumption. Cancel before commit wins; after request starts cannot undo it. Command completion is not proof of lock: KDE GetActive true remains mandatory, otherwise lock-unverified/no replay. No extra slow probe runs after consuming the generation.

## Scope and fail-closed limits

Prior registered native task/job/Assigner local Codex/build PID inventory and executor leases are unchanged. Arbitrary unregistered Codex desktop/IDE/CLI/remote work, surviving descendants and previously pruned build registrations remain outside coverage; no claim of all coding idle. User scope clarification is independent of this lifetime repair.

Fresh loaded SDK rows and content-free activity timestamps are required. Stale/corrupt/missing activity, invalid/nonfinite/bool timestamps, unrecognized producer state and overflow hold. Known fully drained closed rows do not require heartbeats but preserve last activity. Stale open rows after a crashed voice producer hold; never delete them to manufacture idle. Closed records/ledger are bounded at4096; overflow holds and requires verified retirement/cleanup or a future reviewed compaction implementation. Metadata does not restore an arm. Real physical input/compositor support remains an acceptance requirement.

## Proposed writes and operation

Modify bridge.py startup/shutdown, goodnight_bridge.py supervisor status/cancel dispatch, goodnight_host.py registered install/control routing, goodnight_runtime.py identity preparation, watch_mode.py durable guest revocation hook. Add goodnight_supervisor.py, goodnight_detached.py, focused detached tests and this doc. No agent.py rewrite, service/environment changes or monitoring/voice/provider changes. Exact baseline/candidate hashes and supplemental patch/rollback are in manifest.

Later coordinated authorized enable: common JARVIS_HOME; JARVIS_GOODNIGHT_V2=1 for bridge and all fenced voice/task/Assigner/registry producers; JARVIS_LOCAL=1, active owner Wayland XDG_SESSION_ID. Bridge and all voice/producer runtimes must load together after retiring old legacy watcher callbacks. Restart revokes; it never resumes an arm. Phrases remain goodnight/good night/goodnight Jarvis/hey Jarvis goodnight, goodnight status and cancel goodnight (schedule variants retained). No actual reload or enable authorized in this revision.

## Acceptance and rollback

Offline mock tests exercise normal trusted arm→SDKclose/drain→ten idle minutes→mock verified lock, no retained session, ordinary new activity/tasks, cancel while final fence blocked via actual SDK dispatch, immutable callback expiry, final slow probe expiry/speech, replay/tamper/restart/guest revocation, stale/malformed rows and closed speech activity. Test speech is fake; no real models/providers/idle helper/lock ran. Actual device/compositor/provider/migration/lock read-back require separately coordinated acceptance after external scope resolution.

Before rollback under deployment authorization: cancel/drain supervisor and participating producers. Supplemental rollback restores this installed baseline, which still lacks detached lifetime, so leave disabled. It does not restore unsafe legacy five-minute watcher. Preserve all unrelated protected source hashes. Applying this revision without coordinated reload does not change currently loaded behavior.
