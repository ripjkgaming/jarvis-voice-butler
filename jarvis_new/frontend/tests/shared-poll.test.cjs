/* eslint-disable @typescript-eslint/no-require-imports */
const assert = require('node:assert/strict'),
  fs = require('node:fs'),
  path = require('node:path'),
  vm = require('node:vm'),
  test = require('node:test'),
  ts = require('typescript');
function runtime() {
  let now = 0,
    id = 0,
    reply = { room: 'a', call_live: true, agent_state: 'speaking' },
    calls = 0,
    pending;
  const timers = new Map(),
    events = {};
  const document = { hidden: false, addEventListener: (k, fn) => (events[k] = fn) };
  const scope = {
    exports: {},
    Date: { now: () => now },
    document,
    setTimeout: (fn, ms) => {
      timers.set(++id, { fn, at: now + ms });
      return id;
    },
    clearTimeout: (i) => timers.delete(i),
    require: (name) =>
      name === 'react'
        ? {}
        : {
            bridgeGet: async () => {
              calls++;
              return typeof reply === 'function' ? reply() : reply;
            },
          },
  };
  vm.runInNewContext(
    ts.transpileModule(fs.readFileSync(path.join(__dirname, '../lib/shared-poll.ts'), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS },
    }).outputText,
    scope
  );
  return {
    subscribe: scope.exports.subscribePoll,
    document,
    events,
    timers,
    setReply: (x) => (reply = x),
    hang: () => (reply = () => new Promise((resolve) => (pending = resolve))),
    release: (x) => pending(x),
    get calls() {
      return calls;
    },
    async advance(ms) {
      const until = now + ms;
      while (true) {
        const next = [...timers.entries()]
          .filter(([, t]) => t.at <= until)
          .sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        now = next[1].at;
        timers.delete(next[0]);
        next[1].fn();
        await Promise.resolve();
        await Promise.resolve();
      }
      now = until;
      await Promise.resolve();
      await Promise.resolve();
    },
  };
}
test('hung request and backoff cannot extend the last successful room colour', async () => {
  const r = runtime(),
    seen = [];
  r.subscribe('/room', 300, (x) => seen.push(x), 3000);
  await r.advance(0);
  assert.equal(seen.at(-1).agent_state, 'speaking');
  r.hang();
  await r.advance(300);
  assert.equal(r.calls, 2);
  await r.advance(2699);
  assert.equal(seen.at(-1).agent_state, 'speaking');
  await r.advance(1);
  assert.equal(seen.at(-1), null);
  r.release(null);
  await r.advance(0);
  r.setReply({ room: 'b', call_live: true, agent_state: 'listening' });
  await r.advance(600);
  assert.equal(seen.at(-1).room, 'b');
});
test('one shared poll, unchanged successful responses renew lease, hidden/resume and remount', async () => {
  const r = runtime(),
    a = [],
    b = [];
  const stopA = r.subscribe('/room', 300, (x) => a.push(x), 3000),
    stopB = r.subscribe('/room', 300, (x) => b.push(x), 3000);
  await r.advance(4000);
  assert.ok(a.at(-1));
  assert.equal(a.length, 1);
  assert.equal(b.length, 1);
  r.document.hidden = true;
  r.events.visibilitychange();
  await r.advance(3001);
  assert.equal(a.at(-1), null);
  r.document.hidden = false;
  r.events.visibilitychange();
  await r.advance(0);
  assert.ok(a.at(-1));
  stopA();
  stopB();
  assert.equal(r.timers.size, 0);
  await r.advance(4000);
  const c = [];
  r.subscribe('/room', 300, (x) => c.push(x), 3000);
  assert.equal(c.at(-1), null);
  await r.advance(0);
  assert.equal(c.at(-1).room, 'a');
});
test('brief failure retains colour, sustained failures expire with no extra requests', async () => {
  const r = runtime(),
    seen = [];
  r.subscribe('/room', 300, (x) => seen.push(x), 3000);
  await r.advance(0);
  r.setReply(null);
  await r.advance(900);
  assert.equal(seen.length, 1);
  r.setReply({ room: 'a', call_live: true, agent_state: 'thinking' });
  await r.advance(1200);
  assert.equal(seen.at(-1).agent_state, 'thinking');
  r.setReply(null);
  await r.advance(5000);
  assert.equal(seen.at(-1), null);
});
