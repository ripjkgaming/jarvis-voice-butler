# Permissioned screen SDK session holder

This is a separately staged, registered screen host. `agent.py` routes only explicit `JARVIS_SCREEN_HOST=1` jobs to `screen_host.start_screen_host`. The normal five-feature entrypoint and frozen release stay untouched. Native tray Stop/Resume/End are wired to an authenticated private Unix transport; no model tool, webview command or SDK text stream exports physical resume. The parent owns activation and later device acceptance approval.

## Functional boundary

`ScreenSessionHolder` starts and closes in-repo `jarvis_rt` `AgentSession` engines. Each engine uses the same room with `delete_room_on_close=False`, video input off and `text_input=False` (remote text input off). Only final `hangup()` invokes the host's `end_call` callback. Unexpected SDK engine closure stops that generation and preserves the room.

`PhysicalScreenControls.dispatch(LocalControlEvent(...))` accepts a host-authenticated button/shortcut event through a required host verifier. Its default verifier refuses everything. The event ID is consumed before awaiting, expires after 30 seconds, and cannot be replayed. A source string, client boolean, microphone transcript, model argument, OCR or `lk.chat` message is not authentication. The test verifier deliberately uses a fake source; it must not be copied into a real host verifier.

Physical stop synchronously invalidates the generation, mutes input/output/transcription and revokes capture consent, then awaits FeatureSession, actual SDK activity/I/O/STT stream shutdown and owned provider close. Voice stop remains the existing FeatureSession speech-stop route; it does not authorize resume. Resume requires a fresh physical owner event and the owner's affirmative confirmation that private speech is quiet. Resume during active sensitive scope is refused; use physical stop first and wait for it to complete.

Resume retires the old engine, creates a fresh muted SDK session, Agent, chat context, STT/LLM/TTS providers and caption tail, checks authority/generation/expiry after startup, then enables the new input. Session, Agent and stateful speech/model provider reuse is refused. Previous user/assistant/tool chat context is refused. Old callbacks retained outside the SDK bus still fail their generation predicate. `GenerationEvents.on()` accepts synchronous observers only; detached work needs its own cancellation/ownership contract.

The SDK schedules `on_enter` without awaiting it. The holder waits for that hook within its startup budget and refuses speech created during startup, so a delayed greeting cannot overlap newly enabled input. These lifecycle states are separate from feature 2's action outcome types: `listening` proves the new SDK engine is ready and its input flag enabled, not verified device audibility, microphone routing or message delivery.

No capture consent transfers to the new FeatureSession. The host must separately configure fresh, explicit, expiring screen consent; physical resume alone permits neither enrollment nor capture. Caption/input quarantine from a private readback is not cleared on the old session. Resume creates a different session instead.

## Host interfaces and write ownership

The exact combined write ownership is recorded in `screen-host-extension-manifest.json`. In this separate stage, `src/agent.py`, the native main/tray registration, screen host/control transport/local STT wrapper and their tests are owned by feature 1. The original three-file adapter candidate remains separately recorded; the combined host candidate supersedes its unregistered status. Shared source and the frozen release are untouched.

Constructor hooks:

| Hook | Contract |
| --- | --- |
| `room` | Current call's connected room, retained throughout engine rollover. |
| `session_factory()` | Synchronously return a fresh `AgentSession`, with fresh approved providers; no capture/device effects in construction. |
| `agent_factory()` | Synchronously return a fresh SDK Agent with fresh context and static approved instructions. No startup greeting or private history copied from the old agent. |
| `bind(session, events)` | Synchronously install/return a new `FeatureSession`, bind a fresh private caption sink, then register ordinary observers through `events.on`. Do not load or inherit screen consent. |
| `room_options_factory(session)` | Return fresh SDK `RoomOptions`, including generation-owned audio output and transcription/caption chain. Holder overrides room deletion, remote text input and video input. |
| `owner_allowed()` | Current local owner predicate, including guest/watch/private-phone boundaries; must never derive authority from a transcript. |
| `end_call()` | Async host room teardown/delete callback, invoked once after final engine drain. Never invoke it from an engine's ordinary close observer. |
| `close_providers(session)` | Optional async cleanup for additional host-owned resources, after SDK streams and collected LLM/STT/TTS objects close. Must be idempotent if a failed cleanup is retried. |

Host wiring must register `holder.hangup` with job shutdown and call `await holder.revoke_authority()` immediately on guest/private/owner authority transitions. An authenticated physical control dispatcher calls `controls.dispatch(event)`; it is not exported to model tools. A failed drain leaves state `failed`, retains ownership and refuses replacement. Correct the cleanup failure and retry physical stop/final hangup rather than bypass it.

A failed drain may be retried before the room teardown callback has started. Once room teardown has been attempted, an error leaves its outcome unconfirmed and is not retried automatically; the host must inspect room state before deciding any further room effect.

The normal `my_agent` captures old session/features/assistant/caption objects in many closures. The separate screen mode bypasses those closures and rebuilds its own caption, transcript and echo observers through `GenerationEvents` for every SDK engine. Its screen-only Agent has no conversational model, tools, handoffs, camera checks or proactive jobs. This narrow mode makes bounded device acceptance possible without rewriting the normal assistant. Job shutdown and owner loss are registered directly with the host. Do not copy the full staged tree into production.

## Runtime compatibility

The holder runs on the in-repo `jarvis_rt` runtime (no LiveKit SDK). The former livekit-agents version guard and private partial-teardown workaround were removed with the LiveKit dependency. Start has a 20-second budget (`_deadline(20)` in `src/screen_session_holder.py`).

## Offline and device acceptance

Tests call real SDK `start`/`aclose` with fake RoomIO, a mock input iterator that produces no samples and a mock SDK streaming STT provider. They prove SDK activity and forward-input tasks, STT streams, provider objects, FeatureSession dispatch and partial initialization drain before replacement; cancellation during stop/hangup drains before returning cancellation. They exercise startup error/timeout, guest change during startup, expired physical events, old close events/callbacks, replay refusal, failed cleanup and final hangup once.

These tests do not prove real microphone routing, room transport delivery, device buffers, local Piper sound, real window geometry/OCR, foreground capture or physical button authenticity. The separate synthetic acceptance plan still requires parent approval. The staged host callbacks and native physical transport are implemented and registered. The isolated offline single-worker Tauri build completed successfully. Parent-approved device acceptance remains pending; no activation occurred. Keep all screen/native-only flags default off in the shipped application.

## Rollback

Stop/revoke and drain every holder, then final hangup only if the call is ending. Unset the screen host/native flags, then apply the exact combined rollback patch recorded in the 12-file manifest after baseline verification. The frozen release patch/manifest and existing screen consent policy are unchanged. No migration, permission grant, captured evidence, package install or live deployment is created by this stage.

## Registered native host configuration

- `JARVIS_SCREEN_HOST=1`: selects the staged screen-only worker entrypoint, initially no SDK input until an authenticated physical Resume.
- `JARVIS_SCREEN_CONTROL_SOCKET`: explicit absolute socket path inside an existing private owner directory (0700); no home lookup or socket-stealing/unlink fallback.
- `JARVIS_SCREEN_CONTROL_EXE`: explicit absolute owner executable file, executable and not group/world writable, pinned by device/inode. Linux SO_PEERCRED must match owner UID and the peer process executable; identity is rechecked after receiving the bounded command.
- `JARVIS_FEATURE_CONSENT_FILE`: independently approved explicit expiring consent. A new positive trusted SDK user screen request consults it; Resume neither reads it nor grants capture scope.
- `JARVIS_SCREEN_READING=1`: separate reviewed screen release switch; default off.
- `JARVIS_SCREEN_NATIVE_ONLY=1`: optional isolated acceptance shell; skips single-instance redirection, webview application commands, sidecar boot, normal hotkeys, state restoration and overlay geometry persistence. Tray contains only Stop/Resume-after-quiet/End/Quit. Normal native launch remains unchanged with this unset.

The client sends exactly one of `stop`, `resume_quiet`, `hangup`, newline terminated and write-half-closed. It sends no claimed identity, consent, text, file path or event ID. The server issues its own one-use event only after kernel verification and returns a bounded lifecycle receipt. Native menu handlers are not exported as CLI/webview functions. The OS owner account/native binary is the trust boundary; this does not protect against a compromised owner account or malicious code inside that approved executable.

`DrainingScreenSTT` reuses local Whisper but owns its decoder executor and waits for decoding/thread shutdown on cancellation and provider close. No decoder thread survives generation retirement. Whisper uses local_files_only=True; absent weights fail without downloading. Tests replace decoding with a blocked mock, use no audio samples and load no Whisper model.
