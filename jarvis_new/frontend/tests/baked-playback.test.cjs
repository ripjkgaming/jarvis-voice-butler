// Run from frontend: node --test tests/baked-playback.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');

function fixture({ reducedMotion = false, paused = false, frameCount = 8 } = {}) {
  const timers = new Map();
  const listeners = new Map();
  const effects = [];
  const painted = [];
  let observer;
  let classObserver;
  let classDisconnected = false;
  let reducedListener;
  let covered = true;
  const classes = new Set();
  const reduced = {
    matches: reducedMotion,
    addEventListener(type, fn) {
      reducedListener = fn;
    },
    removeEventListener() {
      reducedListener = null;
    },
  };
  let now = 100;
  let serial = 0;
  let rafCalls = 0;
  const ctx = {
    clearRect() {},
    drawImage(frame) {
      painted.push(frame.id);
    },
  };
  const canvas = {
    width: 4,
    height: 4,
    getContext: () => ctx,
    closest: () => (covered ? {} : null),
  };
  const document = {
    hidden: false,
    documentElement: { classList: { contains: (name) => classes.has(name) } },
    addEventListener(type, fn) {
      listeners.set(type, fn);
    },
    removeEventListener(type) {
      listeners.delete(type);
    },
    createElement: () => ({ addColorStop() {} }),
  };
  class IntersectionObserver {
    constructor(cb) {
      observer = cb;
    }
    observe() {}
    disconnect() {}
  }
  const scope = {
    exports: {},
    require(name) {
      assert.equal(name, 'react');
      return {
        useRef: (current) => ({ current }),
        useEffect: (effect) => effects.push(effect),
      };
    },
    document,
    window: { matchMedia: () => reduced },
    MutationObserver: class {
      constructor(cb) {
        classObserver = cb;
      }
      observe() {}
      disconnect() {
        classDisconnected = true;
      }
    },
    IntersectionObserver,
    performance: { now: () => now },
    setTimeout(fn, delay) {
      const id = ++serial;
      timers.set(id, { fn, delay });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    requestAnimationFrame() {
      rafCalls += 1;
      return ++serial;
    },
    cancelAnimationFrame() {},
  };
  const source = fs.readFileSync(path.join(__dirname, '../hooks/hud/use-baked-frames.ts'), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  vm.runInNewContext(compiled, scope, { filename: 'use-baked-frames.ts' });
  const frames = Array.from({ length: frameCount }, (_, id) => ({ id }));
  const ref = scope.exports.useBakedPlayback({ frames, width: 4, height: 4 }, 12, paused);
  ref.current = canvas; // React attaches the ref before running effects.
  const cleanup = effects[0]();
  return {
    canvas,
    cleanup,
    setClass(name, present) {
      if (present) classes.add(name);
      else classes.delete(name);
      classObserver();
    },
    setReduced(value) {
      reduced.matches = value;
      reducedListener();
    },
    setCovered(value) {
      covered = value;
    },
    observersCleaned: () => classDisconnected && !reducedListener,
    document,
    frames,
    listeners,
    observer: (entries) => observer(entries),
    painted,
    rafCalls: () => rafCalls,
    timers,
    fire(extraMs = 1) {
      const [id, item] = timers.entries().next().value;
      timers.delete(id);
      now += item.delay + extraMs;
      item.fn();
    },
    ref,
  };
}

test('baked playback paints on its target FPS timer without a display-rate rAF loop', () => {
  const f = fixture();
  assert.equal(f.ref.current.width, 4);
  assert.deepEqual(f.painted, [0]);
  assert.equal(f.timers.size, 1);
  assert.ok(Math.abs(f.timers.values().next().value.delay - 1000 / 12) < 1);
  f.fire();
  assert.deepEqual(f.painted, [0, 1]);
  f.fire();
  assert.deepEqual(f.painted, [0, 1, 2]);
  assert.equal(f.rafCalls(), 0);
  f.cleanup();
  assert.equal(f.timers.size, 0);
});

test('hidden and offscreen playback parks, then resumes when visible', () => {
  const f = fixture();
  f.document.hidden = true;
  f.listeners.get('visibilitychange')();
  assert.equal(f.timers.size, 0);
  // An intersection callback received while hidden must not poison the
  // separate in-view state and prevent the visibility event from resuming.
  f.observer([{ isIntersecting: true }]);
  assert.equal(f.timers.size, 0);
  f.document.hidden = false;
  f.listeners.get('visibilitychange')();
  assert.equal(f.timers.size, 1);
  f.observer([{ isIntersecting: false }]);
  assert.equal(f.timers.size, 0);
  f.observer([{ isIntersecting: true }]);
  assert.equal(f.timers.size, 1);
  f.fire(84);
  assert.equal(f.painted.length, 2);
  f.cleanup();
  assert.equal(f.timers.size, 0);
  assert.equal(f.listeners.size, 0);
});

test('native hiding, transition ownership and modal coverage park playback without polling', () => {
  const f = fixture();
  for (const name of ['hud-hidden', 'school-tx', 'suit-open']) {
    f.setClass(name, true);
    assert.equal(f.timers.size, 0, name);
    f.observer([{ isIntersecting: true }]);
    assert.equal(f.timers.size, 0, 'intersection cannot bypass ownership');
    f.setClass(name, false);
    assert.equal(f.timers.size, 1);
  }
  // A canvas outside the covered HUD (e.g. in its dialog) remains active.
  f.setCovered(false);
  f.setClass('suit-open', true);
  assert.equal(f.timers.size, 1);
  f.cleanup();
  assert.equal(f.timers.size, 0);
  assert.equal(f.observersCleaned(), true);
});

test('reduced motion parks immediately and preference changes resume without remounting', () => {
  const f = fixture({ reducedMotion: true });
  assert.deepEqual(f.painted, [0]);
  assert.equal(f.timers.size, 0);
  f.setReduced(false);
  assert.equal(f.timers.size, 1);
  f.fire(100);
  f.setReduced(true);
  assert.equal(f.timers.size, 0);
  assert.equal(f.painted.at(-1), 0);
  f.cleanup();
  assert.equal(f.observersCleaned(), true);
});

test('paused and single-frame films do not install playback observers or timers', () => {
  for (const options of [{ paused: true }, { frameCount: 1 }]) {
    const f = fixture(options);
    assert.deepEqual(f.painted, [0]);
    assert.equal(f.timers.size, 0);
    assert.equal(f.listeners.size, 0);
    assert.equal(f.cleanup, undefined);
  }
});
