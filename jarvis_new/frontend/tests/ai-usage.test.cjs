// Run from frontend: node --test tests/ai-usage.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

function load(file, dependencies, globals = {}) {
  const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, '..', file), 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  const scope = {
    exports: {},
    require(name) {
      assert.ok(name in dependencies, `Unexpected dependency: ${name}`);
      return dependencies[name];
    },
    ...globals,
  };
  vm.runInNewContext(compiled, scope, { filename: file });
  return scope.exports;
}

async function flush() {
  for (let i = 0; i < 20; i++) await Promise.resolve();
}
function clock() {
  let now = 0;
  let next = 0;
  const timers = new Map();
  return {
    timers,
    Date: class extends Date {
      static now() {
        return now;
      }
    },
    setTimeout(fn, delay) {
      const id = ++next;
      timers.set(id, { at: now + delay, fn });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    async advance(ms) {
      await flush();
      const end = now + ms;
      for (;;) {
        const due = [...timers].sort((a, b) => a[1].at - b[1].at)[0];
        if (!due || due[1].at > end) break;
        now = due[1].at;
        timers.delete(due[0]);
        due[1].fn();
        await flush();
      }
      now = end;
      await flush();
    },
  };
}
function fixture(bridgeGet, hidden = false) {
  const time = clock();
  const listeners = new Set();
  const document = {
    hidden,
    addEventListener(name, fn) {
      assert.equal(name, 'visibilitychange');
      listeners.add(fn);
    },
    removeEventListener(name, fn) {
      assert.equal(name, 'visibilitychange');
      listeners.delete(fn);
    },
    setHidden(value) {
      this.hidden = value;
      listeners.forEach((fn) => fn());
    },
  };
  const client = load(
    'lib/ai-usage.ts',
    { '@/lib/bridge': { bridgeGet } },
    { ...time, document, AbortController }
  );
  return { ...client, ...time, document, listeners };
}
function metrics(overrides = {}) {
  return {
    total_tokens: 1024,
    estimated_cost_usd: 0.0123456789012345,
    input_tokens: 100,
    output_tokens: 200,
    cache_read_tokens: 700,
    cache_write_tokens: 0,
    reported_token_subtotal: 1000,
    unattributed_tokens: 24,
    missing_pricing: false,
    ...overrides,
  };
}
function report(range = 'today', overrides = {}) {
  return {
    source_mode: 'ccusage',
    updated_at: '2026-10-02T00:20:00.123456Z',
    timezone: 'Asia/Singapore',
    range: {
      key: range,
      label: 'Today',
      start: '2026-10-01T16:00:00Z',
      end: '2026-10-02T16:00:00Z',
    },
    source_info: {
      name: 'ccusage',
      version: '20.0.26',
      pricing_mode: 'default',
      timezone: 'Asia/Singapore',
      status: 'ready',
      snapshot_at: '2026-10-02T00:19:58.998Z',
    },
    summary: metrics(),
    apps: [{ name: 'Codex', ...metrics() }],
    models: [
      {
        name: 'unrecognized/future-model:variant',
        app: 'Codex',
        ...metrics({ total_tokens: null, unattributed_tokens: null, missing_pricing: true }),
      },
    ],
    timeline: [{ date: '2026-10-02', ...metrics() }],
    warnings: ['Missing model prices remain unknown.'],
    ...overrides,
  };
}

test('valid reports preserve exact costs, totals, timestamps, null model totals and unknown names', () => {
  const f = fixture();
  const source = report();
  const before = JSON.stringify(source);
  assert.equal(f.isUsageDashboard(source, 'today'), true);
  assert.equal(JSON.stringify(source), before);
  assert.equal(source.summary.estimated_cost_usd, 0.0123456789012345);
  assert.equal(source.summary.total_tokens, 1024);
  assert.equal(source.models[0].total_tokens, null);
  assert.equal(source.models[0].name, 'unrecognized/future-model:variant');
  assert.equal(f.exactNumber(source.summary.estimated_cost_usd), '0.0123456789012345');
  assert.equal(f.tokenCount(null), 'Unavailable');
  assert.equal(f.estimatedCost(null), 'Unavailable');
  assert.equal(f.estimatedCost(0), '$0.00');
  assert.equal(f.estimatedCost(0.0001), '<$0.01');
  assert.equal(f.usagePath('month'), '/insights/usage?range=month&timezone=Asia%2FSingapore');
});

test('rejects wrong-range, wrong-source, malformed or mismatched timezone reports without coercion', () => {
  const f = fixture();
  for (const value of [
    null,
    {},
    report('week'),
    report('today', { source_mode: 'supplemental' }),
    report('today', { timezone: 'UTC' }),
    report('today', { summary: metrics({ total_tokens: '1024' }) }),
    report('today', { summary: metrics({ total_tokens: NaN }) }),
    report('today', { summary: metrics({ estimated_cost_usd: undefined }) }),
    report('today', { models: [{ ...metrics(), name: {} }] }),
    report('today', { timeline: [{ ...metrics(), date: '2026-02-31' }] }),
    report('today', { timeline: [{ ...metrics(), date: '2026-99-99' }] }),
    report('today', { pricing: { note: {} } }),
  ])
    assert.equal(f.isUsageDashboard(value, 'today'), false);
});

test('polling retains only the same-range report on failures, labels stale and recovers', async () => {
  const first = report();
  const last = report('today', { summary: metrics({ total_tokens: 2048 }) });
  const responses = [first, null, report('week'), last];
  const requests = [];
  const f = fixture(async (...args) => {
    requests.push(args);
    return responses.shift();
  });
  const states = [];
  const stop = f.subscribeUsage('today', (state) => states.push(state));
  await f.advance(0);
  assert.equal(states.at(-1).data, first);
  assert.equal(states.at(-1).phase, 'ready');
  assert.equal(requests[0][1], 15000);
  assert.ok(requests[0][2].signal instanceof AbortSignal);
  await f.advance(59999);
  assert.equal(requests.length, 1);
  await f.advance(1);
  assert.equal(states.at(-1).data, first);
  assert.equal(states.at(-1).phase, 'stale');
  await f.advance(60000);
  assert.equal(states.at(-1).data, first);
  assert.equal(states.at(-1).phase, 'stale');
  await f.advance(60000);
  assert.equal(states.at(-1).data, last);
  assert.equal(states.at(-1).phase, 'ready');
  stop();
  assert.equal(f.timers.size, 0);
  assert.equal(f.listeners.size, 0);
});

test('initial source failure never emits manufactured zero totals', async () => {
  const f = fixture(async () => {
    throw new Error('offline');
  });
  const states = [];
  const stop = f.subscribeUsage('week', (state) => states.push(state));
  await f.advance(0);
  assert.equal(states.at(-1).phase, 'error');
  assert.equal(states.at(-1).data, null);
  stop();
});

test('a stale source snapshot keeps its original capture timestamp and estimated costs', async () => {
  const data = report();
  data.source_info.status = 'stale';
  const f = fixture(async () => data);
  let state;
  const stop = f.subscribeUsage('today', (next) => {
    state = next;
  });
  await f.advance(0);
  assert.equal(state.phase, 'stale');
  assert.equal(state.data, data);
  assert.equal(state.data.source_info.snapshot_at, '2026-10-02T00:19:58.998Z');
  stop();
});

test('hidden panels do not poll and rapid visibility changes cannot exceed a request per minute', async () => {
  let count = 0;
  const f = fixture(async () => {
    count++;
    return report();
  }, true);
  const stop = f.subscribeUsage('today', () => {});
  await f.advance(120000);
  assert.equal(count, 0);
  f.document.setHidden(false);
  await f.advance(0);
  assert.equal(count, 1);
  await f.advance(10000);
  f.document.setHidden(true);
  assert.equal(f.timers.size, 0);
  f.document.setHidden(false);
  await f.advance(49999);
  assert.equal(count, 1);
  await f.advance(1);
  assert.equal(count, 2);
  f.document.setHidden(true);
  await f.advance(180000);
  assert.equal(count, 2);
  f.document.setHidden(false);
  await f.advance(0);
  assert.equal(count, 3);
  stop();
});

test('hiding aborts in-flight reads, never overlaps requests, and discards an aborted late response', async () => {
  let complete;
  let count = 0;
  let signal;
  const f = fixture((_path, _timeout, options) => {
    count++;
    signal = options.signal;
    return new Promise((resolve) => {
      complete = resolve;
    });
  });
  const states = [];
  const stop = f.subscribeUsage('today', (state) => states.push(state));
  await f.advance(0);
  await f.advance(120000);
  assert.equal(count, 1);
  f.document.setHidden(true);
  assert.equal(signal.aborted, true);
  f.document.setHidden(false);
  complete(report());
  await f.advance(0);
  assert.equal(states.at(-1).data, null);
  assert.equal(count, 2);
  stop();
  assert.equal(signal.aborted, true);
  complete(report());
  await flush();
  assert.equal(f.timers.size, 0);
});

test('range changes abort old reads and ignore their later results; unmount cancels scheduled work', async () => {
  const pending = [];
  const f = fixture(
    (route, _timeout, { signal }) =>
      new Promise((resolve) => pending.push({ route, signal, resolve }))
  );
  const oldStates = [];
  const stopOld = f.subscribeUsage('today', (state) => oldStates.push(state));
  await f.advance(0);
  stopOld();
  assert.equal(pending[0].signal.aborted, true);
  const countBefore = oldStates.length;
  const nextStates = [];
  const stopNext = f.subscribeUsage('month', (state) => nextStates.push(state));
  await f.advance(0);
  pending[0].resolve(report());
  pending[1].resolve(report('month'));
  await flush();
  assert.equal(oldStates.length, countBefore);
  assert.equal(nextStates.at(-1).data.range.key, 'month');
  stopNext();
  assert.equal(f.timers.size, 0);
  assert.equal(f.listeners.size, 0);
  const stopBeforeStart = f.subscribeUsage('all', () => assert.ok(true));
  stopBeforeStart();
  await f.advance(0);
  assert.equal(pending.length, 2);
});

test('daily chart preserves sparse dates, unknown values and source numbers without filling gaps', () => {
  const f = fixture();
  const days = [
    { date: '2026-10-05', ...metrics({ total_tokens: null }) },
    { date: '2026-10-01', ...metrics({ total_tokens: 0 }) },
    { date: '2026-10-02', ...metrics({ total_tokens: 12 }) },
  ];
  const points = f.chartDays(days, 'total_tokens');
  assert.equal(points.points.length, 3);
  assert.equal(points.spanDays, 5);
  assert.equal(points.points[1].x, 0.25);
  assert.equal(points.points[2].ratio, null);
  assert.equal(days[0].date, '2026-10-05');
  assert.equal(points.points[1].day, days[2]);
  assert.equal(f.dayLabel('2026-10-01'), 'Oct 1');
  assert.match(f.snapshotLabel('2026-10-01T16:30:00Z'), /2 Oct.*00:30:00/);
});

function renderPanel(snapshot, range = 'today') {
  const f = fixture();
  let stateIndex = 0;
  const module = load('components/insights/AiUsagePanel.tsx', {
    react: {
      ...React,
      useState(initial) {
        return [stateIndex++ === 0 ? range : stateIndex === 2 ? snapshot : initial, () => {}];
      },
    },
    'react/jsx-runtime': require('react/jsx-runtime'),
    '@/lib/ai-usage': f,
    './ai-usage-panel.module.css': {
      default: new Proxy({}, { get: (_object, key) => String(key) }),
    },
  });
  return renderToStaticMarkup(React.createElement(module.default));
}

test('rendered report separates exact authoritative totals from incomplete model attribution', () => {
  const data = report();
  const html = renderPanel({ range: 'today', data, phase: 'ready', refreshing: false });
  assert.match(html, /1,024 tokens/);
  assert.match(html, /Reported: 0\.0123456789012345 USD/);
  assert.match(html, /unrecognized\/future-model:variant/);
  assert.match(html, /Unavailable/);
  assert.match(html, /Known subtotal: 1,000/);
  assert.match(html, /Pricing incomplete/);
  assert.match(html, /2026-10-02T00:19:58\.998Z/);
  assert.match(html, /default pricing/);
  assert.match(html, /Asia\/Singapore/);
});

test('rendering a newly selected range cannot flash the previous range or fabricate zeroes', () => {
  const html = renderPanel(
    { range: 'today', data: report(), phase: 'ready', refreshing: false },
    'week'
  );
  assert.match(html, /Reading your usage report/);
  assert.doesNotMatch(html, /1,024|\$0\.00|future-model/);
  const error = renderPanel({ range: 'today', data: null, phase: 'error', refreshing: false });
  assert.match(error, /Usage report unavailable/);
  assert.doesNotMatch(error, /\$0\.00|0 tokens/);
});
