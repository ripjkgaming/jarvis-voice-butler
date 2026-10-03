const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');
const scope = {
  exports: {},
  require() {
    throw Error('Unexpected dependency');
  },
};
vm.runInNewContext(
  ts.transpileModule(
    fs.readFileSync(path.join(__dirname, '../components/stark/metrics.ts'), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }
  ).outputText,
  scope
);
const { metricsFrom, reactorWarnings } = scope.exports;
const nominal = { ...metricsFrom(null), has: true, batt: 81, temp: 49 };
test('unknown and below-boundary metrics produce no warning light', () => {
  assert.equal(reactorWarnings(metricsFrom(null), false, 'idle').lit, false);
  assert.equal(
    reactorWarnings(
      { ...nominal, cpuPct: 89.99, memPct: 91.99, temp: 84.99, batt: 20 },
      false,
      'idle'
    ).lit,
    false
  );
});
test('each existing amber telemetry boundary lights the field', () => {
  for (const [source, patch] of [
    ['cpu', { cpuPct: 90 }],
    ['mem', { memPct: 92 }],
    ['temp', { temp: 85 }],
    ['power', { batt: 19 }],
    ['phone', { phone: { battery: 19, charging: false } }],
    ['network', { net: { kind: 'none' } }],
    ['audio', { volume: { muted: true } }],
    ['storage', { diskFree: 10 * 1024 ** 3 - 1 }],
  ]) {
    assert.deepEqual(Array.from(reactorWarnings({ ...nominal, ...patch }, false, 'idle').active), [
      source,
    ]);
  }
});
test('charging, join readiness, mute and link warnings keep correct priorities', () => {
  const m = { ...nominal, batt: 1, ac: true, phone: { battery: 1, charging: true } };
  for (const phase of ['request', 'agent', 'ready'])
    assert.equal(reactorWarnings(m, false, phase).lit, false);
  assert.deepEqual(Array.from(reactorWarnings(m, true, 'stalled').active), ['mic', 'voice']);
  assert.equal(reactorWarnings(m, false, 'failed').lit, true);
});
test('warnings recover independently and never replace voice colour', () => {
  const hot = reactorWarnings({ ...nominal, cpuPct: 99, temp: 95 }, false, 'agent');
  assert.deepEqual(Array.from(hot.active), ['cpu', 'temp']);
  assert.equal('color' in hot, false);
  assert.deepEqual(Array.from(reactorWarnings({ ...nominal, temp: 95 }, false, 'ready').active), [
    'temp',
  ]);
  assert.equal(reactorWarnings(nominal, false, 'idle').lit, false);
});
