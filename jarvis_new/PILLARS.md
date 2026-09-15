# JARVIS Conversational Pillars — Build Tracker

Backup: [`pre-pillars` tag on `ripjkgaming/jarvis-pillars`](https://github.com/ripjkgaming/jarvis-pillars) (snapshot of `jarvis-voice-butler` before this build).
Status legend: `planned` · `in-progress` · `done`.

## Stage 0 — Backup + scaffold — `done`
- Goal: snapshot working tree to new repo before any pillar code.
- Done: `gh repo create ripjkgaming/jarvis-pillars --private`, backup commit, `pre-pillars` tag, pushed `main` + tag. Secrets verified ignored (`.env.local` x2).

## Stage 1 — Continuous Multimodal Grounding — `done`
- Goal: agent reads HUD telemetry + laptop state without being told.
- Files: `src/context/__init__.py`, `src/context/telemetry.py` (snapshot dataclass, sim + real feeds), `src/context/injector.py` (compact ~2-line context string).
- Tests: `tests/test_telemetry.py` (pure: snapshot render, sim stepping, injector compactness).
- Demo: `uv run pytest tests/test_telemetry.py`.
- Cost: $0 (local strings, no inference).

## Stage 2 — Proactive & Predictive Dialogue — `done`
- Goal: anticipate needs (battery depletion on climb) with edge-triggered spoken warnings.
- Files: `src/proactive/__init__.py`, `src/proactive/monitors.py` (drain-rate, depletion prediction, thresholds — pure), `src/proactive/watcher.py` (15s poll loop, cooldowns, `generate_reply` on transitions only). Wired into `my_agent()` in `src/agent.py` (failure-swallowed).
- Tests: `tests/test_proactive.py` (drain math, threshold edges, cooldown suppression, one-alert climb rehearsal).
- Demo: sim climb scenario drains 30→6% and fires edge-triggered warnings.
- Cost: $0 idle; one LLM turn per real warning only.

## Stage 3 — Intent-Driven Disambiguation — `done`
- Goal: slang/fragment/sarcasm → concrete params ("hot rod red" → color crimson).
- Files: `src/intent/__init__.py`, `src/intent/resolver.py` (exact alias incl. in-phrase → fuzzy → clarify tiers).
- Tests: `tests/test_intent.py` (alias hit, in-phrase hit, fuzzy, low-confidence clarify, sarcasm fixtures).
- Demo: `uv run pytest tests/test_intent.py`.
- Cost: $0 for alias/fuzzy; one Flash call only on fallback path.

## Stage 4 — Adaptive Persona & Subtext — `done`
- Goal: dry wit by default, terse when focused, fact-first + pushback when critical.
- Files: `src/prompts.py` (`PERSONA_BASE/FOCUSED/CRITICAL`, `PUSHBACK_POLICY`, `URGENCY_TIERS` appended to `AGENT_INSTRUCTIONS`).
- Tests: `tests/test_persona.py` + existing `test_prompts.py` green.
- Demo: "drain battery to zero" → refuse + offer saver mode.
- Cost: $0 (prompt text only).

## Stage 5 — HUD sync + E2E — `planned`
- Goal: frontend HUD data messages → agent context; sim orbital-climb rehearsal in console mode.
- Files: `src/agent.py` wiring (data subscription, watcher task lifecycle), frontend echo (separate change).
- Tests: manual rehearsal script; no new unit tests beyond Stage 1–4.
- Cost: unchanged session rates.

## Stage 6 — Docs + costs — `planned`
- Goal: all boxes green, `ruff format --check`, `ruff check`, `pytest` clean; budget-impact note here.
- LiveKit docs feedback submit (per AGENTS.md).
