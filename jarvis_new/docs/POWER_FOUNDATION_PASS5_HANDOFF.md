# JARVIS Upgrade: Pass 5 Handoff

Use Remote Desktop Commander on Ripjk. Repository: `/mnt/data/jarvis-voice-butler/jarvis_new`. Reference image: `/home/ripjk/Downloads/JARVIS_AI_Assistants_Ranking_2026_WITH_BUILD_PLAN.png`.

Read `docs/POWER_FOUNDATION_PASS1.md`, `PASS2.md`, `PASS3.md`, `PASS4.md`. Inspect `git status --short` first. All existing tracked and untracked user changes must be preserved, even if unrelated or deleted in the working tree. No reset, cleanup, overwrite, commit, or `.env.local` edit. Do not restart the running voice agent to test. Implement code and run tests, rather than producing only a plan.

Pass 4 introduced opt-in, native atomic bounded checkpoint history and attempt IDs via `JARVIS_POWER_TASK_CHECKPOINTS=1`, and opt-in expiring, argument-fingerprinted approval tickets on consequential task preparation via `JARVIS_POWER_APPROVAL_TICKETS=1`. Added a safe, standalone numeric-loopback read-only JSON MCP protocol handshake and list inspector, with local server tests. All external tool execution remains outside this new layer. Pass 3 recovery remains opt-in via `JARVIS_POWER_TASK_RECOVERY=1` and prevents resumed interrupted steps. The reconstructed Pass 3 report is explicitly not the missing original.

**Pass 5 priorities:**
1. Run the mandatory full regression suite and Ruff, then examine task engine and confirmation gate end-to-end. Add test-driven crash injection at planning-start, step-start, result persistence and cancellation. No automatic replay of potentially consequential work. Add reviewed recovery workflow and task-revision conflict/lease diagnostics.
2. Tie single-use approval tickets to the actual downstream guarded tool request and trusted requester identity. Require the existing confirmation gate as well. Design idempotency keys for external actions, independently verified outcome receipts, redacted audit and expiry/replay tests. Never enable live sends or purchases as a shortcut.
3. Extend MCP inspector with manual server allowlist wiring, authenticated test server, session protocol checks, pagination/schema validation, timeouts and metadata sanitization. Do not allow arbitrary tool calls. Distinguish local handshake tests from live real-server functionality.
4. Extend existing memory attribution with source anchors, recency and contradiction handling, never claiming sourced evidence proves truth.
5. Build bounded proposal-only coding-worker previews and reviewed diffs in isolated worktrees; no autonomous patch execution. Add nonblocking bounded voice/mobile latency metrics and small HUD status upgrades only after tests and privacy checks.
6. Keep exact files changed, tests/passes, measured behavior, feature flags, unknowns and rollback in `docs/POWER_FOUNDATION_PASS5.md`; save a Pass 6 handoff.

Maintain strict statuses: VERIFIED (actual test/observation), INFERRED (implemented but not demonstrated), UNKNOWN (untested/deferred). Work incrementally, maintain all existing protections and explain failures precisely.
