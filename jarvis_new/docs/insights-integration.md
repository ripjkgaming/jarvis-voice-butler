# Jarvis Insights integration

The HUD and school Applications menu open a shared native-styled Insights drawer.
AI Usage and Paper Market mount only while selected; closing the drawer unmounts
all feature requests. Suit diagnostics takes precedence over Insights.

## Component contract

- `frontend/components/markets/PaperTradingPanel.tsx`: named `PaperTradingPanel` export, called with
  `embedded` and `onClose: () => void`. Render flow content inside the drawer,
  not another fixed overlay. Fetch `bridgePaperMarket({ signal })`, a typed wrapper
  for `bridgeGet('/paper-market', 15000, { signal })`.
- `frontend/components/insights/AiUsagePanel.tsx`: default export, no props.
- Drawer owns dialog semantics, focus trapping, Escape, tabs, and native school
  expansion. Panels own scrollable contents and abortable visible-only fetching.

## Read-only API contract

- `/insights/usage?range=today|week|month|all&timezone=Asia%2FSingapore` forwards
  `http://127.0.0.1:42731/api/dashboard` with fixed `source=ccusage`. Preserve the
  response's source/pricing metadata, source timestamps, range, totals, warnings,
  unknown model attribution, and estimates. Never recalculate prices.
- `/paper-market` forwards `http://127.0.0.1:8767/api/paper-market` unchanged.
- Both routes use the existing bridge authentication. Fixed destinations, bounded
  responses and deadlines; no mutations, caller-selected targets, or redirects.

ccusage dashboard keys: `source_mode`, `source_info`, `updated_at`, `timezone`,
`range`, `summary`, `apps`, `models`, `timeline`, `warnings`, `accounting_note`.
Authoritative `total_tokens` may differ from attributable input/output/cache
subtotals; model `total_tokens` may be unavailable. Costs remain estimated USD.

Activation and any production export/service restart belong to the coordinator.
Development validation uses disposable servers with mocked native IPC and data.

## Lifecycle

`useInsights` captures the launcher before making the covered HUD inert. The
drawer restores focus on close, traps Tab (including native disclosure summaries),
and handles Escape without hiding the shell. Switching tabs unmounts the previous
panel. AI usage requests abort on close, range changes, or document hiding; its
visible refresh cadence is 60 seconds. Failed refreshes retain only that range's
previous report and label it stale. A new range never displays another range's
values.

The suit and Insights controllers share reference-counted HUD coverage and the
existing mount-time native focus reset. Each school overlay releases only its own
`suit_focus` lease. The suit takes precedence, and a handoff skips Insights' native
collapse. Native entry/return signals remain latched before React receives the
transition prop. An explicit `expand` cancels that latch even when rapid
collapse→expand signals skip the transitioning render entirely; an already
committed transition still blocks opening until it ends. Each native signal also
invalidates the preceding overlay's resize/focus work, so cancellation cannot
release the latch and then allow an old cleanup to collapse the native window.
No Rust changes are required.

## Validation

See `benchmarks/insights-qa-2026-10-02.md` for commands, evidence locations and
activation boundaries. Screenshots and browser fixtures contain synthetic data.
