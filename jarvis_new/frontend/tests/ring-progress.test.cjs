// Run from frontend: node --test tests/ring-progress.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../lib/ring-progress.ts'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const scope = { exports: {}, Math };
vm.runInNewContext(compiled, scope, { filename: 'ring-progress.ts' });
const R = scope.exports;

const task = (id, pct, over = {}) => ({
  id,
  tag: 'TASK',
  title: id,
  stage: '',
  pct,
  state: 'running',
  tier: 'amber',
  ...over,
});

// Run a spring to `ms` of wall time in fixed steps of `step` ms.
function run(target, ms, step, from = { x: 0, v: 0 }) {
  const s = { ...from };
  for (let t = 0; t < ms - 1e-9; t += step) R.stepSpring(s, target, step);
  return s;
}

test('spring is frame-rate independent (closed form)', () => {
  const a = run(1, 600, 1000 / 144);
  const b = run(1, 600, 1000 / 60);
  const c = run(1, 600, 1000 / 30);
  // same wall time (within one step), different sampling: states agree closely
  const ref = run(1, 600, 600);
  for (const s of [a, b, c]) assert.ok(Math.abs(s.x - ref.x) < 0.02, `${s.x} vs ${ref.x}`);
  // exact semigroup: 2 x dt/2 == 1 x dt
  const one = { x: 0.2, v: 0.5 };
  const two = { x: 0.2, v: 0.5 };
  R.stepSpring(one, 0.9, 40);
  R.stepSpring(two, 0.9, 20);
  R.stepSpring(two, 0.9, 20);
  assert.ok(Math.abs(one.x - two.x) < 1e-9);
  assert.ok(Math.abs(one.v - two.v) < 1e-9);
});

test('spring never overshoots and is monotone toward the target', () => {
  for (const step of [4, 16.7, 33, 100]) {
    const s = { x: 0, v: 0 };
    let prev = 0;
    for (let i = 0; i < 400; i++) {
      R.stepSpring(s, 0.8, step);
      assert.ok(s.x <= 0.8 + 1e-12 && s.x >= prev - 1e-12);
      prev = s.x;
    }
    assert.equal(s.x, 0.8); // settles exactly
  }
  // target changes mid-flight with momentum: still no overshoot
  const s = { x: 0, v: 0 };
  for (let i = 0; i < 20; i++) R.stepSpring(s, 1, 16);
  for (let i = 0; i < 300; i++) {
    R.stepSpring(s, s.x + 0.001, 16);
    assert.ok(s.x <= 1 + 1e-9);
  }
});

test('progress jumping backwards eases back with no bounce', () => {
  const s = { x: 0, v: 0 };
  for (let i = 0; i < 25; i++) R.stepSpring(s, 0.9, 16); // moving up fast
  assert.ok(s.v > 0);
  let prev = s.x;
  for (let i = 0; i < 200; i++) {
    R.stepSpring(s, 0.2, 16);
    assert.ok(s.x <= prev + 1e-12, 'never goes up again');
    assert.ok(s.x >= 0.2 - 1e-12, 'never undershoots');
    prev = s.x;
  }
  assert.equal(s.x, 0.2);
});

test('dampExp is frame-rate independent', () => {
  let a = 0;
  let b = 0;
  for (let i = 0; i < 60; i++) a = R.dampExp(a, 1, 1000 / 60, 200);
  for (let i = 0; i < 144; i++) b = R.dampExp(b, 1, 1000 / 144, 200);
  assert.ok(Math.abs(a - b) < 1e-9);
});

test('sweepArc is continuous and bounded', () => {
  const o = { head: 0, len: 0 };
  let prev = null;
  for (let t = 0; t < 12000; t += 8) {
    R.sweepArc(t, o);
    assert.ok(o.len >= 19.9 && o.len <= 100.1);
    assert.ok(o.head >= 0 && o.head < 360);
    if (prev !== null) {
      const d = Math.min(Math.abs(o.head - prev), 360 - Math.abs(o.head - prev));
      assert.ok(d < 3, `head step ${d}`);
    }
    prev = o.head;
  }
});

test('ringLayout: rings never overlap and stay inside the band', () => {
  const o = { r: 0, w: 0 };
  for (let n = 1; n <= R.MAX_RINGS; n++) {
    let prevInner = Infinity;
    for (let i = 0; i < n; i++) {
      R.ringLayout(n, i, o);
      const outer = o.r + o.w / 2;
      const inner = o.r - o.w / 2;
      assert.ok(outer < prevInner, `n=${n} i=${i} overlap`);
      assert.ok(outer <= 256 && inner >= 222, `n=${n} i=${i} band ${inner}..${outer}`);
      prevInner = inner;
    }
  }
});

test('collectTasks converts 0..100 to 0..1, maps tiers, dedupes research', () => {
  const t = R.collectTasks(
    [{ id: 'p1', title: 'Deep dive', stage: 'Reading', progress: 40 }],
    [
      { id: 'p1', kind: 'research', title: 'Deep dive', progress: 40, status: 'running' },
      { id: 'd1', kind: 'download', title: 'iso', progress: null, status: 'running' },
      { id: 'b1', kind: 'build', title: 'tests', progress: 250, status: 'done' },
    ]
  );
  assert.equal(t.map((x) => x.id).join(), 'r:p1,a:d1,a:b1');
  assert.equal(t[0].pct, 0.4);
  assert.equal(t[0].tier, 'cyan');
  assert.equal(t[1].pct, null);
  assert.equal(t[1].tier, 'ice');
  assert.equal(t[2].pct, 1); // clamped
  assert.equal(t[2].state, 'done');
  assert.equal(R.collectTasks(null, undefined).length, 0);
});

test('capTasks folds the overflow into one +N aggregate ring', () => {
  const many = Array.from({ length: 9 }, (_, i) => task(`t${i}`, i / 10));
  const capped = R.capTasks(many);
  assert.equal(capped.length, R.MAX_RINGS);
  const more = capped[capped.length - 1];
  assert.equal(more.id, R.MORE_ID);
  assert.equal(more.tag, '+4');
  assert.ok(Math.abs(more.pct - (0.5 + 0.6 + 0.7 + 0.8) / 4) < 1e-12);
  const few = R.capTasks(many.slice(0, 3));
  assert.equal(few.length, 3);
  // terminal tasks pass through and don't consume ring slots
  const mixed = R.capTasks([...many.slice(0, 6), task('x', 1, { state: 'done' })]);
  assert.equal(mixed.length, 7);
  assert.equal(mixed.filter((t) => t.state === 'running').length, 6);
});

function drive(bank, ms, fps, t0 = 0, reduced = false) {
  const dt = 1000 / fps;
  let t = t0;
  for (let e = 0; e < ms - 1e-9; e += dt) {
    t += dt;
    bank.step(t, dt, reduced);
  }
  return t;
}

test('bank: 6 simultaneous rings get distinct stable slots and layout', () => {
  const bank = new R.RingBank();
  const tasks = Array.from({ length: 6 }, (_, i) => task(`t${i}`, 0.1 * (i + 1)));
  bank.sync(tasks, 0);
  drive(bank, 3000, 60);
  assert.equal(bank.count, 6);
  const shown = bank.slots.filter((s) => s.shown);
  assert.equal(shown.length, 6);
  const radii = shown.map((s) => s.r);
  assert.equal([...radii].sort((a, b) => b - a).join(), radii.join()); // outer -> inner in slot order
  for (const s of shown) assert.ok(Math.abs(s.fill.x - s.pct) < 1e-3);
  const slotOf = Object.fromEntries(shown.map((s) => [s.id, s.idx]));
  bank.sync(
    tasks.map((t) => ({ ...t, pct: Math.min(1, t.pct + 0.1) })),
    3000
  );
  drive(bank, 500, 60, 3000);
  for (const s of bank.slots.filter((x) => x.shown)) assert.equal(s.idx, slotOf[s.id]);
});

test('bank: target change is smooth and identical at 30 vs 144 fps', () => {
  const sample = (fps) => {
    const bank = new R.RingBank();
    bank.sync([task('a', 0.2)], 0);
    let t = drive(bank, 2000, fps);
    bank.sync([task('a', 0.8)], t);
    t = drive(bank, 300, fps, t);
    return bank.slots[0].fill.x;
  };
  const lo = sample(30);
  const hi = sample(144);
  assert.ok(lo > 0.2 && lo < 0.8);
  assert.ok(Math.abs(lo - hi) < 0.015, `${lo} vs ${hi}`);
});

test('bank: completion eases to 100%, flashes, fades, then frees the slot', () => {
  const bank = new R.RingBank();
  bank.sync([task('a', 0.5)], 0);
  let t = drive(bank, 2000, 60);
  bank.sync([task('a', 0.5, { state: 'done' })], t);
  assert.equal(bank.slots[0].phase, 'completing');
  const trail = [];
  const dt = 1000 / 60;
  for (let e = 0; e < 4000; e += dt) {
    t += dt;
    bank.step(t, dt);
    trail.push({ x: bank.slots[0].fill.x, f: bank.slots[0].flash, p: bank.slots[0].phase });
  }
  assert.ok(
    trail.some((r) => r.p === 'flash' && r.f > 0.5),
    'flash happened'
  );
  const bad = trail.findIndex((r, i) => i > 0 && r.x < trail[i - 1].x - 1e-9);
  assert.equal(bad, -1, JSON.stringify(trail.slice(Math.max(0, bad - 1), bad + 2)));
  assert.ok(trail.some((r) => r.x === 1));
  assert.equal(bank.slots[0].phase, 'free');
  assert.equal(bank.burst, 1);
  // a done item that lingers in the feed must not respawn the ring
  bank.sync([task('a', 1, { state: 'done' })], t);
  assert.equal(bank.slots[0].phase, 'free');
});

test('bank: job vanishing near the end completes; vanishing early just fades', () => {
  const bank = new R.RingBank();
  bank.sync([task('late', 0.95), task('early', 0.3)], 0);
  let t = drive(bank, 2000, 60);
  bank.sync([], t);
  const by = Object.fromEntries(bank.slots.filter((s) => s.id).map((s) => [s.id, s.phase]));
  assert.equal(by.late, 'completing');
  assert.equal(by.early, 'leaving');
  drive(bank, 4000, 60, t);
  assert.ok(bank.slots.every((s) => s.phase === 'free'));
});

test('bank: failed job fades without completing', () => {
  const bank = new R.RingBank();
  bank.sync([task('a', 0.6)], 0);
  let t = drive(bank, 1500, 60);
  bank.sync([task('a', 0.6, { state: 'failed' })], t);
  assert.equal(bank.slots[0].phase, 'leaving');
  drive(bank, 2000, 60, t);
  assert.equal(bank.slots[0].phase, 'free');
  assert.equal(bank.burst, 0);
});

test('bank: indeterminate waits a beat, sweeps, then cross-fades to a real value', () => {
  const bank = new R.RingBank();
  bank.sync([task('a', null)], 0);
  bank.step(100, 16);
  assert.equal(bank.count, 0, 'hidden during the hold');
  let t = drive(bank, 1500, 60, 100);
  assert.equal(bank.count, 1);
  assert.equal(bank.slots[0].indet, 1);
  bank.sync([task('a', 0.5)], t);
  const seq = [];
  const dt = 1000 / 60;
  for (let e = 0; e < 1500; e += dt) {
    t += dt;
    bank.step(t, dt);
    seq.push(bank.slots[0].indet);
  }
  assert.ok(
    seq.every((v, i) => i === 0 || v <= seq[i - 1] + 1e-12),
    'monotone fade'
  );
  assert.equal(bank.slots[0].indet, 0);
  // a blip that ends inside the hold never appears
  const b2 = new R.RingBank();
  b2.sync([task('x', null)], 0);
  b2.step(200, 16);
  b2.sync([], 200);
  b2.step(300, 16);
  assert.ok(b2.slots.every((s) => s.phase === 'free'));
});

test('bank: reduced motion snaps values', () => {
  const bank = new R.RingBank();
  bank.sync([task('a', 0.7)], 0);
  bank.step(16, 16, true);
  assert.equal(bank.slots[0].fill.x, 0.7);
  assert.equal(bank.slots[0].op, 1);
});

test('bank: focus cycles live rings and parks on a completing one', () => {
  const bank = new R.RingBank();
  bank.sync([task('a', 0.3), task('b', 0.4)], 0);
  let t = drive(bank, 1000, 60);
  const seen = new Set();
  const dt = 1000 / 60;
  for (let e = 0; e < 12000; e += dt) {
    t += dt;
    bank.step(t, dt);
    seen.add(bank.focus);
  }
  assert.equal([...seen].sort().join(), '0,1');
  bank.sync([task('a', 0.3), task('b', 0.4, { state: 'done' })], t);
  t += dt;
  bank.step(t, dt);
  assert.equal(bank.focus, 1);
});

test('lagging feed echo of a finished job never reopens its ring', () => {
  const bank = new R.RingBank();
  bank.sync([task('r:p1', 0.9)], 0);
  bank.step(16, 16);
  // done reported, then a stale "running" echo arrives (other poll clock)
  bank.sync([task('r:p1', 0.9, { state: 'done' })], 1000);
  bank.sync([task('r:p1', 0.9)], 1100);
  let t = 1100;
  for (let i = 0; i < 600; i++) bank.step((t += 16), 16);
  assert.equal(bank.slots.filter((s) => s.phase !== 'free').length, 0);
});

test('collectTasks: research fed by /sys and /activity is one id; terminal wins', () => {
  const rs = [{ id: 'p1', title: 'edu', progress: 88 }];
  const act = (status) => [
    { id: 'project-p1', kind: 'research', title: 'edu', progress: 88, status },
  ];
  let out = R.collectTasks(rs, act('running'));
  assert.equal(out.length, 1);
  out = R.collectTasks([], act('running'));
  assert.equal(JSON.stringify(out.map((x) => x.id)), '["r:p1"]');
  out = R.collectTasks(rs, act('cancelled'));
  assert.equal(out.length, 1);
  assert.equal(out[0].state, 'cancelled');
});
