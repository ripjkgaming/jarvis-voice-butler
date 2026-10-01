/* eslint-disable @typescript-eslint/no-require-imports -- CommonJS Node test harness. */
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const assert = require('node:assert/strict');
const test = require('node:test');
const path = require('node:path');
const rootPath = path.resolve(__dirname, '..');
const compile = (file) =>
  ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
// Execute the production component and shared frame clock with private virtual
// wall time. No React renderer, native command, browser, or bridge is contacted.
async function probe(stallStage, unmountAt = null, { lateAt = null, noFrames = false } = {}) {
  let wall = 0,
    next = 0,
    effect,
    cleanup,
    done = 0,
    doneAt = null,
    frames = 0,
    releaseStall;
  const classes = new Set(),
    timers = new Map(),
    rafs = new Map(),
    stages = [],
    uiStages = [];
  const stalled = () =>
    new Promise((resolve) => {
      releaseStall = resolve;
    });
  const setTimeout = (fn, ms) => {
    const id = ++next;
    timers.set(id, { at: wall + ms, fn });
    return id;
  };
  const clearTimeout = (id) => timers.delete(id);
  const noop = () => {};
  const ctx = new Proxy(
    { createRadialGradient: () => ({ addColorStop: noop }) },
    { get: (obj, key) => (key in obj ? obj[key] : noop) }
  );
  const canvas = {
    width: 1600,
    height: 64,
    getContext: () => {
      if (stallStage === 'context-throws') throw new Error('Canvas unavailable');
      return stallStage === 'no-context' ? null : ctx;
    },
  };
  const document = {
    documentElement: {
      classList: {
        add: (...xs) => xs.forEach((x) => classes.add(x)),
        remove: (...xs) => xs.forEach((x) => classes.delete(x)),
      },
      style: { setProperty: noop, removeProperty: noop },
    },
    querySelector: () => null,
  };
  const window = {
    innerWidth: 1600,
    innerHeight: 64,
    devicePixelRatio: 1,
    matchMedia: () => ({ matches: stallStage === 'restore' }),
  };
  const base = { setTimeout, clearTimeout, performance: { now: () => wall }, window, document };
  const clockModule = { exports: {}, ...base };
  vm.runInNewContext(compile(rootPath + '/lib/animation-clock.ts'), clockModule);
  const rect = {
    frame: [0, 0, 1600, 900],
    content: [0, 0, 1600, 900],
    screen: [0, 0, 1600, 900],
    primary: [0, 0, 1600, 900],
  };
  const helpers = {
    ...clockModule.exports,
    drawPackets: noop,
    drawSeg: noop,
    rgb: () => [0, 255, 255],
    clamp01: (v) => Math.max(0, Math.min(1, v)),
    easeOut: (v) => 1 - (1 - v) ** 3,
    easeIn: (v) => v ** 3,
    easeInOut: (v) => 0.5 - 0.5 * Math.cos(Math.PI * v),
    loadHudRect: () => (['measure', 'geometry'].includes(stallStage) ? null : rect),
    waitForSchoolScreen: async () =>
      ['normal', 'animation', 'scan-restore'].includes(stallStage) ? {} : null,
    stage: async (name) => {
      stages.push(name);
      if (stallStage === 'rejected-cover' && name === 'cover-at')
        throw new Error('Native command failed');
      if (name === stallStage || (name === 'restore' && stallStage === 'scan-restore'))
        return stalled();
      if (name === 'cover-at' || name === 'restore') window.innerHeight = 900;
      // Tauri serializes a successful Rust Result<(), String> as JSON null.
      return null;
    },
  };
  const deps = {
    react: {
      useEffect: (fn) => {
        effect = fn;
      },
      useRef: (value) => ({ current: value === null ? canvas : value }),
    },
    'react/jsx-runtime': { jsx: () => ({}), jsxs: () => ({}) },
    '@/components/hud/school-transition': helpers,
    '@/lib/bridge': {
      bridgeSchoolGeom: async () => (stallStage === 'geometry' ? stalled() : null),
    },
  };
  const scope = {
    exports: {},
    require: (name) => deps[name],
    ...base,
    requestAnimationFrame: (fn) => {
      const id = ++next;
      rafs.set(id, fn);
      return id;
    },
    cancelAnimationFrame: (id) => rafs.delete(id),
    Path2D: class {
      moveTo() {}
      lineTo() {}
    },
  };
  vm.runInNewContext(compile(rootPath + '/components/hud/school-return.tsx'), scope);
  scope.exports.SchoolReturn({
    ret: { id: 1 },
    color: '#00ffff',
    barPx: 64,
    barInset: 0,
    onStage: (name) => uiStages.push(name),
    onDone: () => {
      done++;
      doneAt = wall;
    },
  });
  cleanup = effect();
  for (let i = 0; i < 20; i++) await Promise.resolve();
  for (wall = 16; wall <= 30000; wall += 16) {
    if (unmountAt !== null && wall === unmountAt) cleanup?.();
    if (lateAt !== null && wall >= lateAt && releaseStall) {
      releaseStall();
      releaseStall = undefined;
    }
    // Simulate native watchdog restoring the window at 20 seconds.
    if (wall === 20000) window.innerHeight = 900;
    for (const [id, t] of [...timers])
      if (t.at <= wall) {
        timers.delete(id);
        t.fn();
      }
    if (!noFrames)
      for (const [id, fn] of [...rafs]) {
        rafs.delete(id);
        frames++;
        fn(wall);
      }
    for (let i = 0; i < 4; i++) await Promise.resolve();
  }
  const report = {
    stallStage,
    elapsedMs: wall,
    stages,
    uiStages,
    done,
    doneAt,
    classes: [...classes],
    pendingRaf: rafs.size,
    pendingTimers: timers.size,
    frames,
    viewportHeightAfterNativeWatchdog: window.innerHeight,
  };
  cleanup?.();
  report.pendingRafAfterUnmount = rafs.size;
  return report;
}
for (const name of [
  'cover-at',
  'restore',
  'measure',
  'geometry',
  'no-context',
  'context-throws',
  'scan-restore',
  'rejected-cover',
]) {
  test(`return recovers and releases UI/RAF when ${name} is unavailable`, async () => {
    const result = await probe(name);
    assert.equal(result.done, 1, JSON.stringify(result));
    assert.equal(result.pendingRaf, 0, JSON.stringify(result));
    assert.equal(result.pendingTimers, 0, JSON.stringify(result));
    assert.deepEqual(result.classes, [], JSON.stringify(result));
    assert.ok(result.stages.includes('restore'), JSON.stringify(result));
    assert.equal(
      result.stages.filter((stage) => stage === 'restore').length,
      1,
      JSON.stringify(result)
    );
    assert.ok(result.doneAt < 20000, JSON.stringify(result));
    if (name === 'scan-restore')
      assert.ok(result.uiStages.includes('scan'), JSON.stringify(result));
  });
}
test('unmount cancels a stalled return without a late restore or callback', async () => {
  const result = await probe('cover-at', 64, { lateAt: 1000 });
  assert.equal(result.done, 0);
  assert.equal(result.pendingRaf, 0);
  assert.ok(!result.stages.includes('restore'));
  assert.deepEqual(result.classes, []);
  assert.equal(result.pendingTimers, 0);
});
for (const name of ['cover-at', 'measure', 'geometry', 'restore']) {
  test(`late ${name} settlement cannot restart a completed return`, async () => {
    const result = await probe(name, null, { lateAt: 8000 });
    assert.equal(result.done, 1, JSON.stringify(result));
    assert.equal(result.stages.filter((stage) => stage === 'restore').length, 1);
    assert.equal(result.pendingRaf, 0);
    assert.equal(result.pendingTimers, 0);
    assert.deepEqual(result.uiStages, []);
  });
}
test('the overall deadline releases a return whose animation frames stop', async () => {
  const result = await probe('animation', null, { noFrames: true });
  assert.equal(result.done, 1, JSON.stringify(result));
  assert.ok(result.doneAt <= 16832, JSON.stringify(result));
  assert.equal(result.pendingRaf, 0);
  assert.equal(result.pendingTimers, 0);
  assert.deepEqual(result.classes, []);
  assert.ok(!result.uiStages.includes('scan'), JSON.stringify(result));
  assert.equal(result.stages.filter((stage) => stage === 'restore').length, 1);
});
test('successful null native replies retain sink, draft and scan before completing', async () => {
  const result = await probe('normal');
  assert.equal(result.done, 1, JSON.stringify(result));
  assert.deepEqual(result.uiStages, ['sink', 'draft', 'scan']);
  assert.deepEqual(result.stages, ['cover-at', 'restore']);
  assert.equal(result.pendingRaf, 0);
  assert.equal(result.pendingTimers, 0);
  assert.deepEqual(result.classes, []);
});
