// Run from frontend: node --test tests/particle-skin.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

function load(rel, globals = {}) {
  const src = fs.readFileSync(path.join(__dirname, '..', rel), 'utf8');
  const out = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
  const mod = { exports: {} };
  new Function('module', 'exports', 'require', 'navigator', out.outputText)(
    mod,
    mod.exports,
    () => ({}),
    globals.navigator
  );
  return mod.exports;
}

const motion = (extra = {}) => ({
  particles: {
    cap_desktop: 1,
    cap_low: 0.45,
    low_cores: 4,
    retint_ms: 120,
    size_steps: 4,
    ...extra,
  },
});

test('setParticleSkin mutates in place and bumps version', () => {
  const m = load('lib/character/particle-skin.ts', { navigator: { hardwareConcurrency: 8 } });
  const ref = m.particleSkin.rgb;
  const v = m.particleSkin.version;
  m.setParticleSkin([255, 42, 54, 1], [255, 179, 184, 1], [11, 2, 5, 1], {
    count: 0.45,
    size: 1.35,
    drift: 0.6,
  });
  assert.equal(m.particleSkin.rgb, ref);
  assert.equal(m.particleSkin.version, v + 1);
  assert.equal(m.particleHex(), '#ff2a36');
  assert.equal(m.particleHex(true), '#ffb3b8');
  assert.equal(m.particleSkin.count, 0.45);
});

test('particleCap picks the low-power cap by core count', () => {
  const low = load('lib/character/particle-skin.ts', { navigator: { hardwareConcurrency: 4 } });
  const high = load('lib/character/particle-skin.ts', { navigator: { hardwareConcurrency: 12 } });
  assert.equal(low.particleCap(motion()), 0.45);
  assert.equal(high.particleCap(motion()), 1);
  assert.equal(high.particleCap(null), 1);
});

test('quantSize snaps to steps so cross-fades re-bake rarely', () => {
  const m = load('lib/character/particle-skin.ts', { navigator: { hardwareConcurrency: 8 } });
  assert.equal(m.quantSize(1.0, 4), 1);
  assert.equal(m.quantSize(1.1, 4), 1);
  assert.equal(m.quantSize(1.2, 4), 1.25);
  const seen = new Set();
  for (let i = 0; i <= 100; i++) seen.add(m.quantSize(1 + (0.5 * i) / 100, 4));
  assert.ok(seen.size <= 3);
});
