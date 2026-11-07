# Accelerated historical replay

`./jarvis_new/scripts/paper-market replay --seed 42 --duration-hours 3` replays
30 calendar days in roughly three hours (the last inference may add up to 90 seconds).
This is historical replay, not future live prices. Seed 42 selects December 1–30,
2016: 21 observed AAPL trading sessions. Weekends and holidays contain no invented
bars. The seed selects a past month and per-session adverse slippage of 5, 10 or
20 basis points. Prices themselves are never randomized. Starting cash is $1,000;
positions are long-only, limited to 35% of equity.

The exact requested OpenRouter model uses the existing server-side key resolver
(`OPENROUTER_API_KEY`, private Jarvis keys file, or existing OpenCode credentials).
It has no tools and no fallback model. Model identity and proposal schema are
validated. Only the prior 21 observed daily bars are sent; proposals execute at
the next session open and are marked at that close. Daily replay uses a different
cadence from the existing live intraday strategy.

The paper-market UI shows a separate replay card with progress, P&L, decision
score, full-investment buy-and-hold return, drawdown and unscored errors. An action
is correct when it beats leaving existing holdings unchanged over that session,
after slippage. Cash holds pass when a new long would not profit after slippage;
stock holds pass when that session rises. This is a hindsight outcome score and
cannot establish whether the reasoning was correct. Failed/invalid proposals do
not trade or receive a decision score. Open positions are valued at the latest
observed close, without an invented liquidation. No dividends, taxes or market
impact are included. The model may have memorized this historical period.

The replay runs separately from the expired/live ledger and never resets its
balance or expiry. Every session atomically writes `replay-latest.json` in the
private state directory and updates `runtime.historical_replay` for the existing
read-only UI bridge. A separate process lock prevents duplicate replay writers.
Logs: `journalctl --user -u jarvis-paper-replay.service`. The one-shot process
requires the computer to remain running; it is not a recurring scheduler.

Data: committed `data/paper-market/aapl-2015-2017.csv`, downloaded from
[Plotly's historical finance dataset](https://raw.githubusercontent.com/plotly/datasets/master/finance-charts-apple.csv).
Only dated raw OHLCV columns are used; precomputed chart indicators and adjusted
prices are excluded. The report includes the source and a SHA-256 of normalized
input rows. Test mode can run immediately with `--duration-hours 0`.

---

# Jarvis paper-market experiment

This is a local intraday paper exchange using timestamped Yahoo Finance quotes and minute bars. It does
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
performs one bounded, no-trade live inference per model/effort configuration and
records the result; repeating it returns `duplicate`. This is the only inference
permitted outside regular market hours. `run-once` is the explicit manual trading
entrypoint, with the same lock, freshness, expiry and five-minute deduplication as
the automatic service. Interrupted decisions are not retried in their slot.

The existing `jarvis-paper-market.service` now owns automatic trading. Enable it
explicitly with `./jarvis_new/scripts/paper-market enable-auto`; suspend all
automatic decisions **and risk exits** with `pause-auto`. This switch persists in
the existing ledger and defaults to paused for new installations. Manual
`run-once` remains explicit even while the automatic service is paused.

While enabled, the service checks for a decision every five minutes during the
regular session and checks protective exits each minute. All paths share one
process lock and atomic ledger; inference can briefly defer a risk check. The
separate Codex heartbeat only monitors the service and reports completion. It
must respect the persisted pause switch and must not run a second decision
scheduler. Laptop sleep prevents local work; missed decisions are not backfilled.

The dashboard refreshes marks every minute while open and every 15 minutes while
closed. At expiry it stops fetching, inference and fills, retaining the frozen
ledger. Inspect with `systemctl --user status jarvis-paper-market.service`.

## Intraday evidence and cost controls

The feed scrapes Yahoo Finance's public JSON chart data: timestamped last-trade
prices and actual one-minute OHLCV bars. It excludes incomplete, invalid and
out-of-session bars, never fills gaps, and requires 16 consecutive recent bars
before an entry. Code computes 5-/15-minute momentum, session VWAP and range.
Raw observed bars stay in the local decision audit; the model receives compact
computed features, current holdings/cash and two short recent rationales.

Scraping, signal calculation, risk checks and accounting use **no model tokens**.
No supporting model is needed. **`dots-studio/dots-3-note-preview:free` through OpenRouter** only chooses the
final buy/sell/hold proposal and sizing. With no holdings, it is invoked only
when at least one symbol has positive 5-/15-minute momentum above VWAP. These
are candidate technical setups, not predicted profits or mandatory trades. It
can still choose to hold. No calls occur for closed/expired sessions, paused
automation, stale/missing quotes, insufficient bars or unchanged bar/account
inputs. Failed model attempts are not repeated in the same slot; new evidence
in later slots can retry. The proposal timeout is 45 seconds.

Code attempts paper-only protective sales at a 1% loss or 2% gain relative to
average cost, and attempts to flatten in the last ten minutes of the session.
New buys are blocked in the final thirty minutes. These exits are explicitly
logged as `risk_exit` from `deterministic-risk-policy`, with no model inference.
Fresh quote and atomic fill requirements still apply: a feed outage, process
pause or laptop sleep can prevent an exit, and the service never fabricates a
close or backdates a fill. All trade outcomes remain simulated and uncertain.

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
runtime model is fixed to **`gpt-6-astra` with `medium` effort**; there is no fallback.
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
identifiers are unique per experiment and five-minute slot; fill identifiers are
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
| `runtime` | `last_cycle_at`, `last_cycle_status`, `last_error`, `last_refresh_at`, `feed`, `fill_policy`, `decision_interval_seconds`, `autotrading_enabled`, `scheduler`, `risk_interval_seconds`, optional preflight/maintenance status. |

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
Use `pnpm exec next build` for build-only QA. The `pnpm build` script also deploys
the export into the live desktop shell; use it when applying an authorized UI update.

- [OpenAI app-server protocol](https://learn.chatgpt.com/docs/app-server)
- [GPT-6 Astra model](https://developers.openai.com/api/docs/models/gpt-6-astra)
- [NYSE holidays and trading hours](https://www.nyse.com/trade/hours-calendars)
- [Yahoo Finance exchanges and data delays](https://ca.help.yahoo.com/kb/finance-for-web/exchanges-data-providers-yahoo-finance-sln2310.html)
- [Alpaca paper-trading documentation](https://docs.alpaca.markets/us/docs/paper-trading)

A future supported brokerage-paper adapter would require a separate explicit
setup, paper-only endpoint validation, paper credentials, and reconciliation
against that provider's simulated fills. No such adapter is enabled here.
