# Jarvis PC App — Desktop Build Plan

Decisions locked 2026-09-17: **Tauri shell · Linux-only v1 · bundled local `livekit-server`.**

Runtime topology (5 processes, Tauri orchestrates):

```
┌ Tauri shell (tray + overlay + HUD webview) ─────────────────┐
│  Rust backend: process manager · global hotkey · autostart  │
│  Webview: existing Next.js HUD (ported, Phase 2)            │
└──────┬──────────┬──────────────┬──────────────┬─────────────┘
       │          │              │              │
  livekit-server  agent worker   wake_client    bridge :4317
  :7880 (binary)  (venv 3.14)    (venv-wake)    (stdlib http)
```

## Why LiveKit stays (summary)

LiveKit is five load-bearing systems in this codebase: realtime audio
transport (rooms/tracks), the agent runtime (`AgentSession`, dispatch,
handoffs), the AI pipeline plumbing (Inference STT/LLM/turn-detector,
Gemini Live plugin, Piper-as-TTS-plugin), the HUD data-channel bus, and
token auth/dispatch. Only the **server** changes (Cloud → local binary on
`127.0.0.1:7880`); no Python/TS code changes. Quota 429s disappear; Gemini
keys + network still required for inference.

---

## Phase 0 — Local LiveKit server swap (2–3 days, zero UI)

1. Download `livekit-server` binary (~30MB, latest v1.x; protocol-compatible
   with `livekit-agents>=1.6.9` / `livekit-client ^2.17.2`).
2. Create `~/.jarvis/livekit.yaml` (0600): static keypair
   (`openssl rand -hex`), TURN off, room auto-empty timeout ~60s. Check in
   `livekit.example.yaml` only — never real keys.
3. Env-only rewire (no code): `LIVEKIT_URL=ws://127.0.0.1:7880` + local
   keypair in `.env.local` (worker) and `frontend/.env.local` (token route +
   `wake_client.load_livekit_env`, `src/wake_client.py:113-130`).
   `AGENT_NAME=my-agent` unchanged; dispatch works identically locally.
4. Start order: `livekit-server` → `uv run src/agent.py start` →
   `.venv-wake/bin/python src/wake_client.py` → `pnpm dev` (frontend).
   Full loop: "hey Jarvis" → ack → talk → hangup.
5. Re-run quota-killed suite: `test_routing_dryrun` (5), `test_handoff_desktop`
   (2), `test_agent.py` LLM tests (3) — expect green first time.

Accept: end-to-end call with `*.livekit.cloud` firewalled off; 10 formerly
429ing tests pass; double-voice guard verified.
Risk: Gemini/AssemblyAI still need internet + Google key.
Status (2026-09-18): DONE by phase3 (livekit-server v1.13.7 vendored to
~/.jarvis/bin, livekit.yaml 0600, env rewired to ws://127.0.0.1:7880) and
VERIFIED by phase1 (server live, mint_token JWT signature VALID vs local
secret, shell 13/13 green). Caveat: the `inference.*` (STT/LLM/turn-detector)
tests now target Cloud Inference, which a local server cannot serve — the
`realtime` (Gemini-direct) pipeline is the supported local path; `local`
pipeline needs Cloud creds or a re-point.
Update (2026-09-18): re-point landed as JARVIS_PIPELINE=direct — Silero VAD
+ local faster-whisper ears (src/local_stt.py), Gemini-direct brains
(session + sub-agents), Piper mouth, auto->vad turn mode. Needs only
GOOGLE_API_KEY. `realtime`/`local` untouched. Try:
JARVIS_PIPELINE=direct uv run src/agent.py start (local server up).

## Phase 1 — Tauri shell: process manager + tray (1–2 weeks)

1. Install Rust (`rustup`), `cargo install tauri-cli` (v2), scaffold `shell/`
   (vanilla template; webview points at Next dev `:4000` now, static `out/`
   in Phase 2). System deps via dnf: `webkit2gtk4.1-devel gtk3-devel`
   (needs sudo — only sudo step in this phase).
2. Rust backend modules (`shell/src-tauri/src/`):
   - `manager.rs` — spawn order with health gates: `livekit-server` (TCP
     `:7880`) → agent worker (log watch "registered worker") → `wake_client`
     → `bridge.py` (`GET /health :4317`) → webview. Backoff restart (max 5,
     then tray error). Kill tree on quit.
   - `env.rs` — launcher sets `JARVIS_LOCAL=1`, `PYTHONPATH=src`,
     `LIVEKIT_URL` (local), `JARVIS_HOME` default `~/.jarvis`.
   - `tray.rs` — icon from bridge `/status` poll (2s): green/amber/red.
     Menu: Talk / Mute (placeholder) / Quit.
   - `hotkey.rs` — Super+J summon; fallback doc: KWin custom shortcut →
     `jarvis talk` (Wayland portal limits).
   As-built (`shell/src-tauri/src/`): `main.rs` (wiring, single-instance
   CLI, autostart-once marker), `manager.rs` (ordered gates + backoff
   restarts + kill-tree shutdown), `env_cfg.rs` (contract env, repo/binary
   resolution), `health.rs` (dep-free TCP/HTTP probes, state combinator),
   `tray.rs` (menu + status line via Tauri state), `hotkey.rs` (Super+J →
   Ctrl+Alt+J fallback). v1 Talk behavior: focuses the overlay; mic summon
   stays hotword-driven until the Phase 4 wake_client control hook.
3. CLI: `jarvis toggle|talk|quit`. Single-instance lock. Autostart via
   `tauri-plugin-autostart` → KDE `~/.config/autostart/Jarvis.desktop`,
   default ON with tray opt-out.

Accept: cold boot → green → Super+J → call → Quit leaves no orphans
(`pgrep` clean); worker kill -9 → red → auto-restart → green.
Risk: Rust learning curve — keep backend thin (process/env/tray/hotkey).
Status (2026-09-17): backend written, `cargo check` clean, 12/12 unit tests
green ×3 runs, clippy + fmt clean. GUI smoke + bundling deferred to the
Phase 3 packaging track (needs `-devel` system libs present; build-validated
via `cargo fetch`/`check`).

## Phase 2 — Frontend port: dev server → webview (3–5 days)

1. Token route (`frontend/app/api/token/route.ts:22-26`): the
   `NODE_ENV !== 'development'` guard throws in prod — replace with
   loopback-only check.
2. Static export: `next.config.ts` is empty — add `output: 'export'`; audit
   blockers (`app/api/*` can't export). Move token minting to Tauri
   `invoke('mint_token')` (has the secret; more secure); point
   `actions-log`/`sys` at bridge `/actions` + `/sys`. Data-channel HUD code
   untouched.
3. `app-config.ts`: `agentName` from Tauri config; rebrand LiveKit strings.
4. Node: system is v22, `package.json` wants 24.x — pin via `pnpm env use`.
5. Build `pnpm build` → webview serves `out/` (`tauri://localhost`).

Accept: overlay HUD live; Talk connects to local room, agent joins
(dispatch shows `my-agent`); typed command executes a tool; exec-feed live
over data channel; no-internet still renders HUD + bridge state.
Risk: budget a day for the static-export audit (Rive/xyflow fine on paper).

## Phase 3 — Python packaging + wizard + installer (1–2 weeks)

1. Delivery: ship `uv` + lockfiles, install on first run (~10 min). Frozen
   bundles rejected: dual Python (3.14 vs ≤3.11 for openwakeword) +
   `onnxruntime`×2 + `piper-tts` native bits. Pre-wins: drop dead
   `tflite_runtime` from `.venv-wake`, unify `onnxruntime`, audit the 3.7GB
   HF cache (5× faster-whisper + Mistral-Nemo GGUF — verify what's loaded).
2. First-run wizard: mic/speaker probe → `uv sync` ×2 venvs → Piper voice
   (61MB) + OWW models → keys (`GOOGLE_API_KEY`, Brave/You optional) →
   `~/.jarvis/{voices,brave-profile}` → bridge `/health` gate → finale.
   Persist step state; reruns skip green.
3. System deps script (`dnf` + `flatpak`): spectacle, imagemagick,
   tesseract-ocr, pipewire-pulse utils, playerctl, wmctrl, wl-clipboard,
   wtype, fd-find, brave, WhatSie/Sober flatpaks, notify, nmap/nikto/gobuster.
   Each with a wizard-visible check. `/dev/uinput` udev rule with consent
   screen.
4. Bundler: `.rpm` (Nobara/Fedora) + AppImage via `tauri-bundler`;
   `livekit-server` as per-arch sidecar; desktop entry + icon.

Accept: fresh Nobara VM → install → wizard green → voice + open_app +
WhatsApp + screenshot all work; clean uninstall.
Risk: ~7–8GB installed footprint (state on download page). No
Flatpak/sandbox packaging in v1 (kills D-Bus/uinput/CDP).
Status (2026-09-18): wizard + installer + Phase 0 + bundling built and
verified on the dev box (see AGENT_COMMS.txt phase3 DONE). `src/wizard/`
(stdlib core, runs pre-venv; persist + skip-green), `scripts/`
(jarvis-wizard, system-deps.sh, udev-uinput.sh consent, build-app.sh).
livekit-server v1.13.7 vendored to `~/.jarvis/bin` + `livekit.yaml`
(0600, keypair, TURN off) + env rewire, verified boot :7880. tflite
dropped from `.venv-wake` (ONNX backend verified). `cargo tauri build`
green → `Jarvis-0.1.0-1.x86_64.rpm` + AppImage (108M). 29/29 wizard
tests, ruff clean. Remaining: fresh-Nobara-VM install rehearsal (needs
real sudo + GUI), fd-find + brave-flatpak on this box.

## Phase 4 — Overlay polish + release hygiene (ongoing)

Overlay toggle/always-on-top/blur-hide/click-through orb; real mic mute
(needs a wake_client control hook — `SIGUSR1` toggle or unix-socket; the one
IPC gap the bridge left); updater wiring (unsigned v1); log aggregation +
rotation (`~/.jarvis/logs/`; `actions.log` append-only today); crash
reporter; `livekit.yaml` egress lockdown once stable.

Accept: a week of daily driving, no orphans, no silent mic deaths, updates
keep wizard state.
