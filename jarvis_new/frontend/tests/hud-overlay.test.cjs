// Actual shared overlay helper, isolated per test; no browser or native UI.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');

function fixture(initial = [false, true]) {
  const surfaces = initial.map(inert => ({ inert }));
  const classes = new Set();
  const queried = [];
  const document = {
    documentElement: { classList: { add: value => classes.add(value), remove: value => classes.delete(value) } },
    querySelectorAll(selector) { queried.push(selector); return surfaces.slice(); },
  };
  const source = fs.readFileSync(path.join(__dirname, '../lib/hud-overlay.ts'), 'utf8');
  const scope = { exports: {}, document };
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText, scope);
  return { ...scope.exports, surfaces, classes, queried };
}

for (const firstToRelease of ['insights', 'suit']) {
  test(`shared coverage survives ${firstToRelease} releasing first during a handoff`, () => {
    const f = fixture();
    const release = { insights: f.coverHud(), suit: f.coverHud() };
    assert.ok(f.surfaces.every(surface => surface.inert));
    assert.ok(f.classes.has('suit-open'));
    release[firstToRelease]();
    release[firstToRelease](); // A repeated cleanup must not decrement another owner's count.
    assert.ok(f.surfaces.every(surface => surface.inert));
    assert.ok(f.classes.has('suit-open'));
    release[firstToRelease === 'insights' ? 'suit' : 'insights']();
    assert.deepEqual(f.surfaces.map(surface => surface.inert), [false, true]);
    assert.equal(f.classes.has('suit-open'), false);
    assert.ok(f.queried.every(selector => selector === '.hud, .sbar-root'));
  });
}

test('coverage restores each newly discovered surface to its original inert state', () => {
  const f = fixture([false]);
  const releaseFirst = f.coverHud();
  f.surfaces.push({ inert: false }, { inert: true });
  const releaseSecond = f.coverHud();
  releaseSecond();
  assert.deepEqual(f.surfaces.map(surface => surface.inert), [true, false, true]);
  assert.ok(f.classes.has('suit-open'));
  releaseFirst();
  assert.deepEqual(f.surfaces.map(surface => surface.inert), [false, false, true]);
  assert.equal(f.classes.size, 0);
});

test('a later overlay captures fresh source inert values after all prior owners release', () => {
  const f = fixture([false, true]);
  f.coverHud()();
  f.surfaces[0].inert = true;
  f.surfaces[1].inert = false;
  f.coverHud()();
  assert.deepEqual(f.surfaces.map(surface => surface.inert), [true, false]);
  assert.equal(f.classes.size, 0);
});

test('empty HUD surfaces still suspend ambient work until the last owner releases', () => {
  const f = fixture([]);
  const releaseFirst = f.coverHud();
  const releaseSecond = f.coverHud();
  releaseFirst();
  assert.ok(f.classes.has('suit-open'));
  releaseSecond();
  releaseSecond();
  assert.equal(f.classes.size, 0);
  f.coverHud()();
  assert.equal(f.classes.size, 0);
});
