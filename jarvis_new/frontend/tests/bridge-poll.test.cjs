// Run from frontend: node --test tests/bridge-poll.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');

async function flush() {
  // Drain the endpoint -> fetch -> JSON -> shared-poller promise chain.
  for (let i = 0; i < 20; i++) await Promise.resolve();
}

function clock() {
  let wall = 0;
  let next = 0;
  const timers = new Map();
  return {
    timers,
    setTimeout(fn, ms) {
      const id = ++next;
      timers.set(id, { at: wall + ms, fn });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    async advance(ms) {
      await flush();
      const end = wall + ms;
      for (;;) {
        const due = [...timers].sort((a, b) => a[1].at - b[1].at)[0];
        if (!due || due[1].at > end) break;
        wall = due[1].at;
        timers.delete(due[0]);
        due[1].fn();
        await flush();
      }
      wall = end;
      await flush();
    },
  };
}

function load(file, dependencies, globals = {}) {
  const source = fs.readFileSync(path.join(__dirname, '../lib', file), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
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

function bridgeFixture({ tauri = false, bridgeInfo, fetch } = {}) {
  const time = clock();
  const bridge = load('bridge.ts', {
    '@/lib/tauri': {
      isTauri: () => tauri,
      bridgeInfo: bridgeInfo ?? (() => Promise.reject(new Error('Unexpected native request'))),
    },
  }, {
    ...time,
    AbortController,
    process: { env: {} },
    fetch: fetch ?? (() => { throw new Error('Unexpected fetch'); }),
  });
  return { ...bridge, ...time };
}

const response = (value) => ({ ok: true, json: async () => value });

function pendingUntilAbort(signal) {
  return new Promise((_, reject) => {
    if (signal.aborted) reject(new Error('Aborted'));
    else signal.addEventListener('abort', () => reject(new Error('Aborted')), { once: true });
  });
}

function documentFixture() {
  const listeners = new Set();
  return {
    hidden: false,
    addEventListener(name, fn) {
      assert.equal(name, 'visibilitychange');
      listeners.add(fn);
    },
    setHidden(hidden) {
      this.hidden = hidden;
      listeners.forEach((fn) => fn());
    },
  };
}

function pollFixture({ bridgeGet, time = clock(), react = {} }) {
  const document = documentFixture();
  const poll = load('shared-poll.ts', { react, '@/lib/bridge': { bridgeGet } }, {
    ...time,
    document,
  });
  return { ...poll, ...time, document };
}

// Effects commit separately from render so a stale-data flash cannot be
// hidden by flushing useEffect before checking the hook's return value.
function hookAdapter() {
  const states = [];
  const effects = [];
  let cursor = 0;
  let pending = [];
  return {
    react: {
      useState(initial) {
        const index = cursor++;
        if (!(index in states)) states[index] = typeof initial === 'function' ? initial() : initial;
        return [states[index], (next) => {
          states[index] = typeof next === 'function' ? next(states[index]) : next;
        }];
      },
      useEffect(fn, deps) {
        const index = cursor++;
        const prior = effects[index];
        if (!prior || deps.some((value, i) => !Object.is(value, prior.deps[i]))) {
          pending.push(() => {
            prior?.cleanup?.();
            effects[index] = { deps, cleanup: fn() };
          });
        }
      },
    },
    render(fn) {
      cursor = 0;
      pending = [];
      return fn();
    },
    commit() {
      pending.forEach((fn) => fn());
      pending = [];
    },
    unmount() {
      effects.forEach((effect) => effect.cleanup?.());
    },
  };
}

test('stalled native discovery falls back after 1.5 s, then retries and caches authenticated discovery', async () => {
  let discoveries = 0;
  const requests = [];
  const f = bridgeFixture({
    tauri: true,
    bridgeInfo: () => ++discoveries === 1
      ? new Promise(() => {})
      : Promise.resolve({ url: 'http://bridge.test', token: 'test-token' }),
    fetch: async (url, options) => {
      requests.push({ url, options });
      return response({ ok: true });
    },
  });
  const first = f.bridgeGet('/sys');
  await f.advance(1499);
  assert.equal(requests.length, 0);
  await f.advance(1);
  assert.equal((await first).ok, true);
  assert.equal(requests[0].url, 'http://127.0.0.1:4317/sys');
  assert.equal(requests[0].options.headers, undefined);

  await f.bridgeGet('/sys');
  await f.bridgeGet('/room');
  assert.equal(discoveries, 2);
  assert.equal(requests[1].url, 'http://bridge.test/sys');
  assert.equal(requests[2].options.headers.Authorization, 'Bearer test-token');
  assert.equal(f.timers.size, 0);
});

test('a rejected native lookup is shared by concurrent callers but does not poison the next lookup', async () => {
  let discoveries = 0;
  const urls = [];
  const f = bridgeFixture({
    tauri: true,
    bridgeInfo: async () => {
      if (++discoveries === 1) throw new Error('Native IPC unavailable');
      return { url: 'http://recovered.test' };
    },
    fetch: async (url) => {
      urls.push(url);
      return response({ ok: true });
    },
  });
  await Promise.all([f.bridgeGet('/sys'), f.bridgeGet('/room')]);
  assert.equal(discoveries, 1);
  assert.ok(urls.every((url) => url.startsWith('http://127.0.0.1:4317/')));
  await f.bridgeGet('/sys');
  assert.equal(discoveries, 2);
  assert.equal(urls.at(-1), 'http://recovered.test/sys');
  assert.equal(f.timers.size, 0);
});

test('system and geometry GETs abort at their separate deadlines and can retry', async () => {
  let blocked = true;
  const requests = [];
  const f = bridgeFixture({ fetch: (url, { signal }) => {
    requests.push({ url, signal });
    return blocked ? pendingUntilAbort(signal) : Promise.resolve(response({ recovered: true }));
  } });
  const sys = f.bridgeGet('/sys');
  const geometry = f.bridgeGet('/school/geom');
  await f.advance(999);
  assert.ok(requests.every(({ signal }) => !signal.aborted));
  await f.advance(1);
  assert.equal(await geometry, null);
  assert.equal(requests.find(({ url }) => url.endsWith('/sys')).signal.aborted, false);
  await f.advance(4000);
  assert.equal(await sys, null);
  assert.equal(f.timers.size, 0);
  blocked = false;
  assert.equal((await f.bridgeGet('/sys')).recovered, true);
  assert.equal(f.timers.size, 0);
});

test('the GET deadline also covers a stalled response body', async () => {
  const f = bridgeFixture({ fetch: async (_url, { signal }) => ({
    ok: true,
    json: () => pendingUntilAbort(signal),
  }) });
  const request = f.bridgeGet('/sys');
  await f.advance(5000);
  assert.equal(await request, null);
  assert.equal(f.timers.size, 0);
});

test('a timed-out poll releases its in-flight latch, keeps the last value, and recovers', async () => {
  let calls = 0;
  const bridge = bridgeFixture({ fetch: async (_url, { signal }) => {
    calls++;
    if (calls === 2) return pendingUntilAbort(signal);
    return response({ version: calls });
  } });
  const p = pollFixture({ bridgeGet: bridge.bridgeGet, time: bridge });
  const values = [];
  const stop = p.subscribePoll('/sys', 100, (value) => values.push(value.version));
  await p.advance(0);
  assert.deepEqual(values, [1]);
  await p.advance(100);
  assert.equal(calls, 2);
  const stopSecond = p.subscribePoll('/sys', 100, () => {});
  await p.advance(4999);
  assert.equal(calls, 2);
  await p.advance(1);
  assert.deepEqual(values, [1]);
  await p.advance(199);
  assert.equal(calls, 2);
  await p.advance(1);
  assert.deepEqual(values, [1, 3]);
  stop();
  stopSecond();
  assert.equal(p.timers.size, 0);
});

test('shared polls pause immediately while hidden and resume once without duplicate updates', async () => {
  let calls = 0;
  let complete;
  const p = pollFixture({ bridgeGet: () => {
    calls++;
    if (calls === 1) return new Promise((resolve) => { complete = resolve; });
    return Promise.resolve({ version: 1 });
  } });
  const received = [[], []];
  const stopA = p.subscribePoll('/sys', 100, (value) => received[0].push(value.version));
  const stopB = p.subscribePoll('/sys', 200, (value) => received[1].push(value.version));
  await p.advance(0);
  assert.equal(calls, 1);
  p.document.setHidden(true);
  complete({ version: 1 });
  await flush();
  assert.deepEqual(received, [[1], [1]]);
  assert.equal(p.timers.size, 0);
  await p.advance(1000);
  assert.equal(calls, 1);
  p.document.setHidden(false);
  await p.advance(0);
  assert.equal(calls, 2);
  assert.deepEqual(received, [[1], [1]]);
  p.document.setHidden(true);
  assert.equal(p.timers.size, 0);
  stopA();
  stopB();
});

test('hook path changes and disabled renders never expose the previous endpoint snapshot', async () => {
  const hooks = hookAdapter();
  const p = pollFixture({
    react: hooks.react,
    bridgeGet: async (route) => ({ route }),
  });
  const render = (route, enabled = true) => hooks.render(() => p.useSharedPoll(route, 100, enabled));
  assert.equal(render('/first'), null);
  hooks.commit();
  await p.advance(0);
  assert.equal(render('/first').route, '/first');
  hooks.commit();

  // Read before the subscription effect commits: old state is still present.
  assert.equal(render('/second'), null);
  hooks.commit();
  await p.advance(0);
  assert.equal(render('/second').route, '/second');
  hooks.commit();
  assert.equal(render('/second', false), null);
  hooks.commit();
  assert.equal(p.timers.size, 0);

  // A previously visited endpoint may supply its own cached data immediately.
  assert.equal(render('/first').route, '/first');
  hooks.commit();
  hooks.unmount();
  assert.equal(p.timers.size, 0);
});
