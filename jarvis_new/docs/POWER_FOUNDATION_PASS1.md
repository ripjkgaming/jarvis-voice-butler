# Jarvis power-gap report: foundation implementation, pass 1

Updated: 2026-10-29. Reference: user's "JARVIS_AI_Assistants_Ranking_2026_WITH_BUILD_PLAN.png".
Scope: build safe scaffolding and basic connector diagnostics only; postpone difficult or risky tasks.

## What was actually implemented

New Python package: `src/power_foundation/`, deliberately **not** auto-imported or attached to the running voice agent.

| Area from report | Pass-1 foundation | What is **not** done |
| --- | --- | --- |
| Reproducible task execution | SQLite tasks, idempotency keys, persisted transitions, recovery-to-paused, events | A new runner, scheduling, retry policy, crash-safe lease management or restart integration |
| Safety + permission audit | Action metadata preview (redacted), deny-all foundation executor, no raw action arguments in audit event | Approval UI, fine-grained policy bindings, threat modeling, independent security audit |
| True background survival | Persistent store and interrupted-task reconciliation | Live daemon, watchdog, resume/compensation logic |
| MCP integration catalog | Validated descriptors, discoverable catalog, read-only adapter registry | Real MCP transport/auth, arbitrary remote tool calls, permission-mapped execution |
| Parallel coding workers | Bounded proposal pool (max four workers), no execution wiring | Actual multi-agent coding, worktree isolation, merge arbitration, resource quotas |
| Evidence-backed memory | Persisted evidence record with source and observed timestamp, keyword lookup | Provenance validation, contradiction reconciliation, semantic search, privacy controls |
| Cross-platform voice UX | Voice/mobile interface discoverability and safe health catalog | Remote microphone pairing, latency metrics, device-state sync, new app releases |
| Visible agent workspace | Read-only task/event/connector snapshot JSON | A HUD front-end and live event streaming |

## Newly added files

- `src/power_foundation/__init__.py`
- `src/power_foundation/__main__.py`
- `src/power_foundation/engine.py`
- `src/power_foundation/connectors.py`
- `src/power_foundation/tool_catalog.py`
- `src/power_foundation/workflow.py`
- `src/power_foundation/policy.py`
- `src/power_foundation/verification.py`
- `src/power_foundation/workspace.py`
- `tests/test_power_foundation.py`
- This document

These files are additions only. Other pre-existing working tree modifications must be preserved.

## Connector status and limits

10 named interfaces are registered: desktop, Whatsie, browser, mobile, memory, tasks, voice, MCP, workers and downloads.

A regular `status` displays `scaffold_only` with `ready=false`. This means the Python module exists, not that the service works.

`status --live` performs **read-only** local probes for four endpoints:
- Desktop: `DISPLAY` or `WAYLAND_DISPLAY` environment set.
- Browser: a Brave/Chromium/Chrome binary found on PATH.
- Whatsie: response from `127.0.0.1:9223/json/version`.
- Memory: configured vault directory exists.

The other six remain `scaffold_only`; extending them requires defining a non-mutating health contract first.

In the initial live probe, all four were detected. This is availability only, not an end-to-end success guarantee.

## Commands and verification

Run from `/mnt/data/jarvis-voice-butler/jarvis_new`:

```bash
uv run --no-sync python -m power_foundation status
uv run --no-sync python -m power_foundation status --live
uv run --no-sync ruff check src/power_foundation tests/test_power_foundation.py
uv run --no-sync pytest -q tests/test_power_foundation.py tests/test_monitor_window_move.py tests/test_wa_image.py tests/test_memory.py tests/test_agent_tasks.py tests/test_whatsapp_session.py
```

Verified on 2026-10-29:
- `65 passed, 1 warning` (existing third-party Python deprecation warning).
- Ruff `All checks passed!`.
- The running voice agent, bridge and wake-word client processes remained active.
- No voice-agent restart, browser click, message send, file delete, spending or external publishing was performed.

Current feature switch: `JARVIS_POWER_FOUNDATION=1` permits registry **read-only probes** when called directly. Default is off. The foundation's `execute_action` always denies execution regardless of this switch. No agent startup wiring has been added.

## Important design boundaries

- The existing project already contains functioning/separately-tested modules `src/agent_tasks.py`, `src/system/task_tools.py`, `src/system/whatsapp.py` (including image attachment guarded path), `src/system/monitor_window_move.py`, `src/memory.py`, `src/system/codex_worker.py`, and related browser/voice code. Reuse these rather than duplicate.
- The new SQLite tasks are **independent scaffolding**, not a migration or replacement for the current JSON task store. Do not interpret them as active Jarvis tasks.
- `result_receipt(observation='observed', evidence='...')` validates presence of evidence metadata only. It does **not** prove the underlying observation is genuine. Real execution must independently validate it.
- `ProposalPool` only returns outputs; it cannot isolate malicious code or kill runaway worker threads. Only supply locally reviewed callables. Do not feed LLM-authored Python into it.
- A discovered MCP tool is data, not permission; tool descriptors advertise `executable=false`.
- The user's git working tree contains many prior changes, including tracked and untracked files. Don't discard, overwrite, stage or commit other work without inspection.
- Avoid changing runtime autostart or editing `.env.local` without explicit approval.

## Suggested next-pass sequence

1. **Integrate read-only status first:** add a guarded `/foundation/status` route or existing HUD adapter without touching the live agent until tests pass. Extend genuine local health probes for tasks, mobile, voice, workers and MCP. Test on the user's machine.
2. **Make consent meaningful:** map reviewed action descriptors to **existing** permissioned tools. One-time request-scoped approval; argument binding; independent outcome verification; blocked-action audit; tests for prompt injection, stale approval and cancellation. Never bypass existing gates.
3. **Task reliability:** adapter for the existing `agent_tasks` task format, idempotent workflow IDs, checkpoints, crash/restart tests, bounded retries and cancellation. Avoid creating two competing task schedulers.
4. **MCP servers/connectors:** manually allowlist servers, authenticating/transport-specific implementations, read-only probes, per-tool schemas, throttling/timeouts. No arbitrary arbitrary-command executors.
5. **Memory & coding:** provenance-backed retrieval, contradiction handling, code-worker workspace isolation, approval before edits and patch review, independent testing of changes.
6. **Voice/cross-platform/HUD:** remote session protocol health checks, latency budgets, live observable task state, user-visible action reviews. Preserve live voice latency.
7. **Evaluation:** build deterministic 30-task suite; verify task completion and evidence; assess 0 unapproved actions, restart survival, 7-day stability. Only claim success against measured evidence.

## Request for the next agent

Continue from this repository state. Do not re-create pass-1 scaffolding. Check git status before any change, run targeted tests before and after, and identify which pieces already exist. Focus on **one small safe vertical slice at a time**. Stop before high-risk actions and document how to finish them in a later pass. Provide a concise per-feature status table: VERIFIED implemented / INFERRED existing but untested / UNKNOWN or deferred. Record exact files changed, commands run, and test outputs.
