# Goodnight v2: registered-work scope

This is a staged, default-off implementation. No arm, lock, service restart, microphone/capture or real model action was performed. Set JARVIS_GOODNIGHT_V2=1 only in a coordinated later deployment of every participating voice/bridge/Assigner process. JARVIS_LOCAL=1 and a matching active owner Wayland XDG_SESSION_ID are required. Monitoring v3 flags, provider fixes, bedtime calls and screen activation are unchanged.

## Functional path

The installed AgentSession final audio callback owns exact `goodnight`, `good night`, `goodnight Jarvis` and `hey Jarvis, goodnight` commands. It uses the existing local owner/guest boundary, echo guard and sensitive-input quarantine. SDK item_id or created_at deduplicates finals. ChatMessage/history/model/tool bodies never arm. The text hook consumes only a command already observed in trusted audio; realtime tool dispatch refuses immediate effects/hangup generated from that command, allowing status/cancel only until a new trusted utterance. This inherits the local physical-session trust boundary, not speaker biometrics; nearby voices/media are not guaranteed distinguishable.

`goodnight status` and `cancel goodnight` work through this ingress and owner tools. Authenticated local bridge GET /goodnight/status and POST /goodnight/cancel provide status/cancel only. Cross-process status is explicitly last-reported, with freshness and a live election lease check. Cross-process cancellation is a request against the exact host ID, not proof of settlement. No HTTP or model arm endpoint exists. Once the fixed lock request commits, cancel reports that it cannot undo it, and another arm is refused until completion.

The elected host remains in the current voice session. Idle hangup and optional presence check-ins are deferred while armed; explicit session close cancels and drains the host. This MVP does not leave an autonomous watcher after the call ends. The initial farewell keeps the quiet call open for the ten-minute wait. One-hour CLOCK_BOOTTIME expiry includes suspend. No arm is restored from status metadata. New sessions advance an authority epoch; old requests revoke, old hosts release election, and the latest session retries election. Other still-open conversations remain tracked. Retired SDK generations block locking outright until their session closes; a new host election cannot silently exclude them.

## Exact supported coverage

* Native Jarvis task JSON: queued/planning/running/waiting/cancelling/unknown holds; terminal records plus executor-lease release are needed.
* Legacy Jarvis jobs JSON: nonterminal/unknown or missing finite finished time holds. Registration failure now prevents admitted work.
* Assigner jobs.sqlite3: pending through reporting/cancelling/remote uncertainty holds. Service's actual local Codex executor is leased through runtime cleanup and final reporting, even when a done row appears early. No new model invocation is used for idle checks.
* Canonical agent-tasks registry: exact owned adapter_work settlement, never observation/click/launch proof.
* Registered build-monitor PIDs: exact boot/UID/PID/start identity; detach or settled observation alone does not mean the PID exited. Build-monitor settlements are retained rather than pruned after twenty rows. This proves only registered named process settlement, not surviving descendants or remote work.
* Voice sessions loaded with this adapter: fresh SDK user/agent state, pending owned speech, tools and last activity; stale/malformed/unknown conversations hold. Every timestamp and idle input must be a finite numeric value; booleans and NaN/infinity are unknown.

An absent unused store is clear within this scope because supported creation/admission participates in the fence. A present corrupt, unsafe, incompatible or over-4096-row store holds. No CPU heuristic, recent-file mtime, transcript scan, proc-name scan, or personal file content establishes coding idleness.

Arbitrary unregistered Codex desktop tasks, Claude/opencode/IDE/CLI processes, descendants and remote jobs are OUTSIDE this scope. Jarvis has no authoritative executor API for these. The coordinator's Codex app thread-list tools are not an unattended capability exposed to Jarvis. Previously pruned build registrations also cannot be reconstructed. Do not describe this as proving all coding work idle. A later external executor adapter must register before dispatch and participate in the same fence; it must never be a manual `external_clear=True` override. Activation requires acceptance of this narrower scope or a separately implemented authoritative external adapter.

## Idle and final commit

Require ten minutes since arm, ten continuous minutes of confirmed keyboard/mouse inactivity and ten minutes of known quiet conversation/activity. Real input resets; unknown input/conversation or polling/probe gap over thirty seconds resets continuity. Active tasks hold without consuming the request. Wayland ext_idle_notifier helper starts only on a trusted successful arm; each bounded sequence request includes compositor roundtrip. Unsupported/failed/timeout observations are unknown, with no elapsed-time fallback.

Cross-process admission flock covers native task commits, legacy/canonical registration and Assigner submit/update. Live FD leases span native execution, Assigner consumer cleanup, all voice tools and pending farewell/status speech. No admission lock is held across awaits/model work. Thread mutex plus flock wait is bounded to two seconds. Executor leases close in finally even if metadata cleanup fails. All registered admissions linearize before or after the final request; a job admitted before it is visible to the final inventory. Uninstrumented old producers invalidate deployment coverage, so coordinated loading is mandatory.

Final fence acquisition occurs outside the controller mutex. Under the fence, fresh probes and a final content-free activity read precede rechecking token, expiry, authority/session, quiet continuity and work. SDK activity/revocation happens before waiting for publication. A separate tiny atomic timestamp signal exposes other call activity even while the final fence is held. The commit point consumes the arm and records locking; cancellation before commit wins, after commit cannot undo a request already started. Error/uncertain request never retries.

Only fixed loginctl lock-session for the validated explicit local graphical session is allowed. KDE ScreenSaver GetActive `b true` is required to report locked. Command exit is request evidence only; missing read-back is lock-unverified. SDK speech drain is lifecycle evidence, not proof that a person heard it. These lifecycle states are separate from ActionOutcome.

## Write ownership and interfaces

Coordinator payload manifest lists exact paths/hashes. Author frozen core/ingress was transplanted, then core finalizer and host integration were repaired in the isolated candidate. Shared proposed hunks: agent.py, bridge.py, agent_tasks.py, jobs.py, assigner_headless.py, legacy goodnight.py tombstone and test_goodnight.py replacement. New modules/tests/docs are additive. Tests/conftest.py in the isolated candidate is an offline harness ONLY and is excluded from the source patch.

Fence(): short synchronous admission context; Fence.work(): executor-lifetime lease; admission/execution/async_execution decorators preserve original behavior when the flag is off. Binding installs SDK observers, owns tracked/drained worker tasks and same-session election; control() is status/cancel only. Host's finalizer(expected, commit) acquires the fence before calling commit(fresh_evidence, fixed_request). The reusable author goodnight_ingress module remains separate; registered agent wiring uses goodnight_host.Binding.

## Offline evidence and remaining acceptance

See coordinator handoff and XML for final test totals. Focused tests cover owner intent/dedup, status/cancel, expiry, continuity, late cancellation/rearm, cross-process fence, actual legacy registration, executor lease, new-session election, cleanup failure, other-call activity and lock read-back. Existing task/Assigner/registry tests run with synthetic state and blocked subprocess boundaries. Two executor-related legacy tests are excluded after one sandbox run stalled at the existing async dispatch boundary; this is not a claim of full regression/runtime validation.

No real Wayland idle helper, locked-session request, live Gemini STT events, physical voice discrimination or actual microphone/provider route was exercised. These need separately approved acceptance. Do not enable by merely applying source.

## Migration and safe rollback

The old default-on five-minute watcher is disabled by the tombstone; agent legacy hooks are removed. Before runtime enable, explicitly invalidate any existing legacy goodnight.json token and verify already-loaded watcher code cannot execute, through a coordinated bounded migration. Replacing source alone cannot revoke a loaded watcher. No token was touched during this work.

Rollback: first cancel/drain the new host and stop participating runtimes under authorization; restore reviewed old wiring/producer files and remove additive modules using the SAFE rollback patch. Keep the goodnight.py tombstone: do not restore or reactivate the unsafe elapsed-time/CPU-based watcher. Retired conversation metadata from a crashed process conservatively holds; cleanup requires verified process/session retirement, never wholesale deletion to manufacture idle. Exact rollback hash checks must be against the manifest, preserving monitoring/voice/screen unrelated changes.
