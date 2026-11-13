const assert = require('node:assert/strict');
const test = require('node:test');
const { fixture, render } = require('./paper-market.test.cjs');

test('comparison snapshot renders independent cards, readiness, costs and benchmark context', () => {
  const base = fixture();
  const portfolio = (id, name, equity) => ({
    ...base,
    experiment: {
      ...base.experiment,
      id,
      initial_cash_usd: '1000.00',
      started_at: '',
      expires_at: '',
    },
    account: {
      ...base.account,
      cash_usd: equity,
      equity_usd: equity,
      total_pnl_usd: String(Number(equity) - 1000),
      total_return_pct: String(Number(equity) - 1000),
      invested_value_usd: '0.00',
      maximum_drawdown_pct: '0.00',
      trade_count: 0,
    },
  });
  base.comparison = {
    id: 'shared-week',
    status: 'blocked',
    started_at: null,
    expires_at: null,
    settings: {},
    readiness: { status: 'blocked', reason: 'waiting for sources' },
    news: { status: 'connected' },
    portfolios: [
      {
        id: 'codex',
        name: 'Codex Trader',
        strategy: 'Independent decisions',
        snapshot: portfolio('codex', 'Codex Trader', '1000.00'),
        usage_cost_usd: '0.12',
      },
      {
        id: 'copy',
        name: 'Trader Copy',
        strategy: 'Verified copies',
        snapshot: portfolio('copy', 'Trader Copy', '1010.00'),
        usage_cost_usd: '0.03',
      },
      {
        id: 'bot',
        name: 'Your Trading Bot',
        strategy: 'Existing bot',
        snapshot: portfolio('bot', 'Your Trading Bot', '990.00'),
        usage_cost_usd: '0.06',
      },
    ],
    benchmark: {
      symbol: 'SPY',
      equity_history: [
        { at: '2026-11-01T00:00:00Z', equity_usd: '1000.00' },
        { at: '2026-11-02T00:00:00Z', equity_usd: '1005.00' },
      ],
    },
    equity_history: [],
    gaps: [],
  };
  const html = render(base);
  for (const text of [
    'shared-week',
    'Codex Trader',
    'Trader Copy',
    'Your Trading Bot',
    'blocked',
    'SPY',
    'Usage cost',
    '$0.12',
  ])
    assert.ok(html.includes(text), `missing ${text}`);
  const directEnvelope = { comparison: base.comparison };
  assert.match(render(directEnvelope), /Your Trading Bot/);
});
