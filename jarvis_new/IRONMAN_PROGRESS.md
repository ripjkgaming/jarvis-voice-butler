# Ironman build progress

Tracks `IRONMAN_SPEC.md`. Branch `jarvis-voice-fixes`. Updated 2026-09-30.

| § | Item | Status | Where |
|---|---|---|---|
| 1.1 | Event sources (calendar, mail, jobs, system, deadlines) | Done | `src/proactive/sources/`, `src/jobs.py` |
| 1.2 | One decision policy (say / toast / hud / drop), learned ignore penalty, logged | Done | `src/proactive/engine.py` (bridge sidecar) |
| 1.3 | Email -> "shall I add it to your calendar?" | Done | `src/event_extractor.py`, `confirm_email_event` / `dismiss_email_event` |
| 1.4 | Acceptance: dentist fixture = one offer, 0 writes before yes, 1 after | Done (test) | `tests/test_event_extractor.py` |
| 2 | Long-term memory: Daily / Threads / Preferences, redaction, recall, "last time", forget -> Archive | Done | `src/memory.py`, `src/system/memory_tools.py` |
| 3 | Window layouts + undo, focus, list | Done | `src/system/window_layout.py`, `src/system/window_tools.py` |
| 3 | Terminal in tmux with tiers + allowlist | **Not built** | `run_command` (confirm-gated, argv-only, denylist) remains the shell tool |
| 3 | type_into_window | Covered by existing `focus_window` + confirm-gated `desktop_type` | |
| 4 | Wake word (local openWakeWord) | Already existed | `wake_client.py`, `keyword_spot.py` |
| 4 | Barge-in 300 ms | Done | `agent.barge_in()` (`JARVIS_BARGE_IN_S`) |
| 4 | Cached acks for slow tools | Done | `src/acks.py` |
| 4 | Acceptance (false wakes, wake latency) | Needs a live-mic test on the laptop | |
| 5.1 | Multi-step background tasks, pause before acting, resume after restart | Done | `src/agent_tasks.py`, `src/system/task_tools.py` |
| 5.2 | Vision: active window / camera | Done | `src/look.py`, `src/system/look_tools.py` |
| 5.3 | run_tests / explain_failure / gated open_pr | Done | `src/code_assist.py`, `src/system/code_tools.py` |
| 5.4 | Home/device control | Deferred (per spec) | |
| 6 | Persona: 25-word default, habit line | Done | `src/prompts.py`, `memory.habit_line()` |
| 6 | HUD panels by voice | Done | `src/hud_panels.py`, `frontend/components/hud/hud-panels.tsx`, bridge `GET /panels` |
| 6 | Shared confirm_gate helper + audit test | **Not built** | Each consequential tool keeps its own single-use exact-match gate |
| 6 | Workshop mode | Done (read-only specialists) | `src/workshop.py` |
| 8.1 | Live-model tests skipped by default | Done | `live_llm` marker; `JARVIS_LIVE_LLM=1` runs them |
| 8.2 | One Google identity / `JARVIS_CALENDAR_ACCOUNT` | Done | |
| 8.4 | Bounded retry of errored mail | Done | `mail_watch_job.retry_due()` |
| 8.5 | `:memory:.ses` gitignored | Done | |

Kill switches: `JARVIS_PROACTIVE_ENGINE`, `JARVIS_EMAIL_EVENTS`, `JARVIS_MEMORY`,
`JARVIS_ACKS`, `JARVIS_TASKS`, `JARVIS_EXAM_REMIND` (each `=0` turns it off).

Test suite (this container): 1787 passed, 20 skipped, 27 failed. All 27
also fail on the pre-Ironman baseline and need the real machine
(Playwright browsers, LiveKit credentials, local STT server, camera).

Still to do on the laptop: a voice rehearsal of each phase (spec §7).
