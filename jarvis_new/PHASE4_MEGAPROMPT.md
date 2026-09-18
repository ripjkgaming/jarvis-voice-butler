# PHASE 4 MEGAPROMPT — paste everything below the line to a fresh AI

You are taking over Phase 4 (overlay polish + release hygiene) of the
Jarvis PC app. You have zero context: everything you need is below.
Read it all before touching anything.

---

## 1. What Jarvis is

Jarvis is an offline-first voice butler for Linux (Nobara/Fedora, KDE
Wayland). The user says "hey Jarvis" (offline wake word), talks, and a
voice agent executes tools on the laptop: opening apps/websites, system
control, WhatsApp reading (via a locally bridged chat client), inbox,
device control, web research, screenshots/OCR, and more. A HUD overlay
shows live tool activity. Design values: local-first (no cloud except AI
inference + the user's own keys), fail-soft telemetry, destructive actions
confirm-gated.

## 2. Architecture (5 processes, must all run)

Tauri shell (Rust) supervises 4 sidecars in strict start order:

1. `livekit-server` (vendored binary at `~/.jarvis/bin/livekit-server`,
   config `~/.jarvis/livekit.yaml`, serves `ws://127.0.0.1:7880`) —
   open-source WebRTC server run LOCALLY. There is no LiveKit Cloud in
   this build; rooms/tokens/data-channel all work against the local
   server. Never "migrate to Cloud" anything.
2. Agent worker: `jarvis_new/.venv/bin/python jarvis_new/src/agent.py
   start` (Python 3.14 venv). LiveKit `AgentServer` + `AgentSession`.
   Voice pipelines (`$JARVIS_PIPELINE`): `realtime` (default, Gemini Live
   direct on the owner's AI Studio key), `direct` (cloud-free: local
   Silero VAD + local faster-whisper STT in `src/local_stt.py` +
   Gemini-direct LLM + Piper TTS), `local` (legacy, needs LiveKit Cloud
   Inference — BROKEN on a local server by design, do not try to fix it).
3. Wake listener: `jarvis_new/.venv-wake/bin/python
   jarvis_new/src/wake_client.py` (separate Python 3.11 venv —
   openwakeword requires <=3.11). Offline "hey Jarvis" detection, plays a
   Piper "Yes, Sir." ack, mints a LiveKit token, pumps mic audio into the
   room, plays agent audio on speakers.
4. Bridge: `jarvis_new/.venv/bin/python jarvis_new/src/bridge.py`
   (stdlib only). Loopback HTTP API on `127.0.0.1:4317` (env
   `JARVIS_BRIDGE_PORT`, optional `JARVIS_BRIDGE_TOKEN` Bearer auth):
   `GET /health /status /actions?limit= /sys /config`, `POST /mic`,
   `GET /mic`. All JSON, presence-flags only, never secret values.
5. HUD webview: static Next.js export served from `jarvis_new/shell/ui/`
   (built from `jarvis_new/frontend/` via `pnpm build`, output `out/`,
   copied to `shell/ui/`). No Next.js server at runtime.

The Tauri shell (`jarvis_new/shell/`, Rust, Tauri v2) owns: boot order
with readiness gates, the contract env, tray icon, global hotkey
(Super+J, fallback Ctrl+Alt+J), single-instance CLI
(`jarvis toggle|talk|mute|unmute|quit|diagnostics`), autostart, and two
frontend invoke commands (`app_config`, `mint_token`).

## 3. Repo layout (repo root = jarvis-voice-butler/, code = jarvis_new/)

- `jarvis_new/src/agent.py` — worker entry (`cli.run_app(server)`),
  pipelines, handoffs (main router ↔ ResearchAgent ↔ SystemAgent).
- `jarvis_new/src/wake_client.py` — hotword loop, room join, mic pump.
- `jarvis_new/src/local_voice.py` — Piper TTS LiveKit plugin.
- `jarvis_new/src/local_stt.py` — faster-whisper STT plugin.
- `jarvis_new/src/bridge.py` — loopback control plane (stdlib).
- `jarvis_new/src/mint_token.py` — participant-token minter used by the
  shell's `mint_token` invoke command (livekit Python pkg, 15s timeout).
- `jarvis_new/src/intent/` — deterministic intent resolver + Needle 2
  router (NOT on the live voice path; do not wire it into the session).
- `jarvis_new/src/system/`, `src/tools.py`, `src/browser.py`,
  `src/prompts.py`, `src/hud_events.py`, `src/context/`,
  `src/proactive/` — tools and agent support. Read-only for you.
- `jarvis_new/shell/` — Tauri app. `src-tauri/src/`: `main.rs`
  (wiring), `manager.rs` (supervision), `env_cfg.rs` (env + dotenv),
  `health.rs` (probes), `tray.rs` (menu), `hotkey.rs`, `commands.rs`
  (invoke). `src-tauri/tauri.conf.json` (window label `overlay`, tray id
  `jarvis-tray` — NEVER rename), `capabilities/default.json`,
  `shell/ui/` (committed built export, ~18MB — rebuild via `pnpm build`
  in `frontend/`, never hand-edit).
- `jarvis_new/frontend/` — Next.js 15 + React 19 + livekit-client.
  Node pinned via `.npmrc` (`use-node-version=24.21.0`); system node is
  v22 — always build with the pinned toolchain. `lib/tauri.ts`
  (invoke wrappers, `__TAURI_INTERNALS__` detection),
  `lib/bridge.ts` (bridge fetchers). No `app/api/*` routes exist (they
  cannot statically export — never re-add server routes).
- `jarvis_new/src/wizard/` + `jarvis_new/scripts/` — first-run wizard
  (stdlib, pre-venv) and installer scripts. Read-only for you unless a
  Phase 4 task explicitly names them.
- `jarvis_new/tests/` — pytest suite. `shell/src-tauri` — `cargo test`.
- Docs: `DESKTOP_BUILD_PLAN.md` (Phase 4 section = your full spec),
  `AGENT_COMMS.txt` (coordination log — protocol below).

## 4. Environment contract (do not break)

- Shell sets for sidecars: `JARVIS_LOCAL=1` (every system tool refuses
  without it), `PYTHONPATH=<repo>/src`, `LIVEKIT_URL=ws://127.0.0.1:7880`,
  `JARVIS_HOME` (default `~/.jarvis`), `AGENT_NAME` (default `my-agent`).
  Shell loads `<repo>/.env.local` + `<repo>/frontend/.env.local` itself
  (never overriding real env, never logging values).
- Secrets live ONLY in those two ignored files + `~/.jarvis/livekit.yaml`
  (0600). Never print, log, commit, or test-fixture real secret values.
  `.gitignore` excludes: `.env*`, `.venv*`, `shell/src-tauri/target/`
  (6.3GB), `frontend/{node_modules,.next,out}`, HF caches.
- Audio: PipeWire, mic/speakers via sounddevice. Display: Wayland
  (global hotkeys depend on the compositor portal — KWin-shortcut
  fallback documented in code). System tools assume binaries on PATH
  (spectacle, tesseract, pactl, wmctrl, wtype, fd-find, brave...).

## 5. Tool commands (use exactly these)

- Python: `uv run pytest tests/<file> -q`, `uv run ruff check <paths>`,
  `uv run ruff format <paths>` — always from `jarvis_new/`.
- Rust: `cargo test`, `cargo clippy --all-targets`, `cargo fmt`,
  `cargo tauri dev` (needs frontend `pnpm dev` on :4000 first),
  `cargo tauri build` — always from `jarvis_new/shell/src-tauri/`
  (dev) or `jarvis_new/shell/` (tauri CLI). Rust 1.98 via rustup,
  tauri-cli 2.11 at `~/.cargo/bin`.
- Frontend: `pnpm build` in `jarvis_new/frontend/`, then
  `cp -r frontend/out/. shell/ui/` to re-sync the committed export.
- Wizard: `uv run python -m wizard check` from `jarvis_new/` (NOT from
  `src/`). Green board = sysdeps/livekit/venvs/models/keys/audio/gate.
- NEVER `sudo` anything yourself; NEVER commit or push (owner pushes);
  NEVER launch the GUI without logging it first (first run writes a real
  autostart entry — coordinate in the log).

## 6. Coordination protocol (AGENT_COMMS.txt, append-only)

- Before touching any file outside §7 areas, append a CLAIM line:
  `[<UTC timestamp>] [phase4] CLAIM <paths> — <reason>`.
- Log DONE per slice with test output. Never delete/rewrite others'
  lines. The frozen contracts (§4 + window/tray IDs + bridge field
  names) change only with a logged line + owner approval.

## 7. Your work, in order (each slice shippable; TDD everything)

### 4.1 Overlay behaviors (2–3 days)
Toggle is wired (`tray.rs`, `hotkey.rs`) — verify against the real
webview, not assumptions: focus-out→hide via
`WindowEvent::Focused(false)`; click-through idle via
`set_ignore_cursor_events(true)` off while interacting; geometry persist
to `~/.jarvis/overlay.json` on move/resize + restore on boot (new small
module `shell/src-tauri/src/overlay.rs` + tests); command-field autofocus
on show (frontend listens for the `jarvis-toggle` event — check
`frontend/components/hud/command-field.tsx` first). Accept: Super+J →
focused overlay <200ms; blur/Esc hides; position survives restart.

### 4.2 Mic mute — the last IPC gap (3–4 days)
Implement exactly this design: (a) `src/wake_client.py`: unix-socket
listener on `$JARVIS_HOME/wake.sock` (stdlib `socket`, daemon thread):
`{"mute": bool}` pauses `_pump_mic` frame capture (room stays joined),
`{"status": true}` replies `{muted, threshold, in_call}`. Handle a stale
socket file (unlink if unconnectable). (b) `src/bridge.py`: `POST /mic`
`{"muted": bool}` proxy with 2s socket timeout + `GET /mic` status, same
Bearer gate, fail-soft `{"ok": false}` when the listener is absent; tests
in `tests/test_bridge.py` with a stub socket (no mic hardware needed).
(c) Shell `commands.rs`: `set_mic_muted(bool)` + `mic_status()` invoke
pair (timeout, `Result<_, String>`); tray Mute checkbox replaces the
Talk-menu placeholder; `jarvis mute|unmute` single-instance verbs in
`main.rs`. (d) HUD: mic button calls the invoke pair, grey muted orb
(extend `frontend/hooks/hud/use-jarvis-state.ts`). Accept: tray mute
silences hotword AND in-call mic; unmute restores both; shell re-asserts
muted state when a sidecar reports Ready.

### 4.3 Updater (2 days + deferred signing)
Add `tauri-plugin-updater` to `shell/src-tauri/Cargo.toml` (NOT currently
a dependency — verify first), wire `tauri.conf.json` updater section with
a dev-feed placeholder URL. v1 = checks only ("update available" tray
note), NO auto-install, NO signing (GPG for rpm / AppImage signatures
explicitly deferred — document, do not half-implement). Accept: staged
newer version reported; `~/.jarvis/wizard-state.json` survives updates.

### 4.4 Logs, rotation, diagnostics (2 days)
`manager.rs`: 10MB×3 rotation per sidecar log in `~/.jarvis/logs/`
(std-only rolling writer, no new deps). `actions.log`: 5MB cap on append
(spill to `actions.log.1`) — find the append site via
`src/system/__init__.py::log_action`. New `jarvis diagnostics` CLI verb:
tarball with bridge `/status`+`/config`, last 200 lines per sidecar log,
wizard `check` output, versions (shell/sidecars/livekit-server/frontend
build id); TEST asserts zero secret patterns in the bundle. Crash path:
sidecar Down past restart budget → tray red + desktop notification
(replace the silent eprintln in the watchdog). Accept: 30-day simulated
volume <50MB; secrets test green.

### 4.5 Egress lockdown (1 day, AFTER stability only)
Default-deny egress in the vendored `livekit.yaml` via
`src/wizard/livekit.py` (allowlist: `generativelanguage.googleapis.com`
+ Cloud Inference hosts only if the legacy `local` pipeline is ever
re-enabled; registries off), plus wizard `--open-egress` escape hatch
with a loud warning. Accept: realtime + direct calls work locked;
outbound `curl example.com` from a tool context fails.

### 4.6 Acceptance + carryover
Still open (owner-side, report readiness, do NOT attempt): fresh-Nobara-VM
rehearsal (needs owner sudo+GUI). Decide brave-flatpak: native
`/usr/bin/brave-browser` already covers `src/browser.py` — recommend
dropping `com.brave.Browser` from `scripts/system-deps.sh` + wizard
`deps.py` catalog unless you find a real gap; log the decision.

## 8. Verification checklist (run per slice, paste output in your DONE)

```
cargo test                          # 14+ passing, 0 failed (shell/src-tauri)
cargo clippy --all-targets          # 0 warnings
cargo fmt --check                   # clean
uv run pytest tests/test_bridge.py tests/test_wizard.py tests/test_local_stt.py -q
uv run ruff check src/bridge.py src/wake_client.py src/wizard/   # + edited files
uv run python -m wizard check       # all green (gate needs shell running)
```

## 9. Current verified state (your starting line — do not regress)

Shell 14/14 + clippy/fmt clean; `pnpm build` green with `shell/ui`
byte-identical; wizard 32/32; live boot proven (manager→4 sidecars→agent
registered→bridge all-true→wizard 7/7→tray in KDE); E2E room-join proven
with minted token. Pushed to `ripjkgaming/jarvis-voice-butler` `main`.

## 10. Report back

Per slice: (a) what shipped + files changed, (b) full pasted test output,
(c) anything needing owner sudo/GUI. Final: one summary + DONE lines in
AGENT_COMMS.txt. If the GUI must be launched, log it first and confirm
the autostart gate with the owner — never surprise-launch.
