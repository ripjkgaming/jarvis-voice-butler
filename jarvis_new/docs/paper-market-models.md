# Paper Market model routing audit

## Frozen trading runtime

Paper Market trading inference is fixed to `gpt-6.1-sol` with `medium` effort.
The Codex Trader worker verifies the installed CLI version, authenticated account,
catalog entry, supported effort, effective provider/model/effort, and settings
updates before accepting a proposal. A reroute or incompatible setting fails
closed. Package metadata now reports the same model and effort.

TradingAgents is installed at the default
`~/tradingagents-sim/TradingAgents` path. Its `default_config.py` supplies
`gpt-6-sol` for deep reasoning and `gpt-6-luna` for quick reasoning, and accepts
`TRADINGAGENTS_*` environment overrides. The paper adapter overrides both tiers,
their providers and backend URLs, and reasoning effort on each graph creation.
Its Codex client also ignores tier names passed by the library and always invokes
the CLI with `-m gpt-6.1-sol` and `model_reasoning_effort=medium`. Thus analyst,
researcher, debate, trader, and risk graph roles routed through either tier use
the fixed setting.

The Codex CLI adapter uses the installed CLI and its existing authenticated login;
it invokes isolated `codex exec` calls in temporary directories. Each invocation
has a fixed model, medium effort, and a hard 600-second overall retry deadline.
The paper worker's app-server protocol additionally retains explicit effective
model metadata checks.

## Conflicts found outside this adapter

- The separate Codex paper worker was previously pinned to `gpt-6-astra`; it now
  uses the requested `gpt-6.1-sol`/medium pair.
- `src/paper_market/__init__.py` previously exposed Dots/`default` as the ledger
  metadata fallback; this is now fixed to Sol/medium.
- The installed TradingAgents defaults use different model names for deep and
  quick tiers. The paper adapter overrides them at construction time, including
  provider-tier overrides.
- The deployed TradingAgents systemd unit reportedly sets
  `PAPER_MARKET_MODEL=tradingagents:gpt-6-sol/luna` and
  `PAPER_MARKET_EFFORT=multi-agent`. The unit file is outside this change's scope
  and must be updated by the service integrator; the current runner no longer
  consumes those variables.
- `src/system/codex_worker.py` retains generic Luna/low defaults for the separate
  computer-control worker. The paper worker explicitly supplies and validates
  Sol/medium, so changing shared computer-control defaults is neither needed nor
  appropriate for this isolated paper-market fix.

## TradingAgents paper adapter

`review_portfolio(ledger, news=[])` performs one bounded review against the
provided bot ledger. Shared screened news is included in each relevant symbol's
TradingAgents portfolio context. Article ID and content changes produce a new
daily signal cache key, so substantive revisions trigger reassessment. Proposals
still flow through the existing rating targets, fresh quote fetch, and common
paper ledger checks. The current strategy retains its 30% Buy / 20% Overweight /
5% Underweight targets and 5% swing stop. `readiness()` performs authenticated
CLI/model setup checks without model inference or trades.

The live CLI/model readiness check must be reported separately from model
inference: it confirms access and configuration only. No measured experiment is
started by that check.
