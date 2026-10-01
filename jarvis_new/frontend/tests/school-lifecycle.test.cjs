// Run from frontend: node --test tests/school-lifecycle.test.cjs
// Native commands, geometry responses, and wall time are entirely mocked.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

async function flush() {
  for (let i = 0; i < 20; i++) await Promise.resolve();
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const primary = { x: 0, y: 0, w: 1920, h: 1080 };
const secondary = { ...primary, x: 1920 };
const geometry = (nonce, out, destination = primary) => ({
  nonce,
  out: { ...out },
  primary: { ...destination },
  hud: { x: 0, y: 0, w: out.w, h: out.h },
  same: out.x === destination.x && out.y === destination.y,
  dir: out.x > destination.x ? 'left' : 'right',
  panel: 64,
  inset: 0,
});

function fixture({ invoke, read } = {}) {
  let wall = 0;
  let next = 0;
  let random = 0;
  const timers = new Map();
  const stages = [];
  const reads = [];
  const window = { innerWidth: primary.w, innerHeight: primary.h };
  const setTimeout = (fn, ms) => {
    const id = ++next;
    timers.set(id, { at: wall + ms, fn });
    return id;
  };
  const clearTimeout = (id) => timers.delete(id);
  const math = Object.create(Math);
  math.random = () => ++random / 10001;
  const dependencies = {
    react: {},
    'react/jsx-runtime': {},
    '@/lib/animation-clock': {},
    '@/lib/school-entry-scene': {},
    '@/lib/tauri': {
      isTauri: () => true,
      invoke: async (command, args) => {
        assert.equal(command, 'school_stage');
        assert.equal(args.stage, 'measure', 'Geometry acknowledgement must only measure');
        stages.push({ ...args, at: wall });
        return invoke?.(args);
      },
    },
    '@/lib/bridge': {
      bridgeSchoolGeom: async (nonce) => {
        reads.push({ nonce, at: wall });
        return read ? read(nonce) : null;
      },
    },
  };
  const filename = path.join(__dirname, '../components/hud/school-transition.tsx');
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  const scope = {
    exports: {},
    require(name) {
      assert.ok(name in dependencies, `Unexpected dependency: ${name}`);
      return dependencies[name];
    },
    window,
    Math: math,
    performance: { now: () => wall },
    setTimeout,
    clearTimeout,
  };
  vm.runInNewContext(compiled, scope, { filename });
  return {
    waitForSchoolScreen: scope.exports.waitForSchoolScreen,
    stages,
    reads,
    timers,
    window,
    setTimeout,
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

test('equal-size monitor moves require a fresh nonce reporting the destination coordinates', async () => {
  let output = secondary;
  const snapshots = new Map();
  const f = fixture({
    invoke: ({ nonce }) => snapshots.set(nonce, geometry(nonce, output)),
    read: (nonce) => snapshots.get(nonce),
  });
  f.setTimeout(() => {
    output = primary;
  }, 210);
  let settled = false;
  const result = f
    .waitForSchoolScreen('primary', () => true)
    .then((value) => {
      settled = true;
      return value;
    });
  await f.advance(209);
  assert.equal(settled, false, 'Matching viewport dimensions do not acknowledge a monitor hop');
  assert.equal(f.stages.length, 3);
  assert.equal(new Set(f.stages.map((call) => call.nonce)).size, 3);
  await f.advance(1);
  assert.equal((await result).out.x, 0);
  assert.equal(f.stages.length, 4);
  assert.equal(f.timers.size, 0);
});

test('return acknowledgement requires the saved screen and a fully resized viewport', async () => {
  const target = { x: -2560, y: 120, w: 2560, h: 1440 };
  const snapshots = new Map();
  let output = { ...target, y: 0 };
  const f = fixture({
    invoke: ({ nonce }) => snapshots.set(nonce, geometry(nonce, output)),
    read: (nonce) => snapshots.get(nonce),
  });
  let settled = false;
  const result = f
    .waitForSchoolScreen(target, () => true)
    .then((value) => {
      settled = true;
      return value;
    });
  await f.advance(70);
  assert.equal(settled, false, 'Matching width and x do not cover an incorrect y coordinate');
  output = target;
  await f.advance(70);
  assert.equal(settled, false, 'Correct output does not prove the viewport resized');
  f.window.innerWidth = target.w;
  f.window.innerHeight = target.h;
  await f.advance(70);
  assert.deepEqual((await result).out, target);
  assert.equal(f.timers.size, 0);
});

test('a pending report keeps its nonce; a newer primary is taken from the fresh report', async () => {
  let ready = false;
  const newPrimary = { x: 0, y: -1080, w: 1920, h: 1080 };
  const f = fixture({
    read: (nonce) => (ready ? geometry(nonce, newPrimary, newPrimary) : null),
  });
  const result = f.waitForSchoolScreen('primary', () => true);
  await f.advance(140);
  assert.equal(f.stages.length, 1);
  assert.equal(new Set(f.reads.map((call) => call.nonce)).size, 1);
  ready = true;
  await f.advance(70);
  assert.deepEqual((await result).out, newPrimary);
  assert.equal(f.timers.size, 0);
});

test('stalled native measurement is bounded and its late resolution cannot restart polling', async () => {
  const native = deferred();
  const f = fixture({ invoke: () => native.promise });
  const result = f.waitForSchoolScreen('primary', () => true, 2500);
  await f.advance(2500);
  assert.equal(await result, null);
  assert.equal(f.stages.length, 1);
  assert.equal(f.reads.length, 0);
  native.resolve();
  await f.advance(10000);
  assert.equal(f.stages.length, 1);
  assert.equal(f.reads.length, 0);
  assert.equal(f.timers.size, 0);
});

test('stalled geometry response is bounded and a late valid report cannot acknowledge arrival', async () => {
  const response = deferred();
  const f = fixture({ read: () => response.promise });
  const result = f.waitForSchoolScreen('primary', () => true, 2500);
  await f.advance(2500);
  assert.equal(await result, null);
  response.resolve(geometry(f.stages[0].nonce, primary));
  await f.advance(10000);
  assert.equal(f.stages.length, 1);
  assert.equal(f.reads.length, 1);
  assert.equal(f.timers.size, 0);
});

test('reversal cancels pending retries and never measures the destination afterward', async () => {
  let alive = true;
  const f = fixture({ read: (nonce) => geometry(nonce, secondary) });
  const result = f.waitForSchoolScreen('primary', () => alive);
  await f.advance(0);
  alive = false;
  await f.advance(70);
  assert.equal(await result, null);
  const count = f.stages.length;
  await f.advance(10000);
  assert.equal(count, 1);
  assert.equal(f.stages.length, count);
  assert.equal(f.reads.length, 1);
  assert.equal(f.timers.size, 0);
});

test('unavailable geometry fails safely on its real-time deadline without extra measurements', async () => {
  const f = fixture({ read: () => Promise.reject(new Error('Bridge unavailable')) });
  const result = f.waitForSchoolScreen('primary', () => true, 2500);
  await f.advance(2500);
  assert.equal(await result, null);
  assert.equal(f.stages.length, 1);
  await f.advance(70); // Release the final short polling pause.
  assert.equal(f.timers.size, 0);
});
