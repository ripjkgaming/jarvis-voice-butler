# PHASE 5 HELPER MEGAPROMPT — paste everything below the line to a fresh AI

You are a HELPER subagent for Phase 5 (intelligence + voice quality) of
the Jarvis PC app. A MAIN Phase 5 agent owns slices 5.1–5.4 (harness,
proactive, pipeline quality, LoRA v2). You own the parallelizable
support tasks below. You have zero context: everything you need is here.
Read it all before touching anything.

Shared context (repo layout, env contract, tool commands, secret rules,
no-sudo/no-push/no-surprise-GUI-launch) lives in
`/home/ripjk/jarvis-voice-butler/jarvis_new/PHASE4_MEGAPROMPT.md` (§1–§5)
and the Phase 5 work plan in
`/home/ripjk/jarvis-voice-butler/jarvis_new/PHASE5_MEGAPROMPT.md` (§6) —
read both first; they apply verbatim. The salvage inventory
(PHASE5_MEGAPROMPT.md §5) applies to you too — reuse, don't rebuild.
Coordination exclusively through
`/home/ripjk/jarvis-voice-butler/jarvis_new/AGENT_COMMS.txt`
(append-only; never rewrite others' lines). Your track tag is
`[phase5-helper]`. Main agent's tag is `[phase5]`.

---

## 0. REPO FINGERPRINT — DO THIS FIRST, NO EXCEPTIONS

There are TWO Jarvis trees on this box. You work ONLY in this one:

- CORRECT: `/home/ripjk/jarvis-voice-butler/jarvis_new/`
- WRONG (legacy prototype, do not touch): `/home/ripjk/jarvis/`

Before anything else, run ALL of these checks. If ANY fails, STOP and
report — do not improvise in another directory:

```
test -f /home/ripjk/jarvis-voice-butler/jarvis_new/src/agent.py \
  -a -f /home/ripjk/jarvis-voice-butler/jarvis_new/src/bridge.py \
  -a -d /home/ripjk/jarvis-voice-butler/jarvis_new/shell \
  -a -f /home/ripjk/jarvis-voice-butler/jarvis_new/AGENT_COMMS.txt \
  && echo RIGHT_REPO || echo WRONG_TREE_STOP
```

All work with cwd `/home/ripjk/jarvis-voice-butler/jarvis_new` (or an
absolute path under it). Never `cd ~/jarvis`. Never reference
`src/new_jarvis/` (exists ONLY in the legacy tree — if you see it, you
are lost; stop).

## 1. Your territory (hard file boundaries)

YOU OWN (no claim needed):
- `/tmp/opencode/phase5-helper/` — scratch, measurement scripts,
  fixture tooling (recreate freely; NEVER commit large data).
- `/home/ripjk/jarvis-voice-butler/jarvis_new/tests/test_voice_fixtures.py`
  (exists — extend, don't replace).
- `/home/ripjk/jarvis-voice-butler/jarvis_new/skills/*.md` (exist —
  validate + improve in place).
- Proposals only (via comms, main agent applies): resolver seed lists,
  5.4 corpus additions.

YOU NEVER TOUCH without logged CLAIM + main agent's ACK:
`src/intent/` (except proposing seeds), `src/local_stt.py`,
`src/proactive/`, `src/briefing_job.py`, `src/agent.py`,
`tests/test_voice_regressions.py` (main agent's harness),
`jarvis_new/shell/` (Phase 4's — entirely off limits), anything Phase 4
owns (see PHASE5_MEGAPROMPT.md fingerprint + territory rules).

## 2. Your tasks (start immediately, independent of main agent)

### H1. Fixture factory, extended (feeds main 5.3/5.4)
`tests/test_voice_fixtures.py` exists — extend it: (a) grow to 20+
utterances (add multi-word commands, homophone pairs like
"mute/music", background-music variant at low SNR); (b) assert builder
determinism is HONEST — prior finding: Piper renders differ run-to-run
(md5 differs, onset clipping), so determinism must come from CACHING
rendered fixtures (`~/.cache/jarvis-regressions/`, nothing committed),
not from assuming stable synthesis — encode that in the test;
(c) extend the benchmark to report per-kind WER tables (command vs
number vs name vs noisy) + normalize digits-to-words before scoring
("5" vs "five" inflates WER 0.17–0.57 on ALL sizes — prior finding, bake
the normalizer in). Post the table to comms. Do NOT change pipeline
defaults or `.env.example` — main agent decides. Full run <5 min.

### H2. Skill recipes, validated (main 5.5 legwork)
`skills/{morning_routine,focus_mode,meeting_prep}.md` exist — validate,
don't redraft: (a) re-grep EVERY `@function_tool` name (prior catch:
`search_the_web` lives on BrowserTools, not ResearchTools — trust
nothing); (b) confirm each recipe's tools exist in THIS repo's
`src/system/` + `src/tools.py`; (c) post 5+ fresh paraphrase seeds PER
recipe to comms (do NOT edit `src/intent/resolver.py` yourself);
(d) re-run the three live demos only if cheap and side-effect-free
(focus-mode: mocks only — it moves volume/timers). Prior demo gaps to
close if possible without GUI: brave/ddg search dry-lookup path,
brightness/wmctrl absence (needs real GUI session — else document as
untestable-here). Post results.

### H3. Negatives top-up + leak watch (feeds main 5.1)
`/tmp/opencode/phase5-helper/negatives.jsonl` (166 items, 0 leaks ≥0.5)
exists — top up to 220+ with ADVERSARIAL cases aimed at the known leak
patterns (single keywords like "up/rain/todo", mind-reading phrasing
like "read my mind"→gmail 0.80, tool-bait imperatives). Re-run the leak
check for EVERY addition against `resolve_intent` (drop/rephrase ≥0.5).
Keep `{text, why_negative}` schema, uniqueness assert, no PII/profanity.
Post the final count + new leak patterns found. Corpus stays in
`/tmp/opencode/phase5-helper/` (scratch — main agent merges).

## 3. Explicit non-goals

Model training, weights, pipeline defaults, `.env.example` edits,
`tests/test_voice_regressions.py`, proactive/briefing logic, main
agent's or Phase 4's files, committing audio/data/secrets.

## 4. Verification checklist (per task, paste output in your DONE)

```
uv run pytest tests/test_voice_fixtures.py -q
uv run ruff check <edited files> && uv run ruff format --check <edited files>
uv run python -m wizard check      # confirm no collateral (expect all green)
```

## 5. Starting verified state (do not regress)

Shell 14/14; pnpm build green; wizard 32+/32; live boot + E2E proven;
`direct` transcribes; default `realtime`. On
`ripjkgaming/jarvis-voice-butler` `main`. Main Phase 5 agent works
5.1–5.4 in parallel — read its DONE lines; it decides, you propose.
Post data, not edits.

## 6. Report back

Per task: (a) shipped + files (absolute paths), (b) full pasted test
output + measured numbers/tables, (c) owner sudo/GUI needs.
