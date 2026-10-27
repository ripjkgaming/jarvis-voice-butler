// Run from frontend: node --test tests/character-layout.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const cp = require('node:child_process');

function skin() {
  const src = fs.readFileSync(path.join(__dirname, '../lib/character/skin.ts'), 'utf8');
  const out = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
  const mod = { exports: {} };
  const cls = new Proxy({}, { get: (_t, k) => `c_${String(k)}` });
  const req = (n) => {
    if (n.endsWith('.module.css')) return { default: cls, ...cls };
    if (n === './color')
      return { css: () => '', lerpColor: () => [0, 0, 0, 1], parseColor: () => [0, 0, 0, 1] };
    if (n === './particle-skin') return { setParticleSkin() {} };
    return {};
  };
  new Function('module', 'exports', 'require', out.outputText)(mod, mod.exports, req);
  return mod.exports;
}

function layouts() {
  const py = `import json,sys;sys.path.insert(0,'..');from src import characters as C;print(json.dumps(C.motion()['layouts']))`;
  return JSON.parse(
    cp.execFileSync('python3', ['-c', py], { cwd: path.join(__dirname, '..') }).toString()
  );
}

test('Jarvis (reference layout) gets no skin rules; no layouts means no CSS', () => {
  const { layoutCss } = skin();
  assert.equal(layoutCss(undefined), '');
  const css = layoutCss(layouts());
  assert.ok(!css.includes("data-skin='jarvis'"));
});

test('Ultron preset mirrors the columns and uses the spec widths', () => {
  const { layoutCss } = skin();
  const L = layouts();
  const css = layoutCss(L);
  const [l, c, r] = L.hud_angular.columns;
  assert.ok(css.includes(`minmax(250px, ${r}fr) minmax(0, ${c}fr) minmax(190px, ${l}fr)`));
  assert.ok(css.includes(`--top-h: ${L.hud_angular.top_h}px`));
});

test('Friday preset places the summary strip and card grid from the spec', () => {
  const { layoutCss } = skin();
  const L = layouts();
  const css = layoutCss(L);
  assert.ok(css.includes(`repeat(${L.hud_glass.cards}, minmax(0, 1fr))`));
  assert.ok(css.includes('display: contents'));
  assert.ok(css.includes('grid-column: 1 / -1; grid-row: 1;'));
});

test('every layout in _motion.yaml is well formed', () => {
  const L = layouts();
  for (const [name, s] of Object.entries(L)) {
    assert.ok(['columns', 'cards'].includes(s.flow), name);
    if (s.flow === 'columns')
      assert.equal(
        s.columns.reduce((a, b) => a + b, 0),
        100,
        name
      );
    assert.ok(s.reactor_scale >= 0.4 && s.reactor_scale <= 1.4, name);
  }
});
