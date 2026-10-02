// Actual production hook in a deterministic effect harness; no native UI.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');

function compile(file) {
  return ts.transpileModule(fs.readFileSync(path.join(__dirname, '..', file), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
}

function fixture({ tauri = true, deferMenu = false, deferFocus = false, deferReset = false, availHeight = 1080 } = {}) {
  let index = 0, dirty = false, mounted = true, result, school = false, transitioning = false, suitOpen = false;
  let geometry = { height: 900 }, refreshes = 0, nextLease = 100, resolveReset;
  let effects = [], layoutEffects = [];
  const cells = [], calls = [], menuPending = [], focusPending = [];
  const listeners = new Map(), classes = new Set(), surfaces = [{ inert: false }, { inert: true }];
  const focusReset = { current: deferReset ? new Promise(resolve => { resolveReset = resolve; }) : Promise.resolve() };
  const document = {
    documentElement: { classList: { add: value => classes.add(value), remove: value => classes.delete(value) } },
    querySelectorAll: () => surfaces,
  };
  const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const react = {
    useRef(value) { const i = index++; return cells[i] ??= { current: value }; },
    useState(initial) {
      const i = index++;
      cells[i] ??= { value: typeof initial === 'function' ? initial() : initial };
      return [cells[i].value, next => {
        const value = typeof next === 'function' ? next(cells[i].value) : next;
        if (!Object.is(value, cells[i].value)) { cells[i].value = value; dirty = true; }
      }];
    },
    useCallback(fn, deps) { const i = index++; if (!same(cells[i]?.deps, deps)) cells[i] = { deps, fn }; return cells[i].fn; },
    useEffect(fn, deps) { queueEffect(effects, fn, deps); },
    useLayoutEffect(fn, deps) { queueEffect(layoutEffects, fn, deps); },
  };
  function queueEffect(queue, fn, deps) {
    const i = index++;
    if (same(cells[i]?.deps, deps)) return;
    const old = cells[i];
    cells[i] = { deps };
    queue.push(() => { old?.cleanup?.(); cells[i].cleanup = fn(); });
  }
  const overlay = { exports: {}, document };
  vm.runInNewContext(compile('lib/hud-overlay.ts'), overlay);
  const deps = {
    react,
    '@/hooks/hud/use-window-geometry': { useWindowGeometry: () => geometry, refreshWindowGeometry: () => { refreshes++; } },
    '@/lib/hud-overlay': overlay.exports,
    '@/lib/tauri': {
      isTauri: () => tauri,
      invoke(name, args) {
        calls.push({ name, ...args });
        if (name === 'school_menu' && args.extra > 0 && deferMenu) return new Promise(resolve => menuPending.push(resolve));
        if (name === 'suit_focus' && args.active) return deferFocus ? new Promise(resolve => focusPending.push(resolve)) : Promise.resolve(++nextLease);
        return Promise.resolve(null);
      },
    },
  };
  const scope = {
    exports: {}, document,
    require(name) { assert.ok(deps[name], `Unexpected dependency: ${name}`); return deps[name]; },
    window: {
      screen: { availHeight },
      addEventListener(name, fn) { const handlers = listeners.get(name) ?? new Set(); handlers.add(fn); listeners.set(name, handlers); },
      removeEventListener(name, fn) { const handlers = listeners.get(name); handlers?.delete(fn); if (handlers?.size === 0) listeners.delete(name); },
    },
  };
  vm.runInNewContext(compile('hooks/hud/use-insights.ts'), scope);
  function render() {
    let turns = 0;
    do {
      assert.ok(++turns < 20, 'Effect loop');
      dirty = false; index = 0;
      result = scope.exports.useInsights(school, transitioning, suitOpen, focusReset);
      const layout = layoutEffects, passive = effects;
      layoutEffects = []; effects = [];
      layout.forEach(fn => fn()); passive.forEach(fn => fn());
    } while (dirty);
    return result;
  }
  render();
  return {
    calls, classes, surfaces, listeners, menuPending, focusPending, focusReset, document,
    get menuCalls() { return calls.filter(call => call.name === 'school_menu'); },
    get focusCalls() { return calls.filter(call => call.name === 'suit_focus'); },
    get refreshes() { return refreshes; },
    get result() { return result; },
    resolveReset() { resolveReset?.(); },
    update(next = {}) {
      if ('school' in next) school = next.school;
      if ('transitioning' in next) transitioning = next.transitioning;
      if ('suitOpen' in next) suitOpen = next.suitOpen;
      if ('height' in next) geometry = { height: next.height };
      return render();
    },
    open(tab) { result.open(tab); return render(); },
    close() { result.close(); return render(); },
    signal(phase = 'collapse') { listeners.get('jarvis-school')?.forEach(fn => fn({ detail: phase })); },
    async flush() { for (let i = 0; i < 16; i++) await Promise.resolve(); return mounted ? render() : result; },
    unmount() { mounted = false; cells.forEach(cell => cell.cleanup?.()); },
  };
}

test('closed Insights is inert and unmount removes its only mode listener', async () => {
  const f = fixture();
  assert.equal(f.result.visible, false);
  assert.equal(f.result.tab, 'usage');
  assert.equal(f.calls.length, 0);
  assert.equal(f.refreshes, 0);
  assert.deepEqual(f.surfaces.map(surface => surface.inert), [false, true]);
  assert.deepEqual([...f.listeners.keys()], ['jarvis-school']);
  f.update({ school: true, height: 64 });
  await f.flush();
  assert.equal(f.calls.length, 0);
  f.unmount();
  assert.equal(f.listeners.size, 0);
  assert.equal(f.classes.size, 0);
});

test('captures the launcher before inert coverage, and tab changes keep that return target', () => {
  const f = fixture();
  const launcher = { id: 'usage-launcher' };
  f.document.activeElement = launcher;
  f.open('usage');
  assert.equal(f.result.returnFocus.current, launcher);
  f.document.activeElement = { id: 'market-tab' };
  f.open('market');
  assert.equal(f.result.returnFocus.current, launcher);
  f.close();
  const nextLauncher = { id: 'market-launcher' };
  f.document.activeElement = nextLauncher;
  f.open('market');
  assert.equal(f.result.returnFocus.current, nextLauncher);
});

test('normal mode opens either tab without native resizing and restores prior coverage on close', () => {
  const f = fixture();
  f.open('market');
  assert.equal(f.result.visible, true);
  assert.equal(f.result.tab, 'market');
  assert.ok(f.surfaces.every(surface => surface.inert));
  assert.ok(f.classes.has('suit-open'));
  f.open('usage');
  assert.equal(f.result.tab, 'usage');
  assert.equal(f.calls.length, 0);
  f.close();
  assert.equal(f.result.visible, false);
  assert.deepEqual(f.surfaces.map(surface => surface.inert), [false, true]);
  assert.equal(f.classes.size, 0);
});

test('school drawer grows above the measured taskbar and tab changes retain one native focus lease', async () => {
  const f = fixture();
  f.update({ school: true, height: 64 });
  f.open();
  await f.flush();
  assert.equal(f.result.barHeight, 64);
  assert.equal(f.menuCalls.length, 1);
  assert.equal(f.menuCalls[0].extra, 820);
  assert.equal(f.focusCalls.length, 1);
  assert.equal(f.focusCalls[0].active, true);
  f.update({ height: 884 });
  f.open('market');
  await f.flush();
  assert.equal(f.result.barHeight, 64);
  assert.equal(f.menuCalls.length, 1);
  assert.equal(f.focusCalls.length, 1);
  f.close();
  assert.equal(f.menuCalls.at(-1).extra, 0);
  assert.equal(f.focusCalls.at(-1).active, false);
  assert.equal(f.focusCalls.at(-1).lease, 101);
});

test('school expansion respects available height', () => {
  const f = fixture({ availHeight: 600 });
  f.update({ school: true, height: 64 });
  f.open();
  assert.equal(f.menuCalls[0].extra, 504);
});

test('school browser preview never invokes native window or focus commands', async () => {
  const f = fixture({ tauri: false });
  f.update({ school: true, height: 64 });
  f.open();
  await f.flush();
  assert.equal(f.result.visible, true);
  assert.equal(f.calls.length, 0);
  f.close();
  assert.equal(f.calls.length, 0);
});

test('suit handoff closes Insights without collapsing the native school menu', async () => {
  const f = fixture();
  f.update({ school: true, height: 64 });
  f.open();
  await f.flush();
  f.update({ suitOpen: true });
  assert.equal(f.result.visible, false);
  assert.equal(f.menuCalls.length, 1);
  assert.equal(f.focusCalls.at(-1).lease, 101);
  f.open('market');
  assert.equal(f.result.visible, false);
  f.update({ suitOpen: false });
  assert.equal(f.result.visible, false);
  f.open('market');
  assert.equal(f.result.visible, true);
});

test('transition closes Insights and blocks reopening without reclaiming native geometry', async () => {
  const f = fixture();
  f.update({ school: true, height: 64 });
  f.open();
  await f.flush();
  f.update({ transitioning: true });
  assert.equal(f.result.visible, false);
  assert.equal(f.menuCalls.length, 1);
  f.open();
  assert.equal(f.result.visible, false);
  f.update({ transitioning: false, school: false });
  assert.equal(f.result.visible, false);
  assert.equal(f.classes.size, 0);
});

test('a native mode signal owns geometry before the transition prop arrives', async () => {
  const f = fixture();
  f.update({ school: true, height: 64 });
  f.open();
  await f.flush();
  f.signal();
  f.update();
  assert.equal(f.result.visible, false);
  assert.equal(f.menuCalls.length, 1, 'mode signal must suppress menu collapse before React receives transition state');
  f.open('market');
  assert.equal(f.result.visible, false);
  f.update({ transitioning: true });
  f.update({ transitioning: false, school: false });
  f.open('market');
  assert.equal(f.result.visible, true);
});

for (const intermediateRender of [false, true]) {
  test(`rapid mode cancellation permits a new Insights open${intermediateRender ? ' after an intervening idle render' : ' with batched signals'}`, () => {
    const f = fixture();
    f.signal('collapse');
    if (intermediateRender) f.update();
    f.signal('expand');
    f.update();
    f.open('market');
    assert.equal(f.result.visible, true);
    assert.equal(f.result.tab, 'market');
    assert.equal(f.menuCalls.length, 0);
  });
}

test('rapid mode cancellation dismisses the school drawer and releases its focus without collapsing native geometry', async () => {
  const f = fixture();
  f.update({ school: true, height: 64 });
  f.open();
  await f.flush();
  f.signal('collapse');
  f.signal('expand');
  f.update({ school: false, height: 900 });
  assert.equal(f.result.visible, false);
  assert.equal(f.menuCalls.length, 1);
  assert.equal(f.menuCalls[0].extra, 820);
  assert.deepEqual(f.focusCalls.filter(call => !call.active).map(call => call.lease), [101]);
  assert.equal(f.classes.size, 0);
  assert.deepEqual(f.surfaces.map(surface => surface.inert), [false, true]);
  f.open('market');
  assert.equal(f.result.visible, true);
  assert.equal(f.menuCalls.length, 1);
});

test('rapid mode cancellation releases a late Insights focus lease even before cleanup renders', async () => {
  const f = fixture({ deferFocus: true });
  f.update({ school: true, height: 64 });
  f.open();
  await f.flush();
  f.signal('collapse');
  f.signal('expand');
  f.focusPending[0](505);
  // Settle native acknowledgement before React can commit the dismissal.
  for (let i = 0; i < 16; i++) await Promise.resolve();
  assert.deepEqual(f.focusCalls.filter(call => !call.active).map(call => call.lease), [505]);
  f.update({ school: false, height: 900 });
  assert.equal(f.result.visible, false);
  assert.equal(f.menuCalls.length, 1);
  f.open('market');
  assert.equal(f.result.visible, true);
  assert.equal(f.menuCalls.length, 1);
});

test('rapid mode cancellation prevents a stale expansion acknowledgement from acquiring Insights focus', async () => {
  const f = fixture({ deferMenu: true });
  f.update({ school: true, height: 64 });
  f.open();
  f.signal('collapse');
  f.signal('expand');
  f.menuPending[0](null);
  for (let i = 0; i < 16; i++) await Promise.resolve();
  assert.equal(f.focusCalls.length, 0);
  f.update({ school: false, height: 900 });
  assert.equal(f.result.visible, false);
  assert.equal(f.menuCalls.length, 1);
  f.open();
  assert.equal(f.result.visible, true);
});

test('an expand signal during a committed transition keeps Insights hidden until the prop clears', () => {
  const f = fixture();
  f.signal('collapse');
  f.update({ transitioning: true });
  f.signal('expand');
  f.open('market');
  assert.equal(f.result.visible, false);
  assert.equal(f.menuCalls.length, 0);
  f.update({ transitioning: false });
  f.open('market');
  assert.equal(f.result.visible, true);
});

test('Insights waits for the shared mount reset and does not issue its own global reset', async () => {
  const f = fixture({ deferReset: true });
  f.update({ school: true, height: 64 });
  f.open();
  await f.flush();
  assert.equal(f.focusCalls.length, 0);
  f.resolveReset();
  await f.flush();
  assert.equal(f.focusCalls.length, 1);
  assert.equal(f.focusCalls[0].active, true);
});

test('late expansion and reset acknowledgements cannot refocus a closed drawer', async () => {
  const f = fixture({ deferMenu: true, deferReset: true });
  f.update({ school: true, height: 64 });
  f.open();
  f.close();
  f.menuPending[0](null);
  f.resolveReset();
  await f.flush();
  assert.equal(f.focusCalls.length, 0);
});

test('a late focus reply releases only its own lease after a new drawer acquires focus', async () => {
  const f = fixture({ deferFocus: true });
  f.update({ school: true, height: 64 });
  f.open();
  await f.flush();
  f.close();
  f.open('market');
  await f.flush();
  assert.equal(f.focusPending.length, 2);
  f.focusPending[1](202);
  await f.flush();
  f.focusPending[0](101);
  await f.flush();
  assert.equal(f.result.visible, true);
  assert.deepEqual(f.focusCalls.filter(call => !call.active).map(call => call.lease), [101]);
  f.close();
  assert.deepEqual(f.focusCalls.filter(call => !call.active).map(call => call.lease), [101, 202]);
});

test('a late focus reply after suit handoff releases its lease without collapsing the successor', async () => {
  const f = fixture({ deferFocus: true });
  f.update({ school: true, height: 64 });
  f.open();
  await f.flush();
  f.update({ suitOpen: true });
  f.focusPending[0](303);
  await f.flush();
  assert.deepEqual(f.focusCalls.filter(call => !call.active).map(call => call.lease), [303]);
  assert.equal(f.menuCalls.length, 1);
});

test('unmount restores coverage, removes listeners, collapses school geometry, and releases its lease', async () => {
  const f = fixture();
  f.update({ school: true, height: 64 });
  f.open();
  await f.flush();
  f.unmount();
  assert.equal(f.listeners.size, 0);
  assert.equal(f.classes.size, 0);
  assert.deepEqual(f.surfaces.map(surface => surface.inert), [false, true]);
  assert.equal(f.menuCalls.at(-1).extra, 0);
  assert.equal(f.focusCalls.at(-1).lease, 101);
});

test('focus acquisition completing after unmount releases only the returned lease', async () => {
  const f = fixture({ deferFocus: true });
  f.update({ school: true, height: 64 });
  f.open();
  await f.flush();
  f.unmount();
  f.focusPending[0](404);
  await f.flush();
  assert.deepEqual(f.focusCalls.filter(call => !call.active).map(call => call.lease), [404]);
  assert.equal(f.listeners.size, 0);
});
