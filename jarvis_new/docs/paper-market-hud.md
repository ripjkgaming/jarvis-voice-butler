# Paper Market comparison HUD

The HUD continues to accept the original `schema_version: 1` single-ledger snapshot. The service may optionally add a top-level `comparison` object. When absent, the original single-account view renders unchanged.

`comparison` contains a shared experiment `id`, `status`, nullable `started_at` and `expires_at`, frozen `settings`, `readiness`, shared `news` status, `portfolios`, `benchmark`, `equity_history`, and `gaps`. Nullable times allow prelaunch readiness states to render without inventing a start or deadline. Blocked readiness is shown as a state, not a failure to parse.

Each portfolio has `id`, `name`, `strategy`, optional `usage_cost_usd`, and its independent Ledger snapshot. The ledger may be nested under `snapshot` or spread alongside the portfolio identity. Existing ledger fields supply starting cash, equity, return, cash, holdings, realized and unrealized P/L, decisions, fills, quote marks, and runtime model metadata. Optional account fields `invested_value_usd`, `maximum_drawdown_pct`, and `trade_count` enrich the comparison card. Missing values remain visibly unknown.

The shared chart plots each portfolio's complete equity history and the benchmark's `equity_history` (each point has `at` and `equity_usd`). It does not manufacture observations. Per-portfolio inspection includes decisions, errors, fills, holdings, and the shared news/feed metadata. Usage cost is displayed separately from simulated return. UI dates use Singapore locale/time formatting.
