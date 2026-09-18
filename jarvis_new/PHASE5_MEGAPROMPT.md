# PHASE 5 MEGAPROMPT — paste everything below the line to a fresh AI

You are taking over Phase 5 (intelligence + voice quality) of the Jarvis
PC app. You have zero context: everything you need is below. Read it all
before touching anything.

CRITICAL: you run IN PARALLEL with a Phase 4 agent. Assume everything
Phase 4 promises will land (mic-mute IPC, overlay events, diagnostics
command, log rotation, updater checks, egress lockdown) — design against
those interfaces, do not reimplement them. Coordinate exclusively through
`jarvis_new/AGENT_COMMS.txt` (append-only log; CLAIM lines before touching
any file outside your areas; never rewrite others' lines). Your track tag
is `[phase5]`. If a Phase 4 slice you depend on is late, log it and work
another slice — never reach into Phase 4's files to "help".

---

## 1. What Jarvis is

Offline-first voice butler for Linux (Nobara/Fedora, KDE Wayland).
"Hey Jarvis" (offline wake word) → voice agent executes laptop tools
(apps/websites, system control, WhatsApp via local bridge, inbox,
devices, research, screenshots/OCR) → HUD overlay shows live activity.
Local-first: no cloud except AI inference + the user's own keys.
Full architecture, repo layout, env contract, and tool commands are
documented in `jarvis_new/PHASE4_MEGAPROMPT.md` (§1–§5) — read that file
first, it applies to you verbatim (paths, commands, secret rules,
no-sudo/no-push/no-surprise-GUI-launch). What follows is only what is
DIFFERENT for Phase 5.

## 2. Your territory vs Phase 4's (file boundaries — hard rules)

PHASE 4 OWNS (never touch without a logged CLAIM + their ACK):
`jarvis_new/shell/` (all of it), `src/bridge.py` mic endpoints,
`src/wake_client.py` socket hook, tray/overlay/updater/manager-logs code,
`src/wizard/livekit.py` (egress), HUD mic button + orb visuals.

PHASE 5 OWNS (your area, no claim needed):
`jarvis_new/src/intent/` (resolver + needle router), `jarvis_new/src/local_stt.py`,
`jarvis_new/src/proactive/`, `jarvis_new/src/briefing_job.py`,
`jarvis_new/src/agent.py` PIPELINE SECTIONS ONLY (`_pipeline_name`,
`_session_for_pipeline`, `_default_agent_llm`, `_session_tts` — the
handoff/router/tool code in that file belongs to everyone, ask first),
`jarvis_new/tests/test_*.py` for the above, eval/data scripts under
`/tmp/opencode/` (recreate, never commit large data), `~/.jarvis/`
runtime artifacts (untracked). Learned-skill definitions under
`~/jarvis/data/learned/` (sandboxed recipes only).

## 3. Your work, in order (each slice shippable; TDD everything)

### 5.1 Needle LoRA v2 — tuning that can actually ship (1 week)
v1 failed safely (4/21 vs base 3/21, dead confidence head, gibberish
trigger-happiness — `.cact` archived UNDEPLOYED at
`~/.jarvis/voice-butler/needle/jarvis.cact`; lessons in
`src/intent/needle_router.py` docstring — read them first). v2 must fix
all three: (a) dataset ≥300 examples with ≥40% off-topic negatives +
hard paraphrases, per-example tool SUBSETS (target + ~3 distractors —
full catalogues truncate answers → loss 0.0000); (b) confidence
calibration (finetuning freezes the confidence head — solve or gate:
deploy ONLY if abstention on gibberish ≥95% AND routed accuracy beats
base by ≥10 points on the held-out set in `/tmp/opencode/needle_eval.py`
style harness); (c) CPU-train only (RTX 2050 4GB OOMs — proven; ~9
min/epoch/100ex, rank 8). Deploy gate is absolute: wire
`JARVIS_NEEDLE_WEIGHTS` ONLY on passing the gate; otherwise archive +
document. Hardware note: keep batch 1, max-len 512.

### 5.2 Proactive layer that earns trust (3–4 days)
Today: `src/proactive/watcher.py` + `monitors.py` + `briefing_job.py`
(poll-based, fragile). Work: (a) notification policy — batching,
quiet hours (env `JARVIS_QUIET_HOURS`, default 22:00–07:00), max 3
proactive interruptions/day with user-tunable cap; every proactive turn
logged with a reason code; (b) morning briefing quality — dedupe, cap
length, skip empty sections, speak ONLY high-signal items (test with
fixtures, no network); (c) watcher robustness — overlapping polls,
stuck-task detection, backoff (mirror `shell/.../manager.rs` ladder
philosophy: 1-2-4-8-16 then red). NEVER: proactive destructive actions,
proactive network exfiltration, or waking the user. Accept: 7-day
simulated week → ≤3 interruptions/day, zero duplicates, zero quiet-hour
violations (test with frozen clocks).

### 5.3 Direct-pipeline quality: measure, then tune (3 days)
The `direct` pipeline (Silero VAD + faster-whisper + Gemini-direct +
Piper) works but latency/accuracy are unmeasured. (a) Build
`tests/test_direct_perf.py`: time-to-first-transcript and end-to-end
turn latency on fixture audio (Piper-rendered commands — pattern exists
in prior work, re-derive, don't assume APIs); assert p50 turn <6s CPU.
(b) Whisper-size guidance matrix (tiny/base/small on this box's CPU —
measure, don't guess) + document the recommendation in `.env.example`
next to `JARVIS_WHISPER_MODEL`; VAD sensitivity sweep (missed-starts vs
false-cuts on fixture audio with pauses). (c) realtime-vs-direct A/B
notes (accuracy + latency + cost) appended to `DESKTOP_BUILD_PLAN.md`
Phase 0 section. Do NOT change default pipeline (`realtime` stays).
Accept: numbers in the plan, no latency regression vs today (measure
before/after on the same fixtures).

### 5.4 Text-level regression harness (2 days, enables everything above)
Recorded-utterance eval that runs with NO mic, NO server, NO keys:
(a) intent corpus — 200+ paraphrases → expected tool+params, run through
`resolve_intent` tiers (exact/keyword/fuzzy/needle-ask); (b) STT corpus —
10 fixture wavs (Piper-rendered across voices/speeds) → expected
transcripts through `FasterWhisperSTT` (fuzzy match, WER threshold, not
exact); (c) one `pytest` entry (`tests/test_voice_regressions.py`) + a
`--update-expected` escape hatch that FAILS LOUDLY on diff (never
silently rewrite goldens). This harness is the deploy gate for 5.1 and
the guardrail for 5.3 — build it before depending on it. Accept: full
run <5 min offline, deterministic (seed everything), README section in
the file header explaining how to add cases.

### 5.5 Learned-skills growth (ongoing, small)
`monitor-place` pattern exists (open apps on logical monitors). Add 2–3
recipe skills the same way (compose EXISTING tools only, no new
binaries): candidates — `morning routine` (briefing + open calendar +
weather in one turn), `focus mode` (mute + close distracting windows +
low brightness, restores after), `meeting prep` (next calendar event +
attendee web lookup + open notes). Each: skill file + intent seeds in
`resolver.py` + regression cases in 5.4's corpus. Auto-coded python
skills live sandboxed (`~/jarvis/data/learned/code_skills/`, 15s
timeout) — recipes preferred over code. Accept: each skill demoed via
the 5.4 harness + one live call each.

## 4. Explicit non-goals (do not start these)

New system tools requiring sudo/packages; Windows/macOS backends;
Flatpak/sandbox packaging; mobile companion; changing the default
pipeline; touching `local` (legacy Cloud) pipeline; anything in Phase
4's file list (§2); committing model weights, audio fixtures >1MB
(generate fixtures at test time), or secrets.

## 5. Verification checklist (per slice, paste output in your DONE)

```
uv run pytest tests/test_intent.py tests/test_needle_router.py tests/test_local_stt.py tests/test_voice_regressions.py tests/test_proactive.py -q
uv run ruff check <edited files> && uv run ruff format --check <edited files>
uv run python -m wizard check      # confirm no collateral (expect all green)
```

## 6. Starting verified state (do not regress)

Shell 14/14; pnpm build green; wizard 32/32; live boot + E2E proven;
`direct` pipeline transcribes ("Hello Jarvis." live check);
`JARVIS_PIPELINE` default `realtime`. Pushed to
`ripjkgaming/jarvis-voice-butler` `main`.

## 7. Report back

Per slice: (a) shipped + files, (b) full pasted test output + measured
numbers, (c) owner sudo/GUI needs. Cross-track notes go in
AGENT_COMMS.txt with `[phase5]` tag — especially: anything you need
from Phase 4's interfaces, and anything Phase 4 should know about your
changes (e.g. new env vars like `JARVIS_QUIET_HOURS`).
