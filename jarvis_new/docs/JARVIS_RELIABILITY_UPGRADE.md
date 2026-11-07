# Jarvis reliability upgrade — 29 October 2026

## Objective and scope

Make reasoning requests predictable during provider outages, respect the owner's
provider choice, and expose local backend health without spending model quota.
This pass builds on the existing working tree. It does not replace the existing
voice, memory, desktop, or Power Foundation implementations.

## Detailed implementation plan

1. **Establish a baseline.** Inspect repository guidance and existing changes;
   run the offline regression suite. Preserve existing changes and credentials.
   Use the in-repo `jarvis_rt` test suite (`tests/test_rt_*.py`) and the
   offline regression suite for regression strategy.
2. **Make routing deterministic where intent is explicit.** Recognize direct
   handling and explicit provider requests before consulting a classifier.
   Preserve exclusive provider choices through outages and classifier mistakes;
   reject malformed and oversized input before any remote classification.
   Keep classification advisory: routing must not execute work.
3. **Harden backend failover.** Support any configured provider mapping, reject
   unknown explicit providers without silently substituting another, validate
   timeout budgets, and contain provider exceptions and malformed responses.
   Retry only known pre-execution unavailability or observed usage limits;
   ambiguous failures must not replay a job. Verify total deadline accounting.
4. **Add inspectable recovery state.** Expose a thread-safe, read-only snapshot
   of provider cooldowns and remaining cooldown seconds; provide an offline
   JSON command for checking installed backend CLIs and selected configuration.
   Do not include prompts, credentials, raw errors, or model responses.
5. **Make Claude completion evidence precise.** Distinguish a missing CLI
   from a worker that started but produced an invalid or incomplete response,
   so fallback cannot silently replay an ambiguous execution.
6. **Verify and report.** Write failing regression tests before behavior
   changes, run targeted tests and Ruff, then assess the full offline suite.
   Record exact results, limits, changed files, and rollback instructions here.

## Acceptance criteria

- Explicit direct handling never invokes the classifier.
- An explicit provider remains authoritative even if classification disagrees.
- Exclusive requests never run another provider.
- Provider exceptions, malformed responses, and invalid budgets return stable
  warnings rather than crashing or replaying work.
- Backend diagnostics run locally without invoking a model.
- Cooldown expiry restores eligibility using a monotonic clock.
- Existing backend, router, and workspace integration tests pass.

## Verification results

### VERIFIED

- Before implementation, regression tests reproduced invalid timeout dispatch,
  unknown provider substitution, malformed provider response crashes, uncaught
  exceptions, and incomplete Claude results incorrectly marked unavailable.
- Added keyword-confusion tests that initially reproduced replay when an error
  merely mentioned `unavailable`, `disabled`, or `quota`. Failover now recognizes
  explicit adapter pre-start failures and concrete usage-limit indicators.
- Targeted regression command: `uv run pytest -q
  tests/test_backend_reliability_upgrade.py tests/test_backend_pool.py
  tests/test_request_router.py tests/test_claude_backend.py
  tests/test_workspace_tools.py tests/test_backend_model.py --disable-warnings`.
  Result: **89 passed in 2.02 seconds**, including 32 new cases.
- Ruff passes for all six changed Python files, including the existing
  workspace-tools integration. Existing workspace diff has no whitespace errors.
- Local diagnostics command runs successfully; both backend CLIs were found.
  No authentication or model request was attempted.
- A local timing probe ran 10,000 explicit routing decisions in 0.0089 seconds
  (0.89 microseconds per decision). This measures only local parsing, not
  end-to-end voice latency or remote provider speed.
- Repository-wide `uv run ruff check src tests --output-format concise` reports
  394 findings outside the six files validated for this pass. Automatic global
  fixes were not applied to the existing working tree.
- The baseline offline suite stopped at its configured 12-failure limit:
  734 passed, 12 failed, 5 skipped, 15 deselected. Failures involved removed
  Friday/Ultron characters and their stale switching/persona assertions.
- A subsequent full offline run was interrupted after 382.70 seconds:
  **1,381 passed, 41 failed, 5 skipped, 15 deselected**. All 41 recorded failures
  were in character-related modules. Process inspection showed background test
  HTTP server threads still running after failures. The run was interrupted
  with SIGINT; no agent process was signaled. A separate regression run excludes
  character-named tests to assess the rest of the working tree.
- The sequential non-character run was also interrupted because background test
  server threads persisted: **1,262 passed, 5 skipped, 91 deselected** in 325.80
  seconds, with no failures before interruption. These partial counts are not
  added to the final batch totals below.

### Completed broad regression in isolated batches

To avoid carrying test servers across thousands of unrelated cases, all test
files except character-named modules were partitioned into four independent
processes. Every batch used `-m 'not live_llm and not slow' -k 'not character'`.
Two batches initially failed collection because individual files rely on the
repository root being importable; those batches were rerun with
`PYTHONPATH=.:src`. No app code was changed for batching.

| Batch | Passed | Failed | Skipped | Deselected | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Early modules, including bridge and backend integration | 774 | 0 | 4 | 3 | 78.70 |
| Remaining files, partition 1 | 1,510 | 11 | 2 | 7 | 95.10 |
| Remaining files, partition 2 | 2,723 | 10 | 1 | 4 | 76.77 |
| Remaining files, partition 3 | 1,846 | 4 | 0 | 3 | 64.72 |
| **Total** | **6,853** | **25** | **7** | **17** | — |

The 25 failures are outside this pass's changed modules: 16 in legacy
`test_switch_conductor` / `test_switch_prewarm` cases for removed characters;
seven in chess series, commentary, and engine-error expectations; one in the
phone hangup response test; and one expecting the absent
`WakeClient._cancel_linked_turn` method. They were inspected, not silently
reclassified as passes. Batch isolation can expose import/order assumptions;
this result is not equivalent to a green sequential full suite. The backend
upgrade tests pass in both the targeted run and the broad batches.

### Usage

From `jarvis_new`, run:

```bash
PYTHONPATH=src uv run python -m backend_diagnostics
```

This reads the current process environment. It does not load credential files.
Within the running agent, `backend_diagnostics.report()` or
`backend_pool.status()` includes that process's cooldowns. A separate CLI process
cannot see the agent's in-memory cooldowns. `ready` means no active cooldown;
authentication, quota, and service reachability remain unknown.

The behavior changes apply on the next normal agent launch. The running voice
agent was not restarted during this upgrade.

### Files changed by this pass

- `src/request_router.py`: explicit intent handling and early input validation.
- `src/backend_pool.py`: validated budgets/responses, controlled exceptions,
  provider selection, remaining deadline, precise failover, cooldown snapshots.
- `src/claude_backend.py`: ambiguous execution failures do not indicate pre-start
  unavailability.
- `src/system/workspace_tools.py`: preserve routing intent with the classifier
  disabled (one integration expression; other existing changes preserved).
- `src/backend_diagnostics.py`: new offline JSON diagnostics command.
- `tests/test_backend_reliability_upgrade.py`: new deterministic regressions.
- `docs/JARVIS_RELIABILITY_UPGRADE.md`: plan, evidence, usage, and limitations.

### UNKNOWN / practical limits

- Live microphone latency, real provider authentication, and real provider
  failover were not exercised. No audio-quality improvement is claimed.
- Providers must honor the supplied remaining timeout. The pool rejects a late
  return but cannot forcibly interrupt an arbitrary Python provider callable.
- Limit detection uses warning text from the current adapters; structured
  provider error types would be a useful future improvement.
- Explicit routing recognizes bounded leading request forms, not every possible
  natural-language expression. The classifier handles other forms when enabled.
- The complete offline suite has not passed; the interrupted full-run result
  above must not be presented as full validation.

### Rollback

Preserve the pre-existing working tree: do not reset these files to Git HEAD.
Reverse only this pass's changes to routing, pool validation/status, Claude's
two failure messages, and the disabled-classifier expression in workspace tools.
Remove the new diagnostics module and regression file if no longer needed.
No dependency, credential, persistent application state, or feature flag change
is required for this upgrade.
