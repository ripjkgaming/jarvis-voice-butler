// Run from frontend: node --test tests/animation-clock.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');

// Virtual wall time keeps timeout/disposal cases deterministic and instantaneous.
function fixture() {
  let wall = 0;
  let next = 0;
  const timers = new Map();
  const source = fs.readFileSync(path.join(__dirname, '../lib/animation-clock.ts'), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const scope = {
    exports: {},
    performance: { now: () => wall },
    setTimeout: (fn, ms) => {
      const id = ++next;
      timers.set(id, { at: wall + ms, fn });
      return id;
    },
    clearTimeout: (id) => timers.delete(id),
  };
  vm.runInNewContext(compiled, scope, { filename: 'animation-clock.ts' });
  return {
    ...scope.exports,
    timers,
    async advance(ms) {
      const end = wall + ms;
      for (;;) {
        const due = [...timers].sort((a, b) => a[1].at - b[1].at)[0];
        if (!due || due[1].at > end) break;
        wall = due[1].at;
        timers.delete(due[0]);
        due[1].fn();
        await Promise.resolve();
      }
      wall = end;
      await Promise.resolve();
    },
  };
}

test('a stalled frame advances at most 34 ms; negative deltas never rewind', () => {
  const { frameClock } = fixture();
  const clock = frameClock();
  assert.equal(clock.tick(100), 0);
  assert.equal(clock.tick(116), 16);
  assert.equal(clock.tick(2116), 50);
  assert.equal(clock.tick(2100), 50);
  clock.dispose();
});

test('concurrent tweens update together before their frame is painted', async () => {
  const { frameClock, runTimers, timers } = fixture();
  const clock = frameClock();
  clock.tick(0);
  const values = [[], []];
  const { tween } = runTimers(clock, () => true);
  const done = values.map((v) => tween(32, (e) => v.push(e), (e) => e));
  clock.tick(16);
  assert.deepEqual(values, [[0, 0.5], [0, 0.5]]);
  clock.tick(32);
  assert.deepEqual(values, [[0, 0.5, 1], [0, 0.5, 1]]);
  await Promise.all(done);
  assert.equal(timers.size, 0);
  clock.dispose();
});

test('settle waits for three actual frames, not its initial condition check', async () => {
  const { frameClock, runTimers } = fixture();
  const clock = frameClock();
  let settled = false;
  const done = runTimers(clock, () => true).settle().then((ok) => { settled = ok; });
  clock.tick(0);
  clock.tick(16);
  await Promise.resolve();
  assert.equal(settled, false);
  clock.tick(32);
  await done;
  assert.equal(settled, true);
  clock.dispose();
});

test('wait times out even when no animation frames arrive', async () => {
  const { frameClock, advance, timers } = fixture();
  const clock = frameClock();
  const result = clock.wait(() => false, 50);
  await advance(50);
  assert.equal(await result, false);
  assert.equal(timers.size, 0);
  clock.dispose();
});

test('a stalled tween finishes at its endpoint after its real-time safety cap', async () => {
  const { frameClock, runTimers, advance, timers } = fixture();
  const clock = frameClock();
  const samples = [];
  const done = runTimers(clock, () => true).tween(100, (e) => samples.push(e));
  await advance(3300);
  await done;
  assert.deepEqual(samples, [0, 1]);
  assert.equal(timers.size, 0);
  clock.dispose();
});

test('reversal disposal releases pending work and prevents further updates', async () => {
  const { frameClock, runTimers, advance, timers } = fixture();
  const clock = frameClock();
  let alive = true;
  const samples = [];
  const done = runTimers(clock, () => alive).tween(100, (e) => samples.push(e));
  const waiting = clock.wait(() => false, 500);
  alive = false;
  clock.dispose();
  await done;
  assert.equal(await waiting, false);
  assert.equal(await clock.frames(3), false);
  clock.tick(100);
  await advance(10000);
  assert.deepEqual(samples, [0]);
  assert.equal(timers.size, 0);
  assert.equal(clock.now, 0);
});
