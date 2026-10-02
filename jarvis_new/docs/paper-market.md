# Jarvis paper-market experiment

This is a local paper exchange using timestamped Yahoo Finance quotes. It does
not open a brokerage account, accept broker keys, or send brokerage orders. The
experiment begins with exactly USD 1,000 in simulated cash and zero holdings.
Its persisted expiry is exactly 168 hours after initialization; restarting the
service or repeating `init` does not extend the experiment.

## Run and inspect

From the repository root:

```bash
./jarvis_new/scripts/paper-market init
./jarvis_new/scripts/paper-market preflight
./jarvis_new/scripts/paper-market install-service
./jarvis_new/scripts/paper-market run-once
./jarvis_new/scripts/paper-market snapshot
```

The wrapper runs the project through `uv`. `init` is idempotent. `preflight`
performs one bounded, no-trade live inference and records the result; repeating
it returns `duplicate`. This is the only inference permitted outside regular
market hours. `run-once` is the explicit trading entrypoint; it exits without
inference during closed sessions, after expiry, or when that 30-minute decision
slot already exists. A file lock covers quote acquisition, inference, refetch,
validation and commit. An interrupted decision is not retried in its original
slot. A busy lock means another decision or quote refresh is already running;
the scheduler may retry the same command after the current operation completes.

The coordinator configures **one Codex heartbeat every 30 minutes** to invoke:

```text
/mnt/data/jarvis-voice-butler/jarvis_new/scripts/paper-market run-once
```

No decision timer, cron job, or independent automation is installed by this
package. The scheduler should stop invoking the command at the persisted
`experiment.expires_at` value and report completion. It should stay quiet when
the market is closed or nothing actionable changes. Laptop sleep prevents local
work; missed decisions are not backfilled. Neither the dashboard nor service
status implies that the heartbeat itself is installed.

`install-service` enables only `jarvis-paper-market.service` in the user's
systemd instance. It serves dashboard data and refreshes quote marks every
minute while the regular market is open, or every 15 minutes while closed. It
does not invoke a model. At expiry it stops fetching prices and keeps serving the
frozen ledger. To inspect or stop this service:

```bash
systemctl --user status jarvis-paper-market.service
systemctl --user stop jarvis-paper-market.service
```

The private state directory defaults to `~/.jarvis/paper-market` and contains a
SQLite WAL ledger and a process lock. Pass `--state-dir /absolute/private/path`
before a subcommand for an isolated test run. The ledger contains exact decimal
cash and cost basis, holdings, original quote timestamps and receipt timestamps,
model inputs, proposals, rationales, errors, protocol verification, available
token usage, simulated fills, and observed equity history. No authentication
material is copied into the ledger. Back up the database with SQLite's backup
facility, rather than copying only the main file while WAL is active.

## Model and local fill rules

The proposal worker uses the installed authenticated Codex app-server. The
runtime model is fixed to **`gpt-6-astra` with `high` effort**; there is no fallback.
It accepts a bounded JSON context and returns structured proposals. Executable
tools, environments, MCP servers, app connections, plugins and web search are
disabled. It verifies the effective configuration and thread response and
rejects tool requests, model reroutes, changed effort, unexpected protocol items,
invalid output and timeouts. The verified CLI version is currently
`codex-cli 0.159.2`; a different version fails closed until its isolation is
verified. The preflight's completed inference verifies access for that request;
a model catalog alone is not treated as entitlement evidence.

The ledger independently enforces:

- USD equities/ETFs from the bounded SPY, QQQ, AAPL, MSFT, NVDA, AMZN, GOOGL,
  META universe; at most four distinct symbols per proposal.
- Buy sizes as positive decimal USD notional, minimum USD 5; sell sizes as
  positive share quantities. Fractional shares use six decimal places.
- No negative cash, leverage, shorting or selling unowned shares. A buy cannot
  leave that symbol above 35% of equity or total exposure above 95%.
- Every required holding/action quote is refetched after the model responds.
  At commit it must be no more than 120 seconds old, in USD, finite and positive,
  for the requested symbol, not from the future, and from the current regular
  session. Missing data rejects the entire batch.
- Market-open and experiment-deadline checks before inference, after inference,
  after refetch, and immediately before committing. The NYSE schedule uses the
  official 2026–2028 holiday/early-close dates and America/New_York daylight
  saving rules; unsupported calendar years fail closed.
- Sells are simulated first, then buys, at the freshly fetched last-trade price
  with adverse 10-basis-point slippage and zero commission. These are local
  simulated fills, not exchange executions. Dividends, splits/corporate actions,
  taxes and market impact are not modeled.

Cash, shares, fills, model/result metadata and the resulting equity point commit
in one SQLite transaction. Rejected proposals cannot partially fill. Later
status or process-cleanup failures cannot relabel a committed fill. Decision
identifiers are unique per experiment and 30-minute slot; fill identifiers are
derived from the decision and action index.

Quotes are unofficial best-effort data, and may be delayed, throttled or absent.
Current display values use the last observed marks with visible source, age,
freshness and as-of time. Unknown marks remain null, not zero. At expiry, the
service retains the final owned shares and values them using observed quotes
received at or before the cutoff; it does not invent a liquidation or fetch a
later quote to improve the result. Equity history starts at initialization and
contains actual recorded observations only.

## Read-only integration contract

The service listens only on `127.0.0.1:8767`. `GET /health` identifies the paper
service. `GET /api/paper-market` returns the snapshot below. Other routes are
rejected; write methods return 405. Foreign Host headers and browser Origin
headers are rejected. The native UI uses the existing authenticated Jarvis
bridge, which proxies its `/paper-market` route to this fixed upstream.

`frontend/components/markets/PaperTradingPanel.tsx` exports the named
`PaperTradingPanel` component with optional `embedded`, `onClose` and `snapshot`
props. Its wire type and runtime parser are in `frontend/lib/paper-market.ts`.
The shared Insights drawer mounts only its selected tab. The paper panel uses
`bridgePaperMarket` with an abort signal, and cancels its reads on unmount or
when hidden. Its optional `snapshot` prop supports isolated fixture-based QA.

Snapshot schema version 1 uses **decimal strings** for money and quantities,
ISO-8601 UTC timestamps, and null for unavailable amounts/timestamps:

| Key | Contents |
| --- | --- |
| `schema_version`, `generated_at` | Version `1` and snapshot time. |
| `experiment` | `id`, `started_at`, `expires_at`, `initial_cash_usd`, `status`, `model`, `effort`. |
| `account` | `cash_usd`, nullable `equity_usd`, `total_pnl_usd`, `total_return_pct`, `unrealized_pnl_usd`; `realized_pnl_usd`, `valuation_complete`, `valuation_fresh`, nullable `marks_as_of`, `valuation_basis`. |
| `market` | `is_open`, `status`, nullable `next_open_at`, `session_close_at`. |
| `quotes[]` | `symbol`, nullable `price_usd`, `quoted_at`, `age_seconds`, `previous_close_usd`; `source`, `status`, nullable `error`. |
| `holdings[]` | `symbol`, `quantity`, `average_cost_usd`, `cost_basis_usd`; nullable `mark_price_usd`, `market_value_usd`, `unrealized_pnl_usd`, `quote_at`. |
| `equity_history[]` | `at`, `cash_usd`, nullable `equity_usd`, `total_pnl_usd`, `valuation_complete`. |
| `decisions[]` | Latest 30: `id`, `at`, `completed_at`, `kind`, `status`, nullable `rationale`, `error`; `model`, `effort`, `actions`, nullable `usage`, `verification`. Full inputs remain in SQLite. |
| `fills[]` | Latest 100: `id`, `decision_id`, `at`, `symbol`, `side`, `quantity`, `price_usd`, `notional_usd`, `realized_pnl_usd`, original `quote` provenance. |
| `runtime` | `last_cycle_at`, `last_cycle_status`, `last_error`, `last_refresh_at`, `feed`, `fill_policy`, `decision_interval_seconds`, optional preflight/maintenance status. |

Every action has exactly `symbol`, `side: "buy" | "sell"`, `notional_usd`, and
`quantity`, with the unused sizing field null. An empty action list means hold.
The service does not expose a trade mutation endpoint.

## Validation and sources

```bash
cd jarvis_new
uv run pytest tests/test_paper_market_*.py
uv run ruff check src/paper_market tests/test_paper_market_*.py
cd frontend
node --test tests/paper-market.test.cjs
pnpm exec tsc --noEmit
```

Tests use private temporary ledgers, fake quotes/clocks and fake app-server
messages. They do not use live model inference or real trading. The separately
invoked preflight is auditable and intentionally limited to one live proposal.
Do not run `pnpm build` for QA: the existing build script exports into the live
desktop shell.

- [OpenAI app-server protocol](https://learn.chatgpt.com/docs/app-server)
- [GPT-6 Astra model](https://developers.openai.com/api/docs/models/gpt-6-astra)
- [NYSE holidays and trading hours](https://www.nyse.com/trade/hours-calendars)
- [Yahoo Finance exchanges and data delays](https://help.yahoo.com/kb/finance/article-exchanges-data-delays-sln2310.html)
- [Alpaca paper-trading documentation](https://docs.alpaca.markets/us/docs/paper-trading)

A future supported brokerage-paper adapter would require a separate explicit
setup, paper-only endpoint validation, paper credentials, and reconciliation
against that provider's simulated fills. No such adapter is enabled here.
