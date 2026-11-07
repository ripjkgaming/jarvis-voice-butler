# Always Aware Phase 4 — implementation and verification

Updated 2026-11-07. Three subagents covered action guards/transactions,
tab split/recovery, and gate/executor integration. The main assistant
integrated their work, added a full-path browser test and read-only readiness
checks. No service or browser restart, extension installation, feature enable,
commit, stash, reset, checkout or clean was performed.

## Changes

- Web profiles advertise executable operations rather than the generic
  `browser`/`cdp_read` tools the executor could not run. The gate persists
  `targets.web_steps` without losing targets, typed values, final-action
  indices or booleans. Unknown/malformed/truncated plans and missing or
  duplicate final actions fail closed. Dry run checks the actual first target,
  requiring an unambiguous CSS selector or exact simple accessible name.
- The action guard covers neutral POST submit buttons, modified Enter,
  button Space/Enter, and intermediate steps marked `final=false`. Keyboard
  operations cannot bypass password, OTP or card-field restrictions. Unsafe
  URL schemes, malformed hosts/ports and nonfinite amounts are refused.
- Transaction intent is appended through `transactions.record` before the
  final click, so a transaction-log append failure prevents the action and
  a process crash leaves an intent receipt. Existing post-action receipts
  remain. The shared general-audit mirror retains its existing best-effort
  write semantics; this change does not claim that mirror failures propagate.
- Disabling Always Aware or its environment switch stops a run at its next
  checkpoint, including the paused state. New runs require enabled state.
- Known-monitor placement failures abort and hand back. Every source window
  and the task destination are verified. New task windows require verified
  placement. A vanished original window can be recreated around the same
  tab without reloading it; incomplete placement and marker cleanup remain
  journaled for retry. Recovery reports only completed handbacks.
- Marker cleanup requires a unique URL/title match, avoiding unrelated tabs
  sharing the same URL. Ambiguity retains recovery information.
- `scripts/aware_spike.py worker` now runs the existing isolated Chromium
  movement regression instead of calling a missing self-test function.
  KWin is explicitly mocked in that command.

## Executable web-plan contract

The shared `PlanStep` dataclass is unchanged. The gate stores:

```json
{
  "web_steps": [
    {"op": "type", "target": "Postcode", "text": "AB1 2CD", "action": null, "final": true},
    {"op": "click", "target": "Submit", "text": "", "action": 0, "final": true}
  ]
}
```

Operations are `click`, `type`, `press`, `select`, `check`, `wait_for`, `goto`.
The gate refuses `goto` for an existing user tab. Structured verifier entries
use `tool` for the operation and may include `target`, `text`, `action`,
`final`. Each declared action has exactly one final click/press. Existing
operation prefixes are normalized. Intermediate action references are kept
but still pass the executor's guards. There is no arbitrary JavaScript tool.

## Verification commands

```console
uv run pytest -q tests/test_aware_*.py
uv run python scripts/aware_spike.py worker
uv run python scripts/aware_brave_preflight.py
```

The browser tests use temporary Chromium profiles, a test copy of the worker,
random loopback ports other than 9222, fake KWin, and local fake checkout/form
servers. No real payment, email, user page or screen capture was driven.
`test_aware_phase4_flow.py` uses real read-only DOM checks for the first target,
serializes the gate plan, executes a declared local submit, independently
verifies it over a fresh CDP attachment, and checks the unrelated test tab's
input and session state are intact. Model replies are fake.

### Results from this run

- `uv run pytest -q tests/test_aware_*.py`: **686 passed**, one upstream
  Google SDK/Python 3.14 deprecation warning, in 67.99 seconds.
- Ruff checks pass on all changed Python modules, scripts and tests.
- `uv run python scripts/aware_spike.py worker`: **ok=true**; the isolated
  tab move/handback regression passed. KWin remained mocked.
- Read-only preflight exits **1**, as expected, because the live worker is
  absent. Real state still has no enabled file and defaults to off.
- The targeted diff whitespace check passes; unrelated pending repository
  edits were left alone.

## Live prerequisites and remaining manual checks

Read-only probe output: `artifacts/aware-phase4/preflight.json`.

| Check | Observed |
| --- | --- |
| CDP | Available at loopback 127.0.0.1:9222; browser reports Chrome/150.0.7871.114 |
| Tab worker | Absent; preflight correctly exits 1 with `no_tab_worker` |
| Main output | HDMI-A-2, 1920x1080, x=0 |
| Task output selected | eDP-1, 1920x1080, x=1920 |
| HUD output detected | DP-1, 1920x1080, x=3840 |
| Enabled state | No state file; default disabled |

The live launcher still lacks `--load-extension`. Follow RUNBOOK.md section 2
only after the owner authorizes extension loading and a Brave restart. Do not
alter or restart the current browser merely to make the preflight pass.

After approval, use only throwaway task/control tabs and a local fake form
for the live KDE checklist: secondary-monitor placement, unchanged main-window
focus and control-tab media/input, successful/failed/cancelled handback,
recovery after an interrupted run, lone-tab original-window reconstruction,
and minimised-window timer/animation progress. Headless success does not
verify KWin or native Wayland throttling. Do not disable monitor outputs or
perform a real send/payment as a test.
