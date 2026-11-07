# Jarvis power foundation: Pass 2 integration report

Date: 2026-10-29. This pass is additive, opt-in, and preserves existing task execution and approval gates.

## Verified from tests

- Baseline: `65 passed, 1 warning` before changes.
- Expanded regression: `71 passed, 1 warning` with `tests/test_power_foundation_pass2.py`.
- `src/power_foundation/integration.py`: read-only projection of existing `agent_tasks` JSON state. Only task ID, state, revision, step counts, approval pending, and explicitly `not_checked` verification status appear. No goals, step contents, private outputs, or confirmation tokens. The older foundation task store remains separate and is not treated as live.
- `src/agent_tasks.py`: optional post-save checkpoint to the foundation event database. Existing native atomic write and cancellation/execution leases remain authoritative. A logging failure cannot fail the native task save. No task recovery scheduler was added or changed.
- `src/bridge.py`: authenticated-by-existing-bridge-guard `GET /foundation/status`, disabled by default.
- `frontend/components/stark/ops.tsx`, `stark-hud.tsx`: HUD panel polls foundation status and renders only when enabled. Labels explicitly indicate read-only and unverified. Requires frontend build/deployment to appear in a running HUD.
- `src/power_foundation/mcp_discovery.py`: manually approved local literal-loopback HTTP metadata allowlist. Does not contain a transport or invoke tools. Tests verify filtering of tools not marked read-only and absence of executable descriptors.
- Evidence helper `claim_evidence` returns `attributed_unverified` only for exact stored evidence text and `unsupported` for missing evidence. Neither implies independently validated truth.

## Feature flags and rollback

Default: feature disabled. Keep `.env.local` untouched. To enable a new bridge process, set `JARVIS_POWER_FOUNDATION=1` in its environment and restart only that service during planned maintenance, not automatically. The frontend HUD panel displays only when the bridge returns enabled=true. To record sanitized post-save checkpoints in new task worker processes also set `JARVIS_POWER_TASK_AUDIT=1`. This opt-in causes a brief SQLite write on each task save and has not been performance-benchmarked. Disable flags to roll back without changing existing tasks or deleting state. Do not manually call `power_foundation recover` on active state while voice tasks are running. The endpoint uses the existing bridge bearer mechanism: in deployments without a token the bridge may allow local unauthenticated access, so configure bearer authorization before exposing beyond loopback.

## Deferred or unknown

- Native task restart survival and crash-safe recovery: pre-existing locks and revision control observed, but no new crash-injection/e2e verification this pass. Add deterministic crash-at-each-checkpoint tests and prove executor lease recovery before changing resume behavior.
- Action previews and independent result verification: foundation metadata APIs remain non-executable. Next: map existing approval request IDs, bind approved argument digests, reject replay/stale approvals, independently inspect side effects, and persist receipts only from trusted observers.
- Real MCP: no server session or transport tested. Next: explicit per-server opt-in config, authentication, transport-specific handshake, local/remote security policy, SSRF and DNS rebinding defenses, timeouts, schema validation, and separate read-only action whitelist. Read-only hint metadata supplied by a server is not proof of a safe operation.
- Memory: attribution lookup only; stored claims may be false or stale. Next: immutable source anchors, contradiction resolution, deletion/privacy rules, and observer-backed validation before claims become `verified`.
- Coding workers: existing proposal pool only. Next: disposable isolated worktrees, concurrency quotas, diff review, explicit one-shot approval tokens, patch application only after review, sandboxing and independent tests. Do not automatically apply LLM code.
- Voice/mobile: native voice route and health remain unchanged; no measured latency or pairing session verification. Next: lightweight monotonic latency collection in bounded sampling and authenticated read-only connection checks, without increasing voice critical-path latency.
- HUD: compile and run UI in planned deployment; status labels currently use simple connector probes which reflect local prerequisites, not end-to-end service success. Add audit history/receipts drilldown once privacy boundaries are reviewed.
- Autonomous browser purchases/transactions, WhatsApp sending/images, unrestricted desktop control, autonomous code deployment, and complex cross-device orchestration remain prohibited in the new layer.

## Next agent handoff prompt

Continue in `/mnt/data/jarvis-voice-butler/jarvis_new`. Read `docs/POWER_FOUNDATION_PASS1.md` and this Pass 2 report, inspect `git status --short`, preserve every pre-existing change, and run baseline tests. The optional native task checkpoint observer and read-only `/foundation/status` endpoint are now implemented. Do not replace the native `agent_tasks` runner or restart live Jarvis without permission. First add tested checkpoint-to-recovery tracing with crash-injection and graceful shutdown, then independently validated receipt observations attached to existing confirmation-gated tools. Investigate a manually approved MCP transport implementation with authentication and a local-only test server, without making returned metadata executable. Add source-bound memory retrieval and supervised patch review. Measure voice/mobile latency without changing the live voice critical path. Test each vertical slice; distinguish VERIFIED, INFERRED, and UNKNOWN; do not commit unrelated modifications.
