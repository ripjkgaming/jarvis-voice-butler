# PHASE 5 MEGAPROMPT — paste everything below the line to a fresh AI

You are taking over Phase 5 (intelligence + voice quality) of the Jarvis
PC app. You have zero context: everything you need is below. Read it all
before touching anything.

CRITICAL: you run IN PARALLEL with a Phase 4 agent. Assume everything
Phase 4 promises will land (mic-mute IPC, overlay events, diagnostics
command, log rotation, updater checks, egress lockdown) — design against
those interfaces, do not reimplement them. Coordinate exclusively through
`/home/ripjk/jarvis-voice-butler/jarvis_new/AGENT_COMMS.txt`
(append-only log; CLAIM lines before touching any file outside your
areas; never rewrite others' lines). Your track tag is `[phase5]`. If a
Phase 4 slice you depend on is late, log it and work another slice —
never reach into Phase 4's files to "help".

---

## 0. REPO FINGERPRINT — DO THIS FIRST, NO EXCEPTIONS

There are TWO Jarvis trees on this box. You work ONLY in this one:

- CORRECT: `/home/ripjk/jarvis-voice-butler/jarvis_new/`
- WRONG (legacy prototype, do not touch, do not read for guidance):
  `/home/ripjk/jarvis/`

Before anything else, run ALL of these checks. If ANY fails, STOP and
report — do not improvise in another directory:

```
test -f /home/ripjk/jarvis-voice-butler/jarvis_new/src/agent.py \
  -a -f /home/ripjk/jarvis-voice-butler/jarvis_new/src/bridge.py \
  -a -d /home/ripjk/jarvis-voice-butler/jarvis_new/shell \
  -a -f /home/ripjk/jarvis-voice-butler/jarvis_new/AGENT_COMMS.txt \
  && echo RIGHT_REPO || echo WRONG_TREE_STOP
```

All work happens with cwd `/home/ripjk/jarvis-voice-butler/jarvis_new`
(or an absolute path under it). Never `cd ~/jarvis`. Never reference
`src/new_jarvis/` (that path exists ONLY in the legacy tree — if you see
it, you are lost; stop).

## 1. What Jarvis is

Offline-first voice butler for Linux (Nobara/Fedora, KDE Wayland).
"Hey Jarvis" (offline wake word) → voice agent executes laptop tools
(apps/websites, system control, WhatsApp via local bridge, inbox,
devices, research, screenshots/OCR) → HUD overlay shows live activity.
Local-first: no cloud except AI inference + the user's own keys.

## 2. Architecture (5 processes, must all run)

Tauri shell (Rust) supervises 4 sidecars in strict start order:

1. `livekit-server` (vendored binary at `~/.jarvis/bin/livekit-server`,
   config `~/.jarvis/livekit.yaml`, serves `ws://127.0.0.1:7880`) —
   open-source WebRTC server run LOCALLY. No LiveKit Cloud anywhere.
2. Agent worker: `/home/ripjk/jarvis-voice-butler/jarvis_new/.venv/bin/python
   /home/ripjk/jarvis-voice-butler/jarvis_new/src/agent.py start`
   (Python 3.14 venv). Pipelines (`$JARVIS_PIPELINE`): `realtime`
   (default, Gemini Live direct), `direct` (cloud-free: local Silero VAD
   + local faster-whisper STT in `src/local_stt.py` + Gemini-direct LLM
   + Piper TTS), `local` (legacy Cloud Inference — BROKEN on a local
   server by design, do not touch).
3. Wake listener: `.../jarvis_new/.venv-wake/bin/python
   .../jarvis_new/src/wake_client.py` (separate Python 3.11 venv —
   openwakeword requires <=3.11).
4. Bridge: `.../jarvis_new/.venv/bin/python .../jarvis_new/src/bridge.py`
   (stdlib only). Loopback HTTP on `127.0.0.1:4317` (env
   `JARVIS_BRIDGE_PORT`, optional `JARVIS_BRIDGE_TOKEN`): `GET /health
   /status /actions?limit= /sys /config`, `POST /mic`, `GET /mic`.
   Presence-flags only, never secret values.
5. HUD webview: static Next.js export in
   `/home/ripjk/jarvis-voice-butler/jarvis_new/shell/ui/` (built from
   `.../frontend/` via `pnpm build`, output `out/`, copied to
   `shell/ui/`). No Next.js server at runtime.

The Tauri shell (`.../shell/`, Rust, Tauri v2) owns boot order, contract
env, tray, hotkey (Super+J), single-instance CLI, autostart, and the
`app_config` / `mint_token` invoke commands.

## 3. Repo layout (all paths absolute under
## `/home/ripjk/jarvis-voice-butler/jarvis_new/`)

- `src/agent.py` — worker entry, pipelines, handoffs. YOURS only in the
  pipeline sections (`_pipeline_name`, `_session_for_pipeline`,
  `_default_agent_llm`, `_session_tts`); router/handoff/tool code is
  shared — ask in comms first.
- `src/local_stt.py` — faster-whisper STT plugin (yours).
- `src/local_voice.py` — Piper TTS plugin (read-only; reuse its render
  for fixtures).
- `src/intent/` — resolver + needle router (yours). NOT on the live
  voice path; do not wire it into any session.
- `src/proactive/`, `src/briefing_job.py` — watcher/monitors/briefing
  (yours).
- `src/wake_client.py`, `src/bridge.py` mic endpoints, `shell/` (ALL),
  `src/wizard/livekit.py`, HUD mic button/orb — PHASE 4'S. Never touch
  without logged CLAIM + their ACK.
- `src/system/`, `src/tools.py`, `src/browser.py`, `src/prompts.py`,
  `src/hud_events.py`, `src/context/`, `src/wizard/` (except where a
  task names it) — read-only.
- `frontend/` — Next.js 15 + React 19. Node pinned via `.npmrc`
  (`use-node-version=24.21.0`); system node is v22 — always use the
  pinned toolchain. No `app/api/*` routes exist (cannot statically
  export — never re-add). Read-only for you.
- `tests/` — pytest suite. `DESKTOP_BUILD_PLAN.md` (your Phase 0 A/B
  notes go in its Phase 0 section), `.env.example` (your
  `JARVIS_WHISPER_MODEL` / `JARVIS_QUIET_HOURS` docs go here).
- `~/jarvis/data/learned/` — sandboxed recipe skills (yours to add to).
- Scratch: `/tmp/opencode/phase5/` (create it; recreate freely; NEVER
  commit large data, weights, or audio).

## 4. Environment contract (do not break)

- Shell sets for sidecars: `JARVIS_LOCAL=1`, `PYTHONPATH=<repo>/src`,
  `LIVEKIT_URL=ws://127.0.0.1:7880`, `JARVIS_HOME` (`~/.jarvis`),
  `AGENT_NAME` (`my-agent`). Shell loads `<repo>/.env.local` +
  `<repo>/frontend/.env.local` itself.
- Secrets live ONLY in those two ignored files + `~/.jarvis/livekit.yaml`
  (0600). Never print, log, commit, or fixture real secrets.
  `.gitignore` excludes: `.env*`, `.venv*`, `shell/src-tauri/target/`
  (6.3GB), `frontend/{node_modules,.next,out}`, HF caches.
- Python: `uv run pytest tests/<file> -q`, `uv run ruff check`,
  `uv run ruff format` — always with cwd
  `/home/ripjk/jarvis-voice-butler/jarvis_new/`. (If `uv run` ever tries
  to resync the env instead of running, fall back to
  `/home/ripjk/jarvis-voice-butler/jarvis_new/.venv/bin/python -m pytest`
  and `uvx ruff`, and log it.)
- Wizard: `uv run python -m wizard check` from the repo root above (NOT
  from `src/`). Green board = sysdeps/livekit/venvs/models/keys/audio.
- NEVER `sudo`; NEVER commit or push (owner pushes); NEVER launch the
  GUI without logging first (autostart gate — coordinate in comms).

## 5. Salvage — reuse this, do not redo it

A prior attempt produced good assets before the tree mixup. All verified
present; consume, don't rebuild:

- `/tmp/opencode/phase5-helper/negatives.jsonl` — 166 reviewed abstain
  utterances `{text, why_negative}` (30/35/45/30/25 gibberish/chitchat/
  out-of-scope/bait/multilingual), unique, no PII, 0 leaks ≥0.5 against
  the resolver. USE AS-IS as the negative half of the v2 dataset.
- Whisper benchmark (same box, same models, CPU int8, 11 utterances):
  tiny mean WER 0.26, base 0.07, small 0.07 (small costs 3× time for
  nothing on commands). Use for 5.3 guidance; re-measure only if you
  distrust it (script: `/tmp/opencode/phase5-helper/bench_whisper.py`).
- `jarvis_new/skills/{morning_routine,focus_mode,meeting_prep}.md` —
  already placed correctly; validate + wire seeds, don't redraft.
- `jarvis_new/tests/test_voice_fixtures.py` — fixture builder (11 Piper
  fixtures, deterministic seeds); extend, don't replace.
- Known findings (bake into designs): (a) Piper renders are
  NONDETERMINISTIC run-to-run (md5 differs, onset clipping) → always
  cache rendered fixtures (pattern: `~/.cache/jarvis-regressions/`,
  nothing committed); (b) faster-whisper re-downloads pruned models on
  demand, so the wizard may prune unselected sizes; (c) number-wording
  ("5" vs "five") inflates WER ~0.17–0.57 on ALL sizes — normalize
  digits-to-words before scoring or exclude numbers from strict asserts;
  (d) finetuning freezes the Needle confidence head (scores come back
  None) — v2 deploy gate is BEHAVIORAL (abstention + accuracy deltas),
  not score-based.
- Training env (if you restart v2): venv
  `/tmp/opencode/needle-train/` (Python 3.11, needle CLI), base weights
  `/tmp/opencode/checkpoints/needle2.pkl`, dataset builder
  `/tmp/opencode/needle_data2.py` (325 train: 195 routed + 130 neg =
  40.0%; 60 held-out, verbatim-disjoint, leak-asserted).

## 6. Your work, in order (each slice shippable; TDD everything)

### 5.4 Regression harness FIRST (2 days — everything else depends on it)
`tests/test_voice_regressions.py` + `tests/voice_corpus.json`, NO mic /
server / keys: (a) 200+ intent paraphrases → expected tool+params
through `resolve_intent` tiers; (b) 10 cached Piper fixture wavs (see §5
cache rule) → fuzzy/WER match through `FasterWhisperSTT` (normalize
digits first per §5c); (c) `JARVIS_REGRESSION_UPDATE=1` escape hatch that
rewrites expectations then FAILS LOUDLY (CI never silently passes).
Accept: full run <5 min offline, deterministic, header docs explain
adding cases.

### 5.2 Proactive layer (3–4 days)
New module `src/proactive_policy.py` (or equivalent): `JARVIS_QUIET_HOURS`
(default 22:00–07:00, overnight wrap — nothing speaks in quiet hours;
urgent queues, never wakes), cap 3/day (`JARVIS_PROACTIVE_MAX_PER_DAY`;
urgent-safety bypasses cap but is logged), 30-min batching, 1-h dedupe,
every decision reason-coded to the action log. Wire into the existing
monitors/watchers with backward-compatible signatures (optional params
with defaults). Briefing: dedupe, cap spoken length (~600 chars), skip
empty sections, high-signal-only speech. Tests with frozen clocks: 7-day
sim → ≤3/day, zero duplicates, zero quiet-hour deliveries; watcher
overlap-skip/stale-takeover/backoff. Document new env vars in
`.env.example`. NEVER: proactive destructive actions, exfiltration,
waking the user.

### 5.3 Direct-pipeline quality (3 days)
(a) `tests/test_direct_perf.py`: time-to-first-transcript + p50 turn
latency on fixtures; assert p50 <6s CPU. (b) Confirm the §5 benchmark
with your 5.4 fixtures; write the size recommendation next to
`JARVIS_WHISPER_MODEL` in `.env.example`; VAD sensitivity sweep, keep
defaults unless numbers say otherwise. (c) realtime-vs-direct A/B notes
into `DESKTOP_BUILD_PLAN.md` Phase 0 section. Do NOT change the default
pipeline (`realtime` stays).

### 5.1 Needle LoRA v2 (1 week, LAST — needs 5.4 green)
Dataset: reuse `/tmp/opencode/needle_data2.py` + §5 negatives (target
≥300 ex, ≥40% neg). Per-example tool SUBSETS (target + ~3 distractors —
full catalogues truncate answers → loss exactly 0.0000; hard rule).
Train CPU-only (RTX 2050 4GB OOMs even at batch 1 — proven twice):
rank 8, ~9 min/epoch/100ex. NOTE: a previous v2 run died at step
30/296 with loss plateaued ~1.4 — if YOUR run plateaus the same way by
epoch 2, STOP and rework the dataset (more near-miss negatives, check
for label noise) instead of burning 5 hours. GATE (absolute, behavioral
per §5d): held-out abstention on gibberish ≥95% AND routed accuracy
≥ base +10pts. Wire `JARVIS_NEEDLE_WEIGHTS` ONLY on PASS; else archive
under `~/.jarvis/voice-butler/needle/` (untracked) + document.

### 5.5 Learned skills (ongoing, small)
Validate the 3 placed recipes (all `@function_tool` names real —
re-grep, trust nothing): 9+ trigger paraphrases each into the 5.4
corpus + resolver seeds (you MAY edit `src/intent/resolver.py` SEED
lists — yours), one live-call demo each logged with transcript
(focus-mode: mocks only, it has side effects). New recipes only if
trivial: compose EXISTING tools, sandbox rules unchanged.

## 7. Explicit non-goals

`local` legacy pipeline; `shell/`; bridge/wake IPC; wizard (except
documenting YOUR env vars); updater/logs/egress; Windows/macOS;
Flatpak; mobile; changing pipeline defaults; committing weights, audio,
fixtures >1MB, or secrets.

## 8. Verification checklist (per slice, paste output in your DONE)

```
uv run pytest tests/test_intent.py tests/test_needle_router.py tests/test_local_stt.py tests/test_voice_regressions.py tests/test_proactive.py tests/test_direct_perf.py -q
uv run ruff check <edited files> && uv run ruff format --check <edited files>
uv run python -m wizard check      # confirm no collateral (expect all green)
```

## 9. Starting verified state (do not regress)

Shell 14/14; pnpm build green; wizard 32+/32; live boot + E2E proven;
`direct` transcribes; default `realtime`. Pushed to
`ripjkgaming/jarvis-voice-butler` `main`. A `[phase5-helper]` agent may
work H1–H3 support tasks in parallel — read its DONE lines; it proposes,
you decide (review/merge/reject with logged reasons within a day).

## 10. Report back

Per slice: (a) shipped + files (absolute paths), (b) full pasted test
output + measured numbers, (c) owner sudo/GUI needs. Cross-track notes
in AGENT_COMMS.txt with `[phase5]` tag. If the GUI must launch, log
first and confirm the autostart gate with the owner — never
surprise-launch.
