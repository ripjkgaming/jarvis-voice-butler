/** Isolated headless panel QA. Never builds/exports the app or calls a bridge.
 * Run: node tests/paper-market-visual.cjs [/tmp/output-directory]
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const ts = require('typescript');
const webpack = require('next/dist/compiled/webpack/webpack');
webpack.init();

const frontend = path.resolve(__dirname, '..');
const output = path.resolve(process.argv[2] || '/tmp/jarvis-paper-market-populated');
const source = fs.mkdtempSync(path.join(os.tmpdir(), 'paper-market-visual-src-'));
const fixture = require('./fixtures/paper-market-populated.json');
const cents = (amount) => Math.round(Number(amount) * 100);
const sum = (items, key) => items.reduce((total, item) => total + cents(item[key]), 0);
assert.equal(
  cents(fixture.account.cash_usd) + sum(fixture.holdings, 'market_value_usd'),
  cents(fixture.account.equity_usd)
);
assert.equal(
  sum(fixture.holdings, 'unrealized_pnl_usd'),
  cents(fixture.account.unrealized_pnl_usd)
);
assert.equal(
  cents(fixture.account.unrealized_pnl_usd) + cents(fixture.account.realized_pnl_usd),
  cents(fixture.account.total_pnl_usd)
);
assert.equal(
  cents(fixture.account.equity_usd) - cents(fixture.experiment.initial_cash_usd),
  cents(fixture.account.total_pnl_usd)
);
assert.equal(sum(fixture.fills, 'realized_pnl_usd'), cents(fixture.account.realized_pnl_usd));
assert.equal(
  cents(fixture.experiment.initial_cash_usd) +
    fixture.fills.reduce(
      (total, fill) => total + cents(fill.notional_usd) * (fill.side === 'sell' ? 1 : -1),
      0
    ),
  cents(fixture.account.cash_usd)
);
for (const holding of fixture.holdings) {
  assert.equal(
    Math.round(Number(holding.quantity) * cents(holding.mark_price_usd)),
    cents(holding.market_value_usd)
  );
  assert.equal(
    cents(holding.market_value_usd) -
      Math.round(Number(holding.quantity) * cents(holding.average_cost_usd)),
    cents(holding.unrealized_pnl_usd)
  );
}

function transpile(relative, destination) {
  const result = ts
    .transpileModule(fs.readFileSync(path.join(frontend, relative), 'utf8'), {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
        esModuleInterop: true,
      },
    })
    .outputText.replace(
      /require\(["']\.\/paper-trading-panel.module.css["']\)/g,
      'require("./styles.js")'
    );
  fs.writeFileSync(path.join(source, destination), result);
}
transpile('components/markets/PaperTradingPanel.tsx', 'panel.js');
transpile('lib/paper-market.ts', 'paper-market.js');
fs.writeFileSync(
  path.join(source, 'styles.js'),
  'module.exports={__esModule:true,default:new Proxy({},{get:(_,key)=>key})};'
);
fs.writeFileSync(
  path.join(source, 'bridge.js'),
  'exports.bridgePaperMarket=()=>{throw Error("QA must not fetch live data")};'
);
fs.writeFileSync(path.join(source, 'fixture.json'), JSON.stringify(fixture));
fs.writeFileSync(
  path.join(source, 'entry.js'),
  `
const React = require('react');
const {createRoot} = require('react-dom/client');
const {PaperTradingPanel} = require('./panel');
const fixture = require('./fixture.json');
const root = createRoot(document.getElementById('root'));
window.__PAPER_MARKET_QA__ = fixture;
window.setPaperSnapshot = snapshot => root.render(React.createElement(PaperTradingPanel,{embedded:true,snapshot}));
window.setPaperSnapshot(fixture);
`
);
fs.mkdirSync(output, { recursive: true });
const css = fs.readFileSync(
  path.join(frontend, 'components/markets/paper-trading-panel.module.css'),
  'utf8'
);
fs.writeFileSync(
  path.join(output, 'index.html'),
  `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Synthetic paper market QA</title><style>
body{margin:0;background:#02080d;padding:20px;color:#fff;}main{max-width:1100px;margin:auto;}#qa-banner{padding:14px;background:#3d260f;color:#ffe0a8;border:1px solid #9c733a;font:600 12px/1.5 system-ui,sans-serif;text-align:center;letter-spacing:.035em;}@media(max-width:600px){body{padding:8px;}#qa-banner{font-size:11px;}}
${css}
</style></head><body><main><div id="qa-banner">SYNTHETIC QA FIXTURE — NOT LIVE PERFORMANCE</div><div id="root"></div></main><script src="bundle.js"></script></body></html>`
);

webpack.webpack(
  {
    mode: 'development',
    target: 'web',
    devtool: false,
    entry: path.join(source, 'entry.js'),
    output: { path: output, filename: 'bundle.js' },
    resolve: {
      modules: [path.join(frontend, 'node_modules'), 'node_modules'],
      alias: {
        '@/lib/paper-market': path.join(source, 'paper-market.js'),
        '@/lib/bridge': path.join(source, 'bridge.js'),
      },
    },
  },
  (error, stats) => {
    try {
      if (error || stats.hasErrors())
        throw error || new Error(stats.toString({ all: false, errors: true }));
      const result = spawnSync(
        'uv',
        [
          'run',
          '--no-sync',
          'python',
          path.join(__dirname, 'paper-market-visual.py'),
          '--directory',
          output,
        ],
        {
          cwd: path.resolve(frontend, '..'),
          stdio: 'inherit',
        }
      );
      if (result.error) throw result.error;
      process.exitCode = result.status || 0;
    } catch (error) {
      console.error(error);
      process.exitCode = 1;
    } finally {
      fs.rmSync(source, { recursive: true, force: true });
    }
  }
);
