// Historical renderer fixture checks; production entry is covered by school-entry-scene tests.
// Run from frontend: node --test tests/render-buffers.test.cjs
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

function loadRenderer(filename = path.join(__dirname, 'fixtures/school-transition-legacy.tsx')) {
  const source =
    fs.readFileSync(filename, 'utf8') +
    '\nexport const probe = { drawTracer, drawPool, buildRim };';
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  const scope = { exports: {}, require: () => ({}), Float64Array };
  vm.runInNewContext(compiled, scope, { filename: 'school-transition.tsx' });
  return scope.exports.probe;
}

function canvas() {
  const commands = [];
  let gradient = 0;
  const methods = {};
  for (const name of [
    'save',
    'restore',
    'beginPath',
    'closePath',
    'moveTo',
    'lineTo',
    'roundRect',
    'clip',
    'stroke',
    'arc',
    'fill',
  ]) {
    methods[name] = (...args) => commands.push([name, ...args]);
  }
  for (const name of ['createLinearGradient', 'createRadialGradient']) {
    methods[name] = (...args) => {
      const id = ++gradient;
      commands.push([name, id, ...args]);
      return { id, addColorStop: (...stop) => commands.push(['addColorStop', id, ...stop]) };
    };
  }
  const ctx = new Proxy(methods, {
    set(target, key, value) {
      commands.push(['set', key, typeof value === 'object' ? ['gradient', value.id] : value]);
      target[key] = value;
      return true;
    },
  });
  return { ctx, commands };
}

const tracer = (changes = {}) => ({
  s0: 10,
  dir: 1,
  dist: 2000,
  tail0: 80,
  t0: 0,
  dur: 1000,
  seed: 1,
  landed: false,
  ...changes,
});
const pool = (changes = {}) => ({
  ph: 64,
  total: 2,
  arrived: 1,
  level: 0.7,
  spread: 0.8,
  settle: 0.3,
  alpha: 0.8,
  held: true,
  inset: 8,
  ...changes,
});
const scene = (api) => ({ rim: api.buildRim(640, 360), pool: pool() });
const digest = (value) => createHash('sha256').update(JSON.stringify(value)).digest('hex');

function tracerCommands(api) {
  const frames = [];
  for (const dir of [-1, 1])
    for (const tail0 of [0, 80, 740]) {
      const s = scene(api);
      const tr = tracer({ dir, tail0 });
      for (const now of [-1, 0, 16, 150, 500, 999, 1000, 1075, 1200, 1280, 1400]) {
        const c = canvas();
        api.drawTracer(c.ctx, s, tr, now, '#22d3ee');
        frames.push([dir, tail0, now, tr.landed, s.pool.arrived, c.commands]);
      }
    }
  return frames;
}

function poolCommands(api) {
  const frames = [];
  for (const inset of [0, 8]) {
    const p = pool({ inset });
    for (const w of [47.5, 640, 1920, 320])
      for (const spread of [0, 0.003, 0.37, 0.9999999, 1, 0.2]) {
        p.spread = spread;
        const c = canvas();
        api.drawPool(c.ctx, p, w, 360.25, 470, 0.016, [95, 227, 255]);
        frames.push([inset, w, spread, c.commands]);
      }
    p.held = false;
    for (const now of [500, 516, 550]) {
      const c = canvas();
      api.drawPool(c.ctx, p, 640, 360, now, 0.034, [168, 85, 247]);
      frames.push([now, p.level, p.spread, c.commands]);
    }
  }
  return frames;
}

// Fixed command-stream goldens captured before buffer reuse. They cover every
// style, gradient, path command and exact coordinate, including frame sequences.
// They do not load a second renderer or depend on a saved development checkout.
const TRACER_GOLDEN = '1d60e47c931fedca09d03cdfbfc6986bdc812ef0d818ad30a56465d089c163ce';
const POOL_GOLDEN = '3e0bb0234dde8a8c3e5eb8098b0af054bd4e0683f717020edf8d8fddf8c34d5d';
const api = loadRenderer();

test('tracer command streams preserve both directions, long tails and landing boundaries', () => {
  assert.equal(digest(tracerCommands(api)), TRACER_GOLDEN);
});

test('pool command streams preserve fractional endpoints, insets, resizes and arrival easing', () => {
  assert.equal(digest(poolCommands(api)), POOL_GOLDEN);
});

test('tracers allocate only when visible and reuse double-precision coordinates', () => {
  const tr = tracer();
  const s = scene(api);
  api.drawTracer(canvas().ctx, s, tr, -1, '#22d3ee');
  assert.equal(tr.points, undefined);
  api.drawTracer(canvas().ctx, s, tr, 500, '#22d3ee');
  const points = tr.points;
  assert.ok(points.x instanceof Float64Array);
  assert.ok(points.y instanceof Float64Array);
  assert.equal(points.x.length, 101);
  api.drawTracer(canvas().ctx, s, tr, 516, '#22d3ee');
  assert.equal(tr.points, points);
  api.drawTracer(canvas().ctx, s, tr, 1200, '#22d3ee');
  assert.equal(tr.points, points);
  const c = canvas();
  api.drawTracer(c.ctx, s, tr, 1400, '#22d3ee');
  assert.equal(tr.points, points);
  assert.deepEqual(c.commands, []);
});

test('tracer buffers reserve normal tail growth and expand only for a larger tail', () => {
  const tr = tracer({ tail0: 0 });
  const s = scene(api);
  api.drawTracer(canvas().ctx, s, tr, 150, '#22d3ee');
  const first = tr.points;
  assert.equal(first.x.length, 101);
  for (const now of [166, 200, 250, 300, 500, 1000, 1200]) {
    api.drawTracer(canvas().ctx, s, tr, now, '#22d3ee');
    assert.equal(tr.points, first);
  }
  tr.tail0 = 740;
  api.drawTracer(canvas().ctx, s, tr, 0, '#22d3ee');
  assert.notEqual(tr.points, first);
  assert.ok(tr.points.x.length > first.x.length);
  const grown = tr.points;
  for (const now of [150, 500, 1200]) {
    api.drawTracer(canvas().ctx, s, tr, now, '#22d3ee');
    assert.equal(tr.points, grown);
  }
  const long = tracer({ tail0: 740 });
  api.drawTracer(canvas().ctx, s, long, 0, '#22d3ee');
  assert.equal(long.points.x.length, Math.ceil(740 / 3) + 1);
});

test('each tracer owns its scratch; drawing another tracer cannot overwrite it', () => {
  const a = tracer();
  const b = tracer({ dir: -1, seed: 2 });
  const s = scene(api);
  api.drawTracer(canvas().ctx, s, a, 500, '#22d3ee');
  const coordinates = a.points.x.slice();
  api.drawTracer(canvas().ctx, s, b, 500, '#22d3ee');
  assert.notEqual(a.points, b.points);
  assert.deepEqual(a.points.x, coordinates);
});

test('pool buffers reserve normal spread and grow only for wider frames', () => {
  const p = pool({ spread: 0 });
  api.drawPool(canvas().ctx, p, 640, 360, 0, 0.016, [95, 227, 255]);
  assert.equal(p.surface, undefined);
  p.spread = 0.01;
  api.drawPool(canvas().ctx, p, 640, 360, 16, 0.016, [95, 227, 255]);
  const first = p.surface;
  assert.ok(first instanceof Float64Array);
  assert.equal(first.length, 181);
  for (const spread of [0.1, 0.3, 0.7, 1, 0.2]) {
    p.spread = spread;
    api.drawPool(canvas().ctx, p, 640, 360, 32, 0.016, [95, 227, 255]);
    assert.equal(p.surface, first);
  }
  api.drawPool(canvas().ctx, p, 1920, 1080, 48, 0.016, [95, 227, 255]);
  assert.notEqual(p.surface, first);
  const grown = p.surface;
  assert.ok(grown.length > first.length);
  api.drawPool(canvas().ctx, p, 320, 240, 64, 0.016, [95, 227, 255]);
  assert.equal(p.surface, grown);
  p.alpha = 0;
  const c = canvas();
  api.drawPool(c.ctx, p, 3840, 2160, 80, 0.016, [95, 227, 255]);
  assert.equal(p.surface, grown);
  assert.deepEqual(c.commands, []);
});

test('pool buffers also grow for an actual surface wider than the normal spread', () => {
  const p = pool({ spread: 0.01 });
  api.drawPool(canvas().ctx, p, 640, 360, 0, 0.016, [95, 227, 255]);
  const first = p.surface;
  p.spread = 2;
  api.drawPool(canvas().ctx, p, 640, 360, 16, 0.016, [95, 227, 255]);
  assert.notEqual(p.surface, first);
  assert.equal(p.surface.length, 361);
  const grown = p.surface;
  p.spread = 1;
  api.drawPool(canvas().ctx, p, 640, 360, 32, 0.016, [95, 227, 255]);
  assert.equal(p.surface, grown);
});

test('pool fill and meniscus use exactly the same sampled coordinates', () => {
  const c = canvas();
  const p = pool({ spread: 0.9999999, inset: 0 });
  api.drawPool(c.ctx, p, 640.3, 360.2, 100.7, 0.016, [95, 227, 255]);
  const paths = c.commands.reduce((all, command) => {
    if (command[0] === 'beginPath') all.push([]);
    else if (command[0] === 'moveTo' || command[0] === 'lineTo') all.at(-1).push(command.slice(1));
    return all;
  }, []);
  assert.deepEqual(paths[0].slice(1, -1), paths[1]);
  assert.equal(paths[1].length, Math.floor((p.spread * (640.3 / 2 + 40)) / 2) + 1);
});
