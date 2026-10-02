# Insights integration QA — 2 October 2026

Implemented native AI Usage and Paper Market navigation in the normal HUD and
school Applications menu. Both panels are lazy mounted in the shared Insights
drawer. The paper component and ledger are maintained by the separate market
session; this integration provides its host, navigation and typed read-only proxy.

## Checked behavior

- AI Usage preserves installed ccusage 20.0.26 default-pricing data, estimated USD
  labels, authoritative token totals, unknown model names, unavailable model totals,
  category attribution, exact source timestamps and Singapore timezone context.
- Daily/weekly/monthly/all-time controls, daily graphs, exact-data disclosures,
  application breakdown and model table. No repricing or inferred missing totals.
- Visible-only, cancellable requests; no panel polling while closed or inactive.
  Stale same-range data stays labeled, and offline/new-range states do not show zero
  usage or values from a previous range.
- Keyboard tabs, native disclosure summaries, Escape, launcher focus restoration,
  school expansion and suit handoff. Shared HUD inert state and native focus leases
  survive out-of-order replies. Native mode signals own geometry immediately.
- Fixed-loopback authenticated bridge routes reject unknown/duplicate parameters,
  arbitrary destinations, redirects, malformed/nonfinite JSON and oversized bodies.
  The 8-second total deadline includes trickled headers and body, with watchdog
  cleanup. Source response bytes and status are preserved, with `Cache-Control:
  no-store`.

## Results

- **61 Node tests passed**:
  `node --test tests/insights-controller.test.cjs tests/hud-overlay.test.cjs tests/suit-controller.test.cjs tests/bridge-poll.test.cjs tests/ai-usage.test.cjs`
  (run from `frontend`).
- **174 Python tests passed**:
  `uv run --no-sync pytest -q tests/test_bridge.py tests/test_bridge_polling.py tests/test_bridge_execution.py tests/test_insights_proxy.py`.
- Frontend TypeScript (`pnpm exec tsc --noEmit --incremental false`), scoped ESLint,
  Prettier, new Python Ruff checks and `git diff --check` passed.
- A copied frontend in `/tmp/jarvis-insights-dev-LnQM8K` passed `pnpm exec next build`.
  No package build script was used and nothing was copied to `shell/ui`. Build
  reported the existing unrelated audio-visualizer unused-variable/dependency
  warnings; no build failures.
- Headless static-export integration passed at 1600px, 520px and school mode:
  `uv run --no-sync python frontend/tests/insights-smoke.py --url http://127.0.0.1:4061`.
  Each case had zero browser errors and covered all ranges, exact synthetic totals,
  disclosure focus, the inactive Paper Market tab, offline states, no closed-panel
  requests, and native IPC mocks. The school case also opened/closed the suit from
  Insights without collapsing the surface during the handoff.
- Read-only live tracker parsing and proxy smoke passed for all four ranges. Only
  schema/source/version/pricing-mode booleans were logged; no real usage snapshot
  or screenshot was added to the repository.
- Existing suit browser suite passed normal 1600px, school, 520px, DPR 2 and
  expanded-school reload cases:
  `uv run --no-sync python frontend/tests/suit-diagnostics-smoke.py --url http://127.0.0.1:4061 --output /tmp/jarvis-insights-suit-regression`.
  Keyboard/presets/Escape, lazy scene assets, visibility/context recovery and
  zero pending renderer work after close remained intact. Reload returned to
  the 64px taskbar with diagnostics closed and no browser errors.

Synthetic browser screenshots and result JSON: `/tmp/jarvis-insights-review`.
Suit regression reports and synthetic screenshots: `/tmp/jarvis-insights-suit-regression`.
Static-export server and any browser processes are disposable test resources.
Physical desktop interaction, production activation, live bridge restart, commits
and pushes are intentionally left to the coordinator.

## Rapid-cancellation follow-up

The independent reviewer reproduced collapse→expand signals arriving before React
committed a transitioning render. Both overlay controllers now treat `expand` as
explicit cancellation. A mode-signal generation guard prevents cancelled native
resize/focus work from running after that latch resets, including acknowledgements
that resolve before React rerenders. Existing committed transitions still block
opening until complete. Stale initial school snapshots cannot reclaim geometry
after a native cancellation.

- **83 related frontend tests passed**, including **42 controller tests** and 13
  new cancellation regressions:
  `node --test tests/insights-controller.test.cjs tests/hud-overlay.test.cjs tests/suit-controller.test.cjs tests/bridge-poll.test.cjs tests/ai-usage.test.cjs tests/paper-market.test.cjs`.
- The independent `/tmp/jarvis-insights-rapid-cancel-review.cjs` reproduction now
  reports `visible: true` for both controllers after batched collapse→expand.
- TypeScript, scoped ESLint, source Prettier and diff checks pass. This follow-up
  changes only the two frontend controllers, their tests and integration/QA docs.
  Backend files retain the independent review's hashes. Earlier browser/build
  results above predate this narrow controller fix; no production activation or
  further backend test run was performed here.
- Updated source and test hashes:
  `/tmp/jarvis-insights-rapid-cancel-refreeze-2026-10-02.json`.
