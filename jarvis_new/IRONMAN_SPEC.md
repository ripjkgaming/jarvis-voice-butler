# Ironman Spec: making Jarvis closer to Iron Man's Jarvis

Source: `~/Ironman plan.md` (ideas). This document turns each idea into a buildable spec
against what already exists in `src/`. Home/device control is deferred (out of scope).
Status verified 2026-09-30. Build progress is tracked in `IRONMAN_PROGRESS.md`.

## 0. Baseline (what already exists)

| Area | Existing pieces |
|---|---|
| Mail | `mail_watch_job.py` (60 s poll, urgency classify, `urgency_judge.py`), `draft_engine.py` (Sonnet drafts, stored in `~/.jarvis/drafts/`), `draft_notify.py`, `system/inbox.py` (Gmail API), send only via `confirm_email_action` |
| Calendar | `google_api.calendar_list/create`, `workspace_tools.import_schedule_to_calendar` + `confirm_calendar_import` (file schedules only, confirm-gated), `calendar_upcoming` |
| Notify | `notify.py` (route by input state: toast / speech / queue, quiet hours, dedupe, `Announcer`, 5 s drain), `dnd.py`, `input_idle.py` |
| Proactive | `proactive/{monitors,policy,watcher}.py` (edge-triggered, cooldown, quiet hours, max/day) |
| Memory | `second_brain.py` (file graph + search), Obsidian vault `~/.jarvis/vault` (append-only), `projects.py`, `activity*.py` |
| Presence | `presence.py`, `camera_hub.py`, `eyes.py`, `gestures.py`, `wake_client.py`, `keyword_spot.py`, `local_stt.py` |
| Desktop | `system/desktop.py`, `kwin_windows.py`, `launcher.py`, `active_window.py`, `taskbar.py` |
| Agents | `claude_cli.py` (headless Claude), `system/background.py`, `backend_model.py`, `telegram_bot.py`, WhatsApp (`wa_autoreply.py`, every 5 min) |
| HUD | Next.js `frontend/`, `hud_events.py`, `drafts_ui.py`. Voice-only, no orb, cursor never used |

Hard rules carried through every feature: voice-only UI, no orb, anything consequential is confirm-gated,
Claude does UI/design, repetitive parts go to opencode (Muse Spark), Ling never writes code.

## 1. Priority 1: Proactive awareness (build first)

**Goal.** Jarvis speaks up unprompted when something matters, and stays silent otherwise.

### 1.1 Event sources (new `src/proactive/sources/`)
Each source is a pure function `poll() -> list[Signal]`; `Signal = {kind, key, title, detail, urgency, ts, action?}`.
- `calendar_source`: event starts in 10 min / 5 min / now; conflict detected; travel/prep hint. Uses `calendar_list`.
- `mail_source`: reuses `mail_watch_job` verdicts (`high` → speak, `normal` → HUD only).
- `build_source`: watches background jobs/tests/builds started via `system/background.py`; "build finished, 2 failures".
- `battery/system_source`: existing monitors.
- `deadline_source`: todos with due dates (`manage_todo`), assignments (school mode).

### 1.2 Policy (extend `proactive/policy.py`)
- One `ProactivePolicy.decide(signal) -> Decision(say|toast|hud|drop)`.
- Inputs: urgency, DnD (`dnd.py`), focus mode (`focus.py`), input idle (`input_idle.py`), quiet hours, per-day cap,
  fingerprint dedupe (`notify.send` already dedupes), and a learned "ignored last 3 times" penalty.
- Speech only when: urgency ≥ high, or user idle > 30 s and not in focus. Otherwise toast + HUD line + queue for next natural pause.
- Every decision logged to `actions.log` (`proactive <kind> <decision> <reason>`) so tuning is data-driven.

### 1.3 Email → calendar suggestion (the "you've got an email, shall I put it in your calendar?" flow)
Currently **missing** (calendar import only handles files). Spec:
1. In `mail_watch_job._handle_one`, after classification, for human mail run `event_extractor.extract(parsed)`:
   Sonnet via `claude_cli`, email treated as untrusted data (same marker pattern as `draft_engine`).
   Returns `{has_event, title, start, end, all_day, location, confidence}`; drop if confidence < 0.7 or start is in the past.
2. Dedupe against `calendar_list` for that day (same title+day skips, as `confirm_calendar_import` does).
3. Store pending suggestion `~/.jarvis/event_suggestions/<msg_id>.json` (status: pending | added | dismissed).
4. Announce via `notify.send(kind="calendar-suggest", speak_text="Email from <sender> mentions <title> on <day> at <time>, Sir. Shall I add it to your calendar?")`.
5. New tools in `workspace_tools.py`: `confirm_email_event(msg_id)` (creates via `calendar_create`) and `dismiss_email_event(msg_id)`.
   Never creates without an explicit spoken yes.
6. Kill switch `JARVIS_EMAIL_EVENTS=0`. Tests: extractor on fixtures (invite, school notice e.g. "MammoXpress 1-2 Oct", no-event mail, injection attempt), dedupe, confirm flow, past-date drop.

### 1.4 Acceptance
- A fixture email "Dentist Tuesday 3pm" yields exactly one spoken offer, zero calendar writes before "yes", one event after.
- No more than `max_per_day` unprompted utterances; none in quiet hours/DnD/focus unless urgency=critical.

## 2. Priority 2: Long-term context memory

**Goal.** "Pick up where I left off" and "what was I doing yesterday" work.

- **Store:** append-only Obsidian vault already exists. Add `Memory/Threads/<slug>.md` (running threads: project, status, last action, next step) and `Memory/Daily/<date>.md`.
- **Capture:** end-of-session summariser (`claude_cli`) writes: what was asked, files/projects touched (`projects.py`, `activity.py`), open loops. Runs on session close and nightly.
- **Retrieval:** `recall(query)` tool: hybrid of `second_brain.search` (files) + grep/embedding-free keyword score over vault notes; returns ≤5 snippets. Session start injects a 2-line "last time" via `context/injector.py` (cost $0: local strings).
- **Preferences:** `Memory/Preferences.md`, edited only on explicit "remember that…"; read into persona prompt (capped 500 chars).
- **Privacy:** nothing leaves the machine except the summariser call; redact secrets/tokens with a regex pass before write; `forget X` archives (moves to `Archive/`), never deletes (matches VAULT.md policy).
- **Tests:** summariser fixtures, redaction, retrieval ranking, injector size cap.

## 3. Priority 3: Whole-desktop control

**Goal.** Jarvis operates apps and the terminal, not just the browser.

- **Already:** launch/close apps, window listing (KWin), active window.
- **Add:** `arrange_windows(layout)` (KWin scripting: left/right/grid/fullscreen), `focus_window(title)`, `type_into_window`, `run_terminal(cmd)` in a dedicated tmux session with output capture.
- **Safety tiers:** read-only (list, focus) → free; reversible (open, arrange) → free with log; destructive (rm, kill, shutdown, installs, sudo) → spoken confirm, command echoed back verbatim. Command allowlist file `~/.jarvis/terminal_allow.json`; anything not on it is confirm-gated.
- **Undo:** window layouts snapshot before arrange (`undo_dir` pattern in `second_brain.py`).
- **Tests:** dry-run mode returning the planned command; confirm-gate coverage (every destructive verb must gate).

## 4. Presence and listening

- **Wake word:** local model (openWakeWord/Porcupine) replacing continuous STT; `wake_client.py` and `keyword_spot.py` are the seams. Mic opens to the pipeline only after a hit, with the existing wake-call listening window.
- **Barge-in:** cut-off within 300 ms of user speech; TTS stops, context retains what was said.
- **Acks:** for tasks > 2 s, emit a pre-recorded/cached short line ("On it, sir") before the work starts. Cached locally, no LLM cost.
- **Acceptance:** false-wake < 1/hour in a 1 h silent-room test; median wake→listening < 400 ms.

## 5. Capabilities

### 5.1 Multi-step background agent tasks
- `start_task(goal)` → `system/background.py` runs a Claude CLI plan-execute loop with a step budget (`system/budget.py`), streams progress to the HUD EXECUTION feed, and reports by voice on completion.
- Consequential steps (send, spend, delete, post) pause and ask via the notify queue. State persisted so a restart resumes.
- Example: "research X, draft the reply, book it" = research → draft (`draft_engine`) → **pause for confirm** → send.

### 5.2 Vision
- Screen: capture active window on demand (`take_screenshot`-style via KWin), pass to Gemini vision; "what's wrong with this error". Camera: `camera_hub.py` frame for "what's this part". Frames are never stored unless asked.

### 5.3 Code & project assistance
- `run_tests(project)`, `explain_failure()` (feeds last test output to Claude CLI), `open_pr()` via `gh` behind a confirm gate. Builds on `projects.py`.

### 5.4 Deferred
Home/device control (lights, music, phone over network). Only design placeholders; no build.

## 6. Feel

- **Persona consistency:** `prompts.py` already has adaptive persona tiers. Add a habit line from Memory ("you usually break at 3") and a one-line-status rule: default answers ≤ 25 words unless asked to elaborate.
- **HUD panels** (Claude-designed, voice-driven): system stats, tasks, calendar, mail, draft queue, suggestions. Voice commands: "show mail", "hide calendar". No cursor use, no orb.
- **Confirmation gates:** one shared `confirm_gate(action, summary)` helper so send/spend/delete/post/calendar-write all use the same spoken "yes/no" path and log; audit test asserts every consequential tool routes through it.
- **Workshop mode:** specialist sub-agents (research, code, ops) coordinated by Jarvis; each is a `claude_cli` role with a scoped tool list; Jarvis speaks one merged status. Build after §1–3 are stable.

## 7. Build order and delegation

| Phase | Work | Who |
|---|---|---|
| A | §1.1–1.4 proactive engine + email→calendar | Claude designs policy and prompts; opencode does source pollers, fixtures, tests |
| B | §2 memory | Claude: schema and summariser prompt; opencode: storage, redaction, tests |
| C | §3 desktop | Claude: safety tiers; opencode: KWin/tmux plumbing |
| D | §4 wake/barge-in | Claude |
| E | §5 background agent, vision, code | split as above |
| F | §6 HUD panels, workshop | Claude (UI); opencode never touches UI |

Each phase ends with: `ruff format --check`, `ruff check`, full `pytest`, and a manual voice rehearsal.

## 8. Known issues found during verification (2026-09-30)

1. **Gemini API quota (HTTP 429)** fails 8 tests (`test_agent` grounding and 7 in `test_handoff_desktop` / `test_routing_dryrun`). 1610 other tests pass. Not code bugs. Fix: wait for quota reset, use a paid key, or mark these `@pytest.mark.live_llm` and skip by default.
2. **One Google identity:** Jarvis uses `ripjkgaming@gmail.com` only. Calendar writes go to Jarvis's own Google login; `JARVIS_CALENDAR_ACCOUNT` selects which saved login (done).
3. `~/.jarvis/google_token.json` (shared login) lacks Gmail scopes; Gmail works only via the separate gmail_connect token. Calendar scope is present.
4. 4 old `error` entries in `mail_watch.state.json` (never retried). Add a bounded retry.
5. Stray untracked `jarvis_new/:memory:.ses` should be gitignored.
