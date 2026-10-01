// Actual production hook in a deterministic React-effect harness; no native UI.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');
const compile = file => ts.transpileModule(fs.readFileSync(path.join(__dirname, '..', file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
function fixture({ deferFocus = false, deferReset = false } = {}) {
  let index = 0, dirty = false, result, command, school = false, transitioning = false;
  let geometry = { height: 900 }, mode, effects = [], cells = [];
  const calls = [], posts = [], focusPending = [], resetPending = [], listeners = new Map(), classes = new Set();
  const surfaces = [{ inert: false }, { inert: true }];
  const root = { dataset: {}, classList: { add: x => classes.add(x), remove: x => classes.delete(x) } };
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const react = {
    useRef(value) { const i = index++; return cells[i] ??= { current: value }; },
    useState(value) { const i = index++; cells[i] ??= { value }; return [cells[i].value, next => { const value = typeof next === 'function' ? next(cells[i].value) : next; if (!Object.is(value, cells[i].value)) { cells[i].value = value; dirty = true; } }]; },
    useCallback(fn, deps) { const i = index++; if (!same(cells[i]?.deps, deps)) cells[i] = { deps, fn }; return cells[i].fn; },
    useEffect(fn, deps) { const i = index++; if (!same(cells[i]?.deps, deps)) { const old = cells[i]; cells[i] = { deps }; effects.push(() => { old?.cleanup?.(); cells[i].cleanup = fn(); }); } },
  };
  react.useLayoutEffect = react.useEffect;
  const model = { exports: {} };
  vm.runInNewContext(compile('lib/suit-diagnostics.ts'), model);
  const deps = {
    react,
    '@/hooks/hud/use-window-geometry': { useWindowGeometry: () => geometry, refreshWindowGeometry: () => {} },
    '@/lib/suit-diagnostics': model.exports,
    '@/lib/tauri': { isTauri: () => true, invoke: (name, args) => { calls.push({ name, ...args }); if (name === 'suit_focus' && args.active) return deferFocus ? new Promise(resolve => focusPending.push(resolve)) : Promise.resolve(123); if (name === 'suit_focus' && !args.active && args.lease === null && deferReset) return new Promise(resolve => resetPending.push(resolve)); return Promise.resolve(null); } },
    '@/lib/bridge': { bridgePost: (url, body) => new Promise(resolve => posts.push({ url, body, resolve })) },
  };
  const scope = { exports: {}, require: name => { assert.ok(deps[name], name); return deps[name]; },
    Date: { now: () => 1000 },
    window: { screen: { availHeight: 1080 }, addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) },
    document: { documentElement: root, querySelectorAll: () => surfaces },
  };
  vm.runInNewContext(compile('hooks/hud/use-suit-diagnostics.ts'), scope);
  function render() { let turns = 0; do { assert.ok(++turns < 20, 'Effect loop'); dirty = false; index = 0; result = scope.exports.useSuitDiagnostics(command, school, transitioning, mode); const pending = effects; effects = []; pending.forEach(fn => fn()); } while (dirty); return result; }
  render();
  return { get calls() { return calls.filter(call => call.name === "school_menu"); }, get focusCalls() { return calls.filter(call => call.name === "suit_focus"); }, posts, focusPending, resetPending, root, classes, surfaces, listeners,
    get result() { return result; },
    update(next = {}) { if ('command' in next) command = next.command; if ('school' in next) school = next.school; if ('transitioning' in next) transitioning = next.transitioning; if ('height' in next) geometry = { height: next.height }; if ('mode' in next) mode = next.mode; return render(); },
    signal() { listeners.get('jarvis-school')?.(); },
    async flush() { for (let i = 0; i < 12; i++) await Promise.resolve(); return render(); },
    unmount() { cells.forEach(cell => cell.cleanup?.()); },
  };
}
const cmd = (revision, op = 'open', issued_at = 1001) => ({ op, revision, issued_at, session: 'test' });
test('default and stale opens are inert; fresh open disables only covered surfaces', () => {
  const f = fixture();
  assert.equal(f.result.open, false); assert.equal(f.calls.length, 0); assert.equal(f.posts.length, 0);
  assert.equal(f.update({ command: cmd(1, 'open', 999) }).open, false);
  assert.equal(f.update({ command: cmd(2) }).open, true);
  assert.ok(f.surfaces.every(surface => surface.inert)); assert.ok(f.classes.has('suit-open'));
  f.update({ command: cmd(3, 'close') });
  assert.deepEqual(f.surfaces.map(x => x.inert), [false, true]); assert.equal(f.classes.size, 0);
});
test('local dismissal is immediate and stale shared polls cannot reopen it', async () => {
  const f = fixture(); f.update({ command: cmd(1) }); f.result.close(); f.update();
  assert.equal(f.result.open, false); assert.equal(f.posts[0].url, '/suit'); assert.equal(f.posts[0].body.open, false);
  assert.equal(f.update({ command: cmd(1) }).open, false);
  f.posts[0].resolve({ suit_diagnostics: cmd(2, 'close') }); await f.flush();
  assert.equal(f.update({ command: cmd(1) }).open, false);
  assert.equal(f.update({ command: cmd(3) }).open, true);
});
test('school expansion retains measured taskbar height, repeated opens never shrink it', () => {
  const f = fixture(); f.update({ school: true, height: 64 }); f.update({ command: cmd(1) });
  assert.equal(f.result.barHeight, 64); assert.equal(f.calls.at(-1).extra, 720);
  f.update({ height: 784, command: cmd(2) });
  assert.equal(f.result.barHeight, 64); assert.equal(f.calls.length, 2); assert.ok(f.calls.every(x => x.extra === 720));
  f.update({ command: cmd(3, 'close') }); assert.equal(f.calls.at(-1).extra, 0);
});
test('school transition dismisses without stealing native geometry', () => {
  const f = fixture(); f.update({ school: true, height: 64, command: cmd(1) });
  f.signal(); f.update({ transitioning: true });
  assert.equal(f.result.open, false); assert.equal(f.calls.length, 1); assert.equal(f.classes.size, 0);
  f.update({ transitioning: false, school: false, height: 900 }); assert.equal(f.result.open, false);
});
test('fresh voice command during transition waits for its completion', () => {
  const f = fixture(); f.update({ transitioning: true, command: cmd(1) });
  assert.equal(f.result.open, false); assert.equal(f.calls.length, 0);
  assert.equal(f.update({ transitioning: false }).open, true);
});
test('unmount releases listeners, inert state and school expansion', () => {
  const f = fixture(); f.update({ school: true, height: 64, command: cmd(1) }); f.unmount();
  assert.equal(f.listeners.size, 0); assert.equal(f.classes.size, 0); assert.equal(f.calls.at(-1).extra, 0);
  assert.deepEqual(f.surfaces.map(x => x.inert), [false, true]);
});

test('school diagnostics takes keyboard focus and releases it on close', async () => {
  const f = fixture(); f.update({ school: true, height: 64, command: cmd(1) });
  await f.flush(); assert.equal(f.focusCalls.at(-1).active, true);
  f.update({ command: cmd(2, 'close') }); assert.equal(f.focusCalls.at(-1).active, false);
});
test('late expansion acknowledgement cannot refocus a dismissed dialog', async () => {
  const f = fixture(); f.update({ school: true, height: 64, command: cmd(1) });
  f.update({ command: cmd(2, 'close') }); await f.flush();
  assert.ok(f.focusCalls.every(call => !call.active));
});

test('focus acquire resolving after dismissal releases only its returned lease', async () => {
  const f = fixture({ deferFocus: true }); f.update({ school: true, height: 64, command: cmd(1) });
  await f.flush(); assert.equal(f.focusPending.length, 1);
  f.update({ command: cmd(2, 'close') }); f.focusPending[0](456); await f.flush();
  assert.equal(f.focusCalls.at(-1).active, false); assert.equal(f.focusCalls.at(-1).lease, 456);
});
test('fresh mount resets orphaned focus before acquiring current modal focus', async () => {
  const f = fixture({ deferReset: true }); f.update({ school: true, height: 64, command: cmd(1) });
  await f.flush(); assert.ok(f.focusCalls.every(call => !call.active));
  assert.equal(f.focusCalls[0].lease, null); f.resetPending[0](null); await f.flush();
  assert.equal(f.focusCalls.at(-1).active, true);
  f.unmount(); assert.equal(f.focusCalls.at(-1).lease, 123);
});

test('first canonical school snapshot collapses an orphaned expanded reload once', () => {
  const f = fixture(); f.update({ mode: 'school', height: 784, command: cmd(1, 'open', 999) });
  assert.equal(f.result.open, false); assert.equal(f.calls.length, 1); assert.equal(f.calls[0].extra, 0);
  f.update({ mode: 'school', school: true, height: 64 });
  f.update({ command: cmd(2), mode: 'school' });
  assert.equal(f.calls.filter(call => call.extra === 0).length, 1); assert.equal(f.calls.at(-1).extra, 720);
});
test('initial normal mode and an in-progress transition never trigger reload geometry cleanup', () => {
  const normal = fixture(); normal.update({ mode: 'normal' }); normal.update({ mode: 'school' }); assert.equal(normal.calls.length, 0);
  const transition = fixture(); transition.update({ mode: 'school', transitioning: true }); transition.update({ transitioning: false }); assert.equal(transition.calls.length, 0);
});
