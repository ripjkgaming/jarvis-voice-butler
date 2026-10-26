const assert = require('node:assert/strict'),
  fs = require('node:fs'),
  vm = require('node:vm'),
  ts = require('typescript');
const source = fs.readFileSync(
  require('node:path').join(__dirname, '../hooks/hud/use-baked-frames.ts'),
  'utf8'
);
const effects = [],
  timers = new Map(),
  published = [];
let id = 0,
  stateIndex = 0,
  draws = [];
const scope = {
  exports: {},
  require: () => ({
    useRef: (v) => ({ current: v }),
    useEffect: (fn) => effects.push(fn),
    useState: () => {
      const n = stateIndex++;
      return [
        n % 2 ? { w: 8, h: 8 } : null,
        (v) => {
          if (n % 2 === 0) published.push(v);
        },
      ];
    },
  }),
  document: { createElement: () => ({ getContext: () => ({ clearRect() {} }) }) },
  setTimeout: (fn) => {
    timers.set(++id, fn);
    return id;
  },
  clearTimeout: (i) => timers.delete(i),
};
vm.runInNewContext(
  ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText,
  scope
);
const opts = {
  frames: 48,
  fps: 24,
  incremental: true,
  cacheId: 'test',
  bakeKey: 'listening',
  render: (ctx, w, h, i, n, rng) => draws.push([i, n, rng()]),
};
const start = () => {
  effects.length = 0;
  scope.exports.useBakedCanvas(opts);
  return effects.at(-1)();
};
const step = () => {
  const [i, fn] = timers.entries().next().value;
  timers.delete(i);
  fn();
};
let cleanup = start();
assert.equal(draws.length, 0);
step();
assert.equal(draws.length, 1);
assert.equal(published.at(-1).frames.length, 1);
step();
cleanup();
assert.equal(timers.size, 0);
assert.equal(published.length, 1);
draws = [];
cleanup = start();
while (timers.size) step();
assert.equal(draws.length, 48);
assert.equal(published.at(-1).frames.length, 48);
assert.deepEqual(
  draws.map((d) => d[0]),
  Array.from({ length: 48 }, (_, i) => i)
);
assert.ok(draws.every((d) => d[1] === 48));
const seeded = scope.exports.mulberry32(1);
assert.deepEqual(
  draws.map((d) => d[2]),
  Array.from({ length: 48 }, () => seeded())
);
const before = draws.length;
start();
assert.equal(timers.size, 0);
assert.equal(draws.length, before);
assert.equal(published.at(-1).frames.length, 48);
opts.frames = 1;
opts.bakeKey = 'still';
opts.bakeDelay = 96;
const beforeStill = draws.length;
start();
assert.equal(draws.length, beforeStill);
step();
assert.equal(draws.length, beforeStill + 1);
assert.equal(published.at(-1).frames.length, 1);
assert.equal(timers.size, 0);
start();
assert.equal(draws.length, beforeStill + 1);
console.log(
  'PASS: one frame per task, first still, cancellation, complete-only cache, frame ordering and deterministic RNG, cache hit'
);
