/* eslint-disable @typescript-eslint/no-require-imports */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');

function runtime(hidden = false) {
  let now = 0,
    id = 0;
  const timers = new Map(),
    listeners = new Set();
  const document = {
    hidden,
    addEventListener: (_, cb) => listeners.add(cb),
    removeEventListener: (_, cb) => listeners.delete(cb),
  };
  const scope = {
    exports: {},
    document,
    AbortController,
    setTimeout: (fn, ms) => {
      timers.set(++id, { fn, at: now + ms });
      return id;
    },
    clearTimeout: (key) => timers.delete(key),
  };
  vm.runInNewContext(
    ts.transpileModule(fs.readFileSync(path.join(__dirname, '../lib/visible-poll.ts'), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS },
    }).outputText,
    scope
  );
  const flush = async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
  };
  return {
    start: scope.exports.startVisiblePoll,
    timers,
    listeners,
    flush,
    visible: (visible) => {
      document.hidden = !visible;
      listeners.forEach((fn) => fn());
    },
    async advance(ms) {
      const end = now + ms;
      await flush();
      for (;;) {
        const next = [...timers.entries()]
          .filter(([, t]) => t.at <= end)
          .sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        now = next[1].at;
        timers.delete(next[0]);
        next[1].fn();
        await flush();
      }
      now = end;
    },
  };
}

test('slow reads serialize; cleanup aborts and removes all work', async () => {
  const r = runtime(),
    signals = [];
  let resolve;
  const stop = r.start((signal) => {
    signals.push(signal);
    return new Promise((done) => (resolve = done));
  }, 100);
  await r.advance(1000);
  assert.equal(signals.length, 1);
  assert.equal(r.timers.size, 0);
  resolve();
  await r.advance(99);
  assert.equal(signals.length, 1);
  await r.advance(1);
  assert.equal(signals.length, 2);
  stop();
  assert.equal(signals[1].aborted, true);
  assert.equal(r.listeners.size, 0);
  resolve();
  await r.advance(1000);
  assert.equal(signals.length, 2);
  assert.equal(r.timers.size, 0);
});

test('hidden windows pause; resume refreshes immediately without overlap', async () => {
  const r = runtime(true),
    signals = [];
  let resolve;
  const stop = r.start((signal) => {
    signals.push(signal);
    return new Promise((done) => (resolve = done));
  }, 100);
  await r.advance(1000);
  assert.equal(signals.length, 0);
  r.visible(true);
  assert.equal(signals.length, 1);
  r.visible(false);
  assert.equal(signals[0].aborted, true);
  r.visible(true);
  assert.equal(signals.length, 1);
  resolve();
  await r.advance(0);
  assert.equal(signals.length, 2);
  resolve();
  await r.flush();
  r.visible(false);
  await r.advance(1000);
  assert.equal(signals.length, 2);
  r.visible(true);
  assert.equal(signals.length, 3);
  stop();
  resolve();
  await r.flush();
  assert.equal(r.timers.size, 0);
});

test('rejected reads recover on the next interval', async () => {
  const r = runtime();
  let calls = 0;
  const stop = r.start(async () => {
    calls++;
    if (calls === 1) throw new Error('temporary failure');
  }, 100);
  await r.advance(200);
  assert.equal(calls, 3);
  stop();
  assert.equal(r.timers.size, 0);
});
