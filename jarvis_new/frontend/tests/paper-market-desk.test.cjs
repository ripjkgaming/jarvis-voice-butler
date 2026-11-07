/* eslint-disable @typescript-eslint/no-require-imports */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

function compile(file, dependencies = {}) {
  const scope = {
    exports: {},
    Date,
    Intl,
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

let paperState;
const lib = compile('../lib/paper-market.ts');
const { Desk } = compile('../components/stark/desk.tsx', {
  '@/components/markets/PaperTradingPanel': {
    usePaperMarket: () => paperState,
  },
  // Glyph primitives are presentational; stub them so the card markup is tested.
  '@/components/ascii/glyphs': { GlyphChart: () => null },
  '@/lib/ai-usage': {
    subscribeUsage() {
      throw new Error('Static fixtures must not subscribe');
    },
    estimatedCost: () => '',
    tokenCount: () => '',
  },
  '@/lib/character/hooks': { useAscii: () => true },
  './classic': { ClassicDeskSpark: () => null },
  '@/lib/paper-market': lib,
  '@/lib/shared-poll': { useSharedPoll: () => ({ drafts: [] }) },
  './frame': {
    Frame: ({ label, children }) =>
      React.createElement('section', { 'data-panel': label }, children),
  },
  './stark.module.css': {
    __esModule: true,
    default: new Proxy({}, { get: (_, key) => key }),
  },
});

const warmupReason = 'At least 16 consecutive completed minute bars are required for an entry.';
const warmupLabel = 'Warming up: waiting for 16 completed minute bars';

function renderMarket(status, error = null, open = true) {
  const snapshot = JSON.parse(
    fs.readFileSync(path.join(__dirname, 'fixtures/paper-market-populated.json'), 'utf8')
  );
  snapshot.runtime.last_cycle_status = status;
  snapshot.runtime.last_error = error;
  snapshot.market.is_open = open;
  snapshot.holdings = [];
  snapshot.account.cash_usd = '997.13';
  paperState = { snapshot, error: null };
  const html = renderToStaticMarkup(React.createElement(Desk, { index: 0 }));
  const panel = html.match(/<section data-panel="PAPER MARKET">([\s\S]*?)<\/section>/);
  assert.ok(panel, 'Render the actual paper-market desk card');
  return panel[1];
}

for (const error of [warmupReason, null]) {
  test(`warm-up explains pending bars without a failure warning (${error === null ? 'no error' : 'known reason'})`, () => {
    const html = renderMarket('warming_up', error);
    assert.ok(html.includes(warmupLabel));
    assert.doesNotMatch(html, /LAST CYCLE FAILED|data-tone="warn"/);
  });
}

for (const status of ['held', 'filled', 'no_setup', 'market_closed', 'session_exit_window']) {
  test(`${status} retains holdings and cash without a failure warning`, () => {
    const html = renderMarket(status, null, status !== 'market_closed');
    assert.ok(html.includes('0 HOLDINGS · CASH $997.13'));
    assert.doesNotMatch(html, /LAST CYCLE FAILED|Warming up:|data-tone="warn"/);
  });
}

for (const [status, error] of [
  ['error', 'Paper proposal timed out.'],
  ['error', null],
  ['rejected', 'The SPY quote expired at the commit boundary.'],
  ['quotes_unavailable', 'Fresh regular-session quotes are required before making a decision.'],
  ['clock_error', 'Clock precedes experiment start.'],
  ['warming_up', 'Automatic paper decision failed; inspect the ledger and feed status.'],
  ['error', warmupReason],
]) {
  test(`genuine failure remains visible (${status}, ${error ?? 'no error text'})`, () => {
    const html = renderMarket(status, error);
    assert.match(html, /data-tone="warn"[^>]*>LAST CYCLE FAILED/);
    assert.ok(!html.includes(warmupLabel));
  });
}
