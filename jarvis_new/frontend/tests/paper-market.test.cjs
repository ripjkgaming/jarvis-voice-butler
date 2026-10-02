const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

function compile(file, dependencies = {}, globals = {}) {
  const scope = {
    exports: {},
    Date,
    Intl,
    ...globals,
    require(name) {
      if (name in dependencies) return dependencies[name];
      if (name.startsWith('react')) return require(name);
      throw new Error(`Unexpected dependency: ${name}`);
    },
  };
  vm.runInNewContext(
    ts.transpileModule(fs.readFileSync(path.join(__dirname, file), 'utf8'), {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
        esModuleInterop: true,
      },
    }).outputText,
    scope
  );
  return scope.exports;
}
const lib = compile('../lib/paper-market.ts');
const { PaperTradingPanel } = compile('../components/markets/PaperTradingPanel.tsx', {
  '@/lib/paper-market': lib,
  '@/lib/bridge': {
    bridgePaperMarket() {
      throw new Error('Static fixtures must not fetch');
    },
  },
  './paper-trading-panel.module.css': {
    __esModule: true,
    default: new Proxy({}, { get: (_, key) => key }),
  },
});

const observed = (at, equity = '1000.00', complete = true) => ({
  at,
  equity_usd: equity,
  total_pnl_usd: equity === null ? null : String(Number(equity) - 1000),
  cash_usd: '1000.00',
  valuation_complete: complete,
});
function fixture() {
  return {
    schema_version: 1,
    experiment: {
      id: 'qa',
      started_at: '2026-10-02T00:00:00Z',
      expires_at: '2026-10-09T00:00:00Z',
      initial_cash_usd: '1000.00',
      status: 'active',
      model: 'gpt-6-astra',
      effort: 'high',
    },
    account: {
      cash_usd: '1000.00',
      equity_usd: '1000.00',
      total_pnl_usd: '0.00',
      total_return_pct: '0.00',
      realized_pnl_usd: '0.00',
      unrealized_pnl_usd: '0.00',
      valuation_complete: true,
    },
    market: {
      is_open: false,
      next_open_at: '2026-10-02T13:30:00Z',
      session_close_at: null,
      status: 'closed',
    },
    quotes: [],
    holdings: [],
    equity_history: [],
    decisions: [],
    fills: [],
    runtime: {
      last_cycle_at: null,
      last_cycle_status: 'not_started',
      last_error: null,
      last_refresh_at: '2026-10-02T00:00:00Z',
      feed: 'Yahoo Finance (unofficial)',
      fill_policy: 'Simulated fills at recorded eligible quotes.',
      decision_interval_seconds: 1800,
    },
  };
}
const render = (snapshot) =>
  renderToStaticMarkup(React.createElement(PaperTradingPanel, { embedded: true, snapshot }));

test('null, malformed and unavailable snapshots never become an account', () => {
  for (const invalid of [
    null,
    {},
    { ok: false },
    { ...fixture(), account: {} },
    { ...fixture(), schema_version: 2 },
  ]) {
    assert.equal(lib.parsePaperMarketSnapshot(invalid), null);
  }
  const snap = fixture();
  snap.account.equity_usd = '';
  assert.equal(lib.parsePaperMarketSnapshot(snap), null);
  assert.equal(lib.money(null), 'Unknown');
  assert.equal(lib.money(''), 'Unknown');
  assert.equal(lib.money('NaN'), 'Unknown');
  assert.equal(lib.money('0.00', true), '$0.00');
});

test('fresh cash-only account does not invent price history or holdings', () => {
  const snap = fixture();
  assert.equal(lib.parsePaperMarketSnapshot(snap), snap);
  const html = render(snap);
  assert.match(html, /No complete valuations recorded yet/);
  assert.match(html, /No open positions/);
  assert.match(html, /No decisions recorded/);
  assert.match(html, /No fills yet/);
  assert.match(html, /Astra/);
  assert.match(html, /high/);
  assert.doesNotMatch(html, /<polyline|<circle/);
});

test('one actual observation renders one point with no invented line', () => {
  const snap = fixture();
  snap.equity_history = [observed('2026-10-02T00:00:00Z')];
  const html = render(snap);
  assert.equal((html.match(/<circle/g) || []).length, 1);
  assert.doesNotMatch(html, /<polyline/);
  assert.match(html, /A trend needs more observations/);
  const chart = lib.equityChart(snap.equity_history, 'equity_usd');
  assert.equal(chart.count, 1);
  assert.equal(chart.segments[0][0].value, 1000);
});

test('history sorts by observation time and gaps break the graph', () => {
  const points = [
    observed('2026-10-02T03:00:00Z', '1050.00'),
    observed('2026-10-02T00:00:00Z'),
    observed('2026-10-02T01:00:00Z', null, false),
    observed('2026-10-02T02:00:00Z', '1010.00'),
    observed('not a date', '500.00'),
  ];
  const chart = lib.equityChart(points, 'total_pnl_usd');
  assert.equal(chart.count, 3);
  assert.equal(chart.segments.length, 2);
  assert.equal(chart.segments[0].length, 1);
  assert.equal(chart.segments[1].length, 2);
  assert.equal(chart.segments[0][0].value, 0);
  assert.equal(chart.segments[1][1].value, 50);
  assert.equal(chart.segments[1][0].x, 476);
});

test('incomplete valuation remains unknown while known cash is displayed', () => {
  const snap = fixture();
  snap.account = {
    ...snap.account,
    cash_usd: '700.00',
    equity_usd: null,
    total_pnl_usd: null,
    total_return_pct: null,
    unrealized_pnl_usd: null,
    valuation_complete: false,
  };
  snap.holdings = [
    {
      symbol: 'TEST',
      quantity: '2',
      average_cost_usd: '150.00',
      mark_price_usd: null,
      market_value_usd: null,
      unrealized_pnl_usd: null,
      quote_at: null,
    },
  ];
  snap.quotes = [
    {
      symbol: 'TEST',
      price_usd: null,
      quoted_at: null,
      age_seconds: null,
      source: 'Yahoo Finance (unofficial)',
      status: 'unavailable',
      error: 'Quote unavailable',
    },
  ];
  snap.equity_history = [observed('2026-10-02T00:00:00Z', null, false)];
  const html = render(snap);
  assert.match(html, /Valuation incomplete/);
  assert.match(html, /\$700\.00/);
  assert.match(html, /Unknown/);
  assert.match(html, /Quote unavailable/);
  assert.doesNotMatch(html, /<circle|<polyline/);
});

test('last-observed stale marks have explicit valuation context', () => {
  const snap = fixture();
  snap.account.valuation_fresh = false;
  snap.account.marks_as_of = '2026-10-01T20:00:00Z';
  const html = render(snap);
  assert.match(html, /LAST OBSERVED EQUITY/);
  assert.match(html, /Holding marks are stale/);
  assert.match(html, /as of/);
});

test('decision rationale and actual fills render independently', () => {
  const snap = fixture();
  snap.decisions = [
    {
      id: 'decision-1',
      at: '2026-10-02T14:00:00Z',
      status: 'executed',
      rationale: 'Small position within the cash budget.',
      error: null,
      model: 'gpt-6-astra',
      effort: 'high',
      actions: [{ symbol: 'TEST', side: 'buy', notional_usd: '120.00', quantity: null }],
    },
  ];
  snap.fills = [
    {
      id: 'fill-1',
      at: '2026-10-02T14:00:01Z',
      symbol: 'TEST',
      side: 'buy',
      quantity: '1',
      price_usd: '120.00',
      notional_usd: '120.00',
      realized_pnl_usd: '0.00',
    },
  ];
  const html = render(snap);
  assert.match(html, /Small position within the cash budget/);
  assert.match(html, /TEST/);
  assert.match(html, /\$120\.00/);
  assert.doesNotMatch(html, /No fills yet/);
});

test('quote age does not present missing or future timestamps as live', () => {
  const now = Date.parse('2026-10-02T12:00:00Z');
  assert.equal(lib.ageLabel(null, now, null), 'Age unknown');
  assert.equal(lib.ageLabel('2026-10-02T11:00:00Z', now), '1h ago');
  assert.equal(lib.ageLabel('2026-10-02T12:10:00Z', now), 'Timestamp ahead');
});

test('polling exposes failed reads, stops while hidden, and aborts on unmount', async () => {
  const state = [],
    effects = [],
    requests = [],
    timers = new Map(),
    intervals = new Map();
  const listeners = new Map();
  let cursor = 0,
    serial = 0,
    pending = [];
  const document = {
    hidden: false,
    addEventListener(name, fn) {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name).add(fn);
    },
    removeEventListener(name, fn) {
      listeners.get(name)?.delete(fn);
    },
  };
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!(index in state)) state[index] = typeof initial === 'function' ? initial() : initial;
      return [
        state[index],
        (next) => {
          state[index] = typeof next === 'function' ? next(state[index]) : next;
        },
      ];
    },
    useEffect(fn, deps) {
      const index = cursor++,
        previous = effects[index];
      if (!previous || deps.some((dep, n) => !Object.is(dep, previous.deps[n])))
        pending.push(() => {
          previous?.cleanup?.();
          effects[index] = { deps, cleanup: fn() };
        });
    },
    useId() {
      return 'qa';
    },
    useMemo(fn) {
      return fn();
    },
  };
  const { PaperTradingPanel: Panel } = compile(
    '../components/markets/PaperTradingPanel.tsx',
    {
      react: hooks,
      '@/lib/paper-market': lib,
      '@/lib/bridge': {
        bridgePaperMarket(signal) {
          return new Promise((resolve) => requests.push({ signal, resolve }));
        },
      },
      './paper-trading-panel.module.css': { __esModule: true, default: {} },
    },
    {
      document,
      AbortController,
      setTimeout(fn) {
        timers.set(++serial, fn);
        return serial;
      },
      clearTimeout(id) {
        timers.delete(id);
      },
      setInterval(fn) {
        intervals.set(++serial, fn);
        return serial;
      },
      clearInterval(id) {
        intervals.delete(id);
      },
    }
  );
  function renderPanel() {
    cursor = 0;
    pending = [];
    const result = Panel({ embedded: true });
    pending.forEach((fn) => fn());
    return result;
  }
  const flush = () => new Promise((resolve) => setImmediate(resolve));
  function fireTimer() {
    const [id, fn] = timers.entries().next().value;
    timers.delete(id);
    fn();
  }
  renderPanel();
  assert.equal(requests.length, 1);
  assert.equal(timers.size, 0, 'No overlapping timer while a read is in flight');
  const snap = fixture();
  requests[0].resolve(snap);
  await flush();
  assert.equal(state[1], snap);
  assert.equal(timers.size, 1);
  fireTimer();
  requests[1].resolve(null);
  await flush();
  renderPanel();
  assert.equal(state[1], snap, 'Keep the last good snapshot');
  assert.match(state[2], /unavailable/, 'A cached snapshot must not hide failure');
  fireTimer();
  assert.equal(requests.length, 3);
  document.hidden = true;
  listeners.get('visibilitychange').forEach((fn) => fn());
  assert.equal(requests[2].signal.aborted, true, 'Hiding aborts the pending request');
  assert.equal(timers.size, 0);
  assert.equal(intervals.size, 0, 'Hiding pauses the quote-age clock');
  requests[2].resolve(fixture());
  await flush();
  assert.equal(state[1], snap, 'A response arriving after hide is ignored');
  document.hidden = false;
  listeners.get('visibilitychange').forEach((fn) => fn());
  assert.equal(requests.length, 4);
  assert.equal(intervals.size, 1);
  assert.equal(requests[3].signal.aborted, false, 'Resume has a fresh request signal');
  for (const effect of effects) effect?.cleanup?.();
  assert.equal(requests[3].signal.aborted, true);
  assert.equal(timers.size, 0);
  assert.equal(intervals.size, 0);
  requests[3].resolve(fixture());
  await flush();
  assert.equal(timers.size, 0, 'A completed read after teardown must not restart polling');
});

module.exports = { fixture, render };
