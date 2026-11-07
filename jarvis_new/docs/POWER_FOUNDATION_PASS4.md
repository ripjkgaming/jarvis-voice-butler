# JARVIS Power Foundation: Pass 4 implementation report

Date: 2026-10-29. Incremental, opt-in changes. Existing running voice agent was not restarted. `.env.local` was not modified. Existing unrelated tracked/untracked changes were preserved.

## Implementation and evidence

| Area | Status | Actual change / limitation |
|---|---|---|
| Native recovery | VERIFIED offline | Additional bounded step checkpoint history (`JARVIS_POWER_TASK_CHECKPOINTS=1`) in the same atomic native task snapshot, with revision, state, step states and attempt ID. New UUID attempt IDs for planning and research/think/write work. No payloads in checkpoints. Existing opt-in reconciler remains the only crash quarantine. Tests cover stale revisions, bounded history and interrupted execution. No automatic external action replay. |
| Approval binding | VERIFIED offline, INFERRED live | `JARVIS_POWER_APPROVAL_TICKETS=1` creates unique approval request IDs, SHA-256 canonical step fingerprints and 15-minute expiry for consequential step handoffs. Approval rejects expired or altered tickets. Consumed request IDs are recorded on the step. Existing `approve()` retains its legacy behavior for non-ticket tasks; this is an incremental safeguard, not a complete external-tool authorization system. No actual consequential step is executed by this engine. |
| MCP transport | VERIFIED local test | New `mcp_transport.py` implements literal-loopback Streamable HTTP JSON handshake, protocol negotiation, initialized notification and tools/list metadata retrieval. It disables redirects/proxies and has a 5-second maximum request timeout. The test server handshake passes; returned tools are non-executable. Only protocol `2025-03-26` and JSON responses supported; no authentication, SSE streaming or arbitrary user-specified endpoint execution wired to Jarvis. |
| Evidence memory | UNKNOWN new integration | Existing Pass 2 evidence attribution remains; no live-memory integration or source integrity observer. |
| Coding workers | UNKNOWN new integration | Existing proposal-only pool remains non-executing, no worktree/patch application implemented. |
| Voice/mobile timing | UNKNOWN | No new live voice instrumentation. Avoid measuring improvements without baseline. |
| HUD | INFERRED existing | Pass 2 read-only HUD remains unchanged. New checkpoint/ticket/MCP state is not yet displayed. |

## Tests and verification

- Mandatory baseline before modifying code: **76 passed, 1 warning**.
- Targeted recovery/approval tests: **14 passed**, Ruff **passed**.
- Local MCP protocol test: **6 passed**, Ruff **passed**.
- Tests run against temp home directories and an isolated loopback server; none prove live backend restart recovery or real external service outcomes.
- Additional full regression and final Ruff should be rerun after final changes.

## Files created or changed by this pass

- Modified: `src/agent_tasks.py`.
- Added: `src/power_foundation/mcp_transport.py`, `tests/test_power_foundation_pass4.py`, `tests/test_power_foundation_mcp_transport.py`.
- Added reconstruction: `docs/POWER_FOUNDATION_PASS3.md`.
- Added: this report and `docs/POWER_FOUNDATION_PASS5_HANDOFF.md`.

## Flags, rollout and rollback

Do not change `.env.local` automatically. Flags default to disabled. A future supervised process rollout can set `JARVIS_POWER_TASK_CHECKPOINTS=1` and `JARVIS_POWER_APPROVAL_TICKETS=1`, separately, after reviewing behavioral differences. The recovery reconciler requires `JARVIS_POWER_TASK_RECOVERY=1`; sanitized audit requires `JARVIS_POWER_FOUNDATION=1` plus `JARVIS_POWER_TASK_AUDIT=1`. Disable newly introduced flags to fall back to the original writer/approver for newly created task records; leave existing snapshots and historical artifacts untouched. Restart only in a planned maintenance window, with explicit authorization and voice health checks. Never manually edit native task JSON during active execution.

## Open risks and deferred implementation

- Checkpoints are bounded in-task history, not a standalone transaction log. Durable commit covers native state only; external tool exactly-once semantics require downstream idempotency keys and observer reconciliation. Full stage-by-stage crash injection and cancellation registry settlement remain pending.
- Approval tickets are bound to consequential task step content but not yet tied to a named user identity, trusted external confirmation provider or downstream tool invocation. The approved preparation still needs its established real-world tool guard. Persisting expiry does not itself authenticate consent. Audit consumption at a trusted tool boundary is next.
- MCP metadata is untrusted. Add authenticated local-server registration and protocol/session validation, pagination, vetted schema interpretation, transport variants and explicit allowlist wiring. Never automatically execute returned tools, even if marked read-only.
- Add immutable source anchors, retrieval freshness/contradiction display, and privacy/deletion semantics to existing memory before enabling memory influence on consequential actions.
- Build review-only coding diffs in separate worktrees with resource caps and user consent before any apply/test execution. Continue to forbid unsupervised edits, deployment, transactions and arbitrary desktop actions.
- Add opt-in bounded monotonic telemetry for voice, TTS, mobile and reconnections with privacy-preserving aggregation; then display sanitized status and verification receipts in the existing HUD.
- WhatsApp sends/attachments, purchases, unrestricted desktop operation, cross-device orchestration and destructive rollback remain explicitly deferred; require exact action binding, independent outcome confirmation, cancellation and human approval before implementing.
