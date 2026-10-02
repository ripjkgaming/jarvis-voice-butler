/* eslint-disable @typescript-eslint/no-require-imports -- CommonJS Node test harness. */
// Execute the production entry component and frame clock with private virtual
// time. React refs, visibility, IPC, storage and drawing are controlled here;
// no browser, desktop process, live bridge or exported shell assets are used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

const root = path.resolve(__dirname, '..');
const compile = (file) =>
  ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
const entryCode = compile('components/hud/school-transition.tsx');
const clockCode = compile('lib/animation-clock.ts');
const sceneCode = compile('lib/school-entry-scene.ts');
const storageKey = 'jarvis.school.hudRect';
const primary = { x: 0, y: 0, w: 1600, h: 900 };
const secondary = { ...primary, x: 1600 };
const clone = (value) => JSON.parse(JSON.stringify(value));
const flush = async () => {
  for (let n = 0; n < 24; n++) await Promise.resolve();
};
function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function events() {
  const listeners = new Map();
  return {
    addEventListener(name, fn) {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name).add(fn);
    },
    removeEventListener(name, fn) {
      listeners.get(name)?.delete(fn);
    },
    emit(name) {
      for (const fn of listeners.get(name) ?? []) fn();
    },
    count: () => [...listeners.values()].reduce((n, group) => n + group.size, 0),
  };
}

function fixture(options = {}) {
  let wall = 0,
    serial = 0,
    mounted = false,
    unmounted = false,
    output = options.cross ? secondary : primary;
  const timers = new Map(),
    rafs = new Map(),
    geometry = new Map();
  const calls = [],
    draws = [],
    sizes = [],
    bars = [],
    done = [],
    classes = new Set(),
    styles = new Map();
  const effects = [],
    layouts = [],
    cleanups = [],
    observers = [];
  const storage = new Map([[storageKey, JSON.stringify({ stale: true })]]);
  const media = { ...events(), matches: !!options.reduced };
  const window = {
    ...events(),
    innerWidth: 800,
    innerHeight: 600,
    devicePixelRatio: options.dpr ?? 2.5,
    matchMedia: () => media,
  };
  const snapshotWrites = [];
  let layoutReads = 0;
  const readLayout = () => {
    layoutReads++;
    throw new Error('The frozen HUD must not trigger a layout read during entry');
  };
  const snapshot = {
    // freezeHud supplies this reactor pivot and native-size capture up front.
    style: new Proxy(
      { transformOrigin: '400px 280px', width: '800px', height: '600px' },
      {
        set(target, property, value) {
          snapshotWrites.push({ at: wall, property, value });
          target[property] = value;
          return true;
        },
      }
    ),
    attached: false,
    getBoundingClientRect: readLayout,
    getClientRects: readLayout,
    remove() {
      this.attached = false;
    },
  };
  for (const property of ['offsetWidth', 'offsetHeight', 'clientWidth', 'clientHeight'])
    Object.defineProperty(snapshot, property, { get: readLayout });
  const host = { dataset: { entryPhase: 'geometry' } };
  const layer = {
    appendChild(node) {
      node.attached = true;
    },
  };
  const canvas = {
    dataset: {},
    width: 0,
    height: 0,
    getContext() {
      if (options.contextThrows) throw new Error('Canvas unavailable');
      return null;
    },
  };
  const document = {
    ...events(),
    hidden: !!options.hidden,
    documentElement: {
      classList: {
        add: (...names) => names.forEach((n) => classes.add(n)),
        remove: (...names) => names.forEach((n) => classes.delete(n)),
      },
      style: {
        setProperty: (name, value) => styles.set(name, value),
        removeProperty: (name) => styles.delete(name),
      },
    },
    createElement: () => ({ getContext: () => null }),
  };
  const setTimeout = (fn, ms) => {
    const id = ++serial;
    timers.set(id, { at: wall + ms, fn });
    return id;
  };
  const clearTimeout = (id) => timers.delete(id);
  const base = { window, document, setTimeout, clearTimeout, performance: { now: () => wall } };
  const clockScope = { exports: {}, ...base };
  vm.runInNewContext(clockCode, clockScope);
  const sceneScope = { exports: {}, ...base };
  vm.runInNewContext(sceneCode, sceneScope);
  let disposed = 0,
    clears = 0;
  const scene = {
    resize: (...args) => sizes.push(args),
    draw: (...args) => draws.push({ at: wall, args }),
    clear: () => {
      clears++;
    },
    dispose: () => {
      disposed++;
    },
  };
  function report(nonce) {
    const full = window.innerWidth === output.w && window.innerHeight === output.h;
    return {
      nonce,
      out: { ...output },
      primary: { ...primary },
      hud: full ? { x: 0, y: 0, w: output.w, h: output.h } : { x: 100, y: 80, w: 800, h: 620 },
      same: output.x === primary.x && output.y === primary.y,
      dir: output.x > primary.x ? 'left' : 'right',
      panel: 64,
      inset: 8,
    };
  }
  const resizeToOutput = () => {
    window.innerWidth = output.w;
    window.innerHeight = output.h;
    window.emit('resize');
  };
  const deps = {
    react: {
      useRef: (current) => ({ current }),
      useEffect: (fn) => effects.push(fn),
      useLayoutEffect: (fn) => layouts.push(fn),
    },
    'react/jsx-runtime': {
      jsx: (_type, props) => {
        if (props.ref)
          props.ref.current =
            props.className === 'stx-capture'
              ? layer
              : props.className === 'stx-canvas'
                ? canvas
                : host;
        return {};
      },
      jsxs: (_type, props) => {
        if (props.ref) props.ref.current = host;
        return {};
      },
    },
    '@/lib/animation-clock': clockScope.exports,
    '@/lib/school-entry-scene': {
      createEntryScene: options.contextThrows
        ? (...args) => sceneScope.exports.createEntryScene(...args)
        : () => (options.noContext ? null : scene),
    },
    '@/lib/tauri': {
      isTauri: () => true,
      invoke: async (command, args) => {
        assert.equal(command, 'school_stage');
        calls.push({ ...clone(args), at: wall });
        if (args.stage === 'measure') geometry.set(args.nonce, report(args.nonce));
        if (options.native) {
          const value = options.native(args, { setTimeout, resizeToOutput, window, at: wall });
          if (value !== undefined) return value;
        }
        if (args.stage === 'cover') resizeToOutput();
        if (args.stage === 'primary') {
          const move = () => {
            output = primary;
            resizeToOutput();
          };
          if (options.moveDelay) setTimeout(move, options.moveDelay);
          else move();
        }
        if (args.stage === 'dock') {
          const dock = () => {
            window.innerWidth = primary.w;
            window.innerHeight = 64;
            window.emit('resize');
          };
          if (options.dockDelay) setTimeout(dock, options.dockDelay);
          else dock();
        }
        // Successful Rust Result<(), String> is serialized as null.
        return null;
      },
    },
    '@/lib/bridge': {
      bridgeSchoolGeom: async (nonce) =>
        options.read ? options.read(nonce, geometry.get(nonce)) : geometry.get(nonce),
    },
  };
  const scope = {
    exports: {},
    ...base,
    localStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
    require: (name) => {
      assert.ok(name in deps, `Unexpected dependency: ${name}`);
      return deps[name];
    },
    requestAnimationFrame: (fn) => {
      const id = ++serial;
      rafs.set(id, fn);
      return id;
    },
    cancelAnimationFrame: (id) => rafs.delete(id),
    IntersectionObserver: class {
      constructor(fn) {
        this.fn = fn;
        this.connected = true;
        observers.push(this);
      }
      observe() {}
      disconnect() {
        this.connected = false;
      }
    },
  };
  vm.runInNewContext(entryCode, scope);
  function unmount() {
    if (unmounted) return;
    unmounted = true;
    for (const cleanup of cleanups.toReversed()) cleanup?.();
  }
  return {
    window,
    document,
    media,
    snapshot,
    snapshotWrites,
    host,
    canvas,
    calls,
    draws,
    sizes,
    bars,
    done,
    classes,
    styles,
    storage,
    timers,
    rafs,
    get wall() {
      return wall;
    },
    get disposed() {
      return disposed;
    },
    get clears() {
      return clears;
    },
    get layoutReads() {
      return layoutReads;
    },
    get listenerCount() {
      return window.count() + document.count() + media.count();
    },
    get observerCount() {
      return observers.filter((o) => o.connected).length;
    },
    mount() {
      assert.equal(mounted, false);
      mounted = true;
      scope.exports.SchoolTransition({
        tx: { kind: options.kind ?? 'collapse', id: 1, snapshot, origin: [400, 280] },
        color: '#5fe3ff',
        onBar: (...args) => bars.push({ at: wall, args }),
        onDone: () => {
          done.push(wall);
          unmount();
        },
      });
      for (const effect of [...layouts, ...effects]) cleanups.push(effect());
    },
    unmount,
    offscreen(value) {
      for (const observer of observers)
        if (observer.connected) observer.fn([{ isIntersecting: !value }]);
    },
    hide(value) {
      document.hidden = value;
      document.emit('visibilitychange');
    },
    reduce(value) {
      media.matches = value;
      media.emit('change');
    },
    async advance(ms, { frames = true } = {}) {
      await flush();
      const end = wall + ms;
      while (wall < end) {
        wall = Math.min(end, wall + 16);
        for (const [id, timer] of [...timers])
          if (timer.at <= wall) {
            timers.delete(id);
            timer.fn();
          }
        await flush();
        if (frames)
          for (const [id, fn] of [...rafs]) {
            rafs.delete(id);
            fn(wall);
          }
        await flush();
      }
    },
    async until(check, limit = 20000) {
      const end = wall + limit;
      while (!check() && wall < end) await this.advance(16);
      assert.ok(check(), `Condition not reached by ${wall} ms: ${JSON.stringify(calls)}`);
    },
  };
}

function released(f) {
  assert.equal(f.rafs.size, 0, 'No retained transition RAF');
  assert.equal(f.timers.size, 0, 'No retained transition timer');
  assert.equal(f.listenerCount, 0, 'No retained visibility/resize/media listeners');
  assert.equal(f.observerCount, 0, 'No retained intersection observer');
  assert.equal(f.snapshot.attached, false, 'Frozen HUD removed');
  assert.deepEqual([...f.classes], []);
  assert.deepEqual([...f.styles], []);
}

test('the frozen HUD attaches before pending geometry; reversal suppresses late IPC continuation', async () => {
  const pending = deferred();
  const f = fixture({ native: ({ stage }) => (stage === 'measure' ? pending.promise : undefined) });
  f.mount();
  assert.equal(f.snapshot.attached, true);
  assert.equal(f.draws.length, 0);
  await f.advance(64);
  f.unmount();
  const canceledCalls = clone(f.calls);
  pending.resolve(null);
  await f.advance(20000);
  assert.deepEqual(f.calls, canceledCalls);
  assert.equal(f.bars.length, 0);
  assert.equal(f.done.length, 0);
  released(f);
});

test('the frozen HUD folds on one affine surface about its reactor without relayout, then detaches', async () => {
  const f = fixture();
  f.mount();
  await f.until(() => f.draws.some((draw) => draw.args[0] === 'compress' && draw.args[1] >= 0.55));
  assert.equal(
    f.snapshot.attached,
    true,
    'The same captured surface remains attached during its fold'
  );
  assert.equal(f.snapshot.style.transformOrigin, '400px 280px');
  const transform = f.snapshot.style.transform;
  const pose = /^matrix\(([^)]+)\)$/.exec(transform)?.[1].split(',').map(Number);
  assert.ok(pose && pose.length === 6 && pose.every(Number.isFinite), 'One finite 2D matrix');
  const [a, b, c, d, x, y] = pose;
  assert.deepEqual([x, y], [0, 0], 'The captured reactor pivot is never translated');
  assert.ok(b < 0 && c > 0, 'The folding surface retains its bank');
  assert.ok(a * d - b * c > 0, 'The surface never flips or becomes singular');
  assert.ok(Math.hypot(c, d) < Math.hypot(a, b), 'The height folds before the width closes');
  await f.until(() => f.calls.some((call) => call.stage === 'cover'));
  assert.equal(
    f.snapshot.attached,
    false,
    'The capture is removed before native coverage/transport'
  );
  assert.equal(f.snapshot.style.transformOrigin, '400px 280px');
  assert.equal(f.snapshot.style.width, '800px');
  assert.equal(f.snapshot.style.height, '600px');
  const poses = f.snapshotWrites
    .filter((write) => write.property === 'transform')
    .map((write) => /^matrix\(([^)]+)\)$/.exec(write.value)?.[1].split(',').map(Number));
  assert.deepEqual(poses[0], [1, 0, 0, 1, 0, 0], 'The original captured pose starts unchanged');
  for (const values of poses) {
    assert.ok(values && values.length === 6 && values.every(Number.isFinite));
    assert.ok(values[0] * values[3] - values[1] * values[2] > 0);
  }
  const final = poses.at(-1);
  assert.ok(Math.hypot(final[0], final[1]) < 0.1 && Math.hypot(final[2], final[3]) < 0.1);
  assert.equal(f.layoutReads, 0);
  assert.deepEqual([...new Set(f.snapshotWrites.map((write) => write.property))].sort(), [
    'opacity',
    'transform',
  ]);
  await f.until(() => f.done.length > 0);
  released(f);
});

test('reduced motion saves the original decorated HUD geometry before direct docking', async () => {
  const f = fixture({ reduced: true, cross: true });
  f.mount();
  await f.until(() => f.done.length > 0);
  assert.deepEqual(JSON.parse(f.storage.get(storageKey)), {
    frame: [1700, 80, 800, 620],
    content: [1700, 100, 800, 600],
    screen: [1600, 0, 1600, 900],
    primary: [0, 0, 1600, 900],
  });
  assert.deepEqual(
    f.calls.map((c) => c.stage),
    ['measure', 'dock']
  );
  assert.equal(f.draws.length, 0);
  assert.equal(f.bars.length, 0);
  assert.equal(f.done.length, 1);
  released(f);
});

for (const fault of ['native', 'bridge']) {
  test(`stalled initial ${fault} is bounded, clears stale return geometry and ignores late settlement`, async () => {
    const pending = deferred();
    const f = fixture(
      fault === 'native'
        ? { native: ({ stage }) => (stage === 'measure' ? pending.promise : undefined) }
        : { read: () => pending.promise }
    );
    f.mount();
    await f.until(() => f.done.length > 0, 4000);
    assert.ok(f.done[0] <= 2000);
    assert.equal(f.storage.has(storageKey), false);
    assert.deepEqual(
      f.calls.map((c) => c.stage),
      ['measure', 'dock']
    );
    pending.resolve(null);
    await f.advance(20000);
    assert.equal(f.done.length, 1);
    assert.deepEqual(
      f.calls.map((c) => c.stage),
      ['measure', 'dock']
    );
    released(f);
  });
}

for (const fault of ['noContext', 'contextThrows']) {
  test(`unavailable entry canvas (${fault}) docks safely and releases transition state`, async () => {
    const f = fixture({ [fault]: true });
    assert.doesNotThrow(() => f.mount());
    await f.until(() => f.done.length > 0, 4000);
    assert.deepEqual(
      f.calls.map((c) => c.stage),
      ['measure', 'dock']
    );
    assert.equal(f.draws.length, 0);
    assert.equal(f.done.length, 1);
    released(f);
  });
}

test('equal-size monitor transfer stays transparent until a fresh destination acknowledgement', async () => {
  const f = fixture({ cross: true, moveDelay: 900 });
  f.mount();
  await f.until(() => f.calls.some((c) => c.stage === 'primary'));
  const moveAt = f.calls.find((c) => c.stage === 'primary').at;
  const frameCount = f.draws.length;
  await f.advance(800);
  assert.equal(f.draws.length, frameCount, 'No arrival drawing over the old equal-size output');
  assert.equal(f.rafs.size, 0);
  assert.equal(f.bars.length, 0);
  await f.until(() => f.draws.some((d) => d.args[0] === 'transfer-in'));
  assert.ok(f.draws.find((d) => d.args[0] === 'transfer-in').at >= moveAt + 900);
  const nonceCalls = f.calls.filter((c) => c.stage === 'measure' && c.at >= moveAt);
  assert.ok(nonceCalls.length > 1);
  assert.equal(new Set(nonceCalls.map((c) => c.nonce)).size, nonceCalls.length);
  await f.until(() => f.done.length > 0);
  assert.ok(
    f.sizes.every((size) => size[2] === 2.5),
    'The full device pixel ratio reaches every renderer resize'
  );
  released(f);
});

test('offscreen entry suspends all RAF work and resumes without skipping drawn time', async () => {
  const f = fixture();
  f.mount();
  await f.until(() => f.draws.length >= 4);
  const prior = f.draws.at(-1);
  const priorStep = prior.args[1] - f.draws.at(-2).args[1];
  f.offscreen(true);
  const count = f.draws.length;
  await f.advance(800);
  assert.equal(f.draws.length, count);
  assert.equal(f.rafs.size, 0);
  f.offscreen(false);
  await f.advance(16);
  const resumed = f.draws.at(-1);
  assert.equal(resumed.args[0], prior.args[0]);
  assert.ok(
    resumed.args[1] - prior.args[1] <= priorStep * 2.2,
    'Long offscreen pauses are bounded to the frame-clock clamp'
  );
  assert.equal(f.rafs.size, 1, 'Exactly one owner resumes the frame loop');
  await f.until(() => f.done.length > 0);
  released(f);
});

for (const change of ['hide', 'reduce']) {
  test(`changing ${change} during entry docks once and cannot revive the completed renderer`, async () => {
    const f = fixture();
    f.mount();
    await f.until(() => f.draws.length >= 4);
    f[change](true);
    await f.until(() => f.done.length > 0);
    const before = { calls: f.calls.length, draws: f.draws.length };
    f[change](false);
    await f.advance(20000);
    assert.equal(f.calls.filter((c) => c.stage === 'dock').length, 1);
    assert.equal(f.done.length, 1);
    assert.deepEqual({ calls: f.calls.length, draws: f.draws.length }, before);
    assert.equal(f.disposed, 1);
    released(f);
  });
}

test('the revealed interactive bar stays unclipped during a delayed dock acknowledgement', async () => {
  const f = fixture({ dockDelay: 450 });
  f.mount();
  await f.until(() => f.calls.some((c) => c.stage === 'dock'));
  assert.equal(f.bars.length, 1);
  assert.deepEqual(f.bars[0].args, [64, 8]);
  const assemblyEnd = f.draws.find((draw) => draw.args[0] === 'forge' && draw.args[1] === 1);
  assert.ok(
    assemblyEnd && assemblyEnd.at <= f.bars[0].at,
    'Interactive bar readiness follows completed assembly'
  );
  assert.equal(f.styles.get('--school-entry-reveal'), '0%');
  assert.equal(f.done.length, 0);
  assert.equal(f.rafs.size, 0);
  await f.advance(400);
  assert.equal(f.styles.get('--school-entry-reveal'), '0%');
  assert.equal(f.done.length, 0);
  await f.until(() => f.done.length > 0);
  assert.equal(f.done.length, 1);
  released(f);
});

test('stalled docking and missing viewport acknowledgement have a bounded completion', async () => {
  const pending = deferred();
  const f = fixture({
    reduced: true,
    native: ({ stage }) => (stage === 'dock' ? pending.promise : undefined),
  });
  f.mount();
  await f.until(() => f.done.length > 0, 4200);
  assert.ok(f.done[0] <= 3840, 'Native IPC and viewport acknowledgement are independently bounded');
  assert.equal(f.window.innerHeight, 600, 'Recovery does not pretend that a stalled shell resized');
  assert.equal(f.bars.length, 0);
  pending.resolve(null);
  await f.advance(20000);
  assert.equal(f.done.length, 1);
  assert.deepEqual(
    f.calls.map((c) => c.stage),
    ['measure', 'dock']
  );
  released(f);
});

test('reversal while docking suppresses a late completion callback', async () => {
  const pending = deferred();
  const f = fixture({
    reduced: true,
    native: ({ stage }) => (stage === 'dock' ? pending.promise : undefined),
  });
  f.mount();
  await f.until(() => f.calls.some((c) => c.stage === 'dock'));
  f.unmount();
  pending.resolve(null);
  await f.advance(20000);
  assert.equal(f.done.length, 0);
  assert.equal(f.bars.length, 0);
  assert.deepEqual(
    f.calls.map((c) => c.stage),
    ['measure', 'dock']
  );
  released(f);
});

test('return-to-school arrival preserves the saved normal HUD rectangle', async () => {
  const saved = {
    frame: [1740, 110, 920, 660],
    content: [1740, 130, 920, 640],
    screen: [1600, 0, 1600, 900],
    primary: [0, 0, 1600, 900],
  };
  const f = fixture({ kind: 'arrive' });
  f.storage.set(storageKey, JSON.stringify(saved));
  f.mount();
  await f.until(() => f.done.length > 0);
  assert.deepEqual(JSON.parse(f.storage.get(storageKey)), saved);
  assert.ok(f.calls.some((call) => call.stage === 'primary'));
  assert.ok(!f.calls.some((call) => call.stage === 'unframe' || call.stage === 'cover'));
  assert.equal(f.bars.length, 1);
  released(f);
});

test('reversal during pending monitor IPC cannot replay arrival, docking or readiness callbacks', async () => {
  const pending = deferred();
  const f = fixture({
    cross: true,
    native: ({ stage }) => (stage === 'primary' ? pending.promise : undefined),
  });
  f.mount();
  await f.until(() => f.calls.some((c) => c.stage === 'primary'));
  f.unmount();
  const before = { calls: f.calls.length, draws: f.draws.length };
  pending.resolve(null);
  await f.advance(20000);
  assert.deepEqual({ calls: f.calls.length, draws: f.draws.length }, before);
  assert.equal(f.bars.length, 0);
  assert.equal(f.done.length, 0);
  released(f);
});

test('the overall deadline releases an entry whose animation frames never arrive', async () => {
  const f = fixture();
  f.mount();
  await f.advance(20000, { frames: false });
  assert.equal(f.done.length, 1);
  assert.ok(f.done[0] <= 15032);
  assert.equal(f.calls.filter((c) => c.stage === 'dock').length, 1);
  assert.ok(
    f.draws.every((draw) => draw.args[1] === 1),
    'Only bounded phase endpoints may paint without a RAF callback'
  );
  released(f);
});
