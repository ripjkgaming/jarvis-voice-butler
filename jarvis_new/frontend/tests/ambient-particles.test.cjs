// Run from frontend: node --test tests/ambient-particles.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');

function fixture() {
  const frames = new Map();
  const counts = { canvases: 0, gradients: 0, arrays: 0, imageDraws: 0 };
  let serial = 0;
  function canvas() {
    counts.canvases += 1;
    const calls = [];
    const context = {
      clearRect(...args) {
        calls.push(['clear', ...args]);
      },
      drawImage(_image, ...args) {
        counts.imageDraws += 1;
        calls.push(['image', ...args, context.globalAlpha]);
      },
      setTransform(...args) {
        calls.push(['transform', ...args]);
      },
      createRadialGradient() {
        counts.gradients += 1;
        return { addColorStop() {} };
      },
      fillRect() {},
      beginPath() {},
      arc() {},
      fill() {},
      moveTo() {},
      lineTo() {},
      closePath() {},
      stroke() {},
    };
    return {
      width: 0,
      height: 0,
      calls,
      context,
      getContext() {
        return context;
      },
      getBoundingClientRect() {
        throw new Error('Drawing must not read layout');
      },
    };
  }
  const source = fs.readFileSync(path.join(__dirname, '../lib/ambient-particles.ts'), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const scope = {
    exports: {},
    document: {
      createElement(tag) {
        assert.equal(tag, 'canvas');
        return canvas();
      },
    },
    requestAnimationFrame(fn) {
      const id = ++serial;
      frames.set(id, fn);
      return id;
    },
    cancelAnimationFrame(id) {
      frames.delete(id);
    },
    Float32Array: class extends Float32Array {
      constructor(...args) {
        super(...args);
        counts.arrays += 1;
      }
    },
    getComputedStyle() {
      throw new Error('Drawing must not read styles');
    },
  };
  vm.runInNewContext(compiled, scope, { filename: 'ambient-particles.ts' });
  return {
    ...scope.exports,
    frames,
    counts,
    canvas,
    advance(timestamp) {
      const callbacks = [...frames.values()];
      frames.clear();
      callbacks.forEach((fn) => fn(timestamp));
    },
  };
}

test('multiple ambient surfaces share one frame and release it on last unsubscribe', () => {
  const f = fixture();
  const a = [],
    b = [];
  const stopA = f.subscribeAmbientFrame((delta) => a.push(delta));
  const stopB = f.subscribeAmbientFrame((delta) => b.push(delta));
  assert.equal(f.frames.size, 1);
  f.advance(100);
  f.advance(116);
  assert.deepEqual(a, [0, 16]);
  assert.deepEqual(b, [0, 16]);
  assert.equal(f.frames.size, 1);
  stopA();
  f.advance(132);
  assert.deepEqual(a, [0, 16]);
  assert.deepEqual(b, [0, 16, 16]);
  stopB();
  assert.equal(f.frames.size, 0);
  stopB(); // React cleanup is safe even if already paused.
  assert.equal(f.frames.size, 0);
});

test('resume starts without catching up hidden time and compositor stalls stay bounded', () => {
  const f = fixture();
  const deltas = [];
  let stop = f.subscribeAmbientFrame((delta) => deltas.push(delta));
  f.advance(100);
  f.advance(116);
  f.advance(3116);
  assert.deepEqual(deltas, [0, 16, 34]);
  stop();
  stop = f.subscribeAmbientFrame((delta) => deltas.push(delta));
  f.advance(90000);
  f.advance(90008);
  assert.deepEqual(deltas, [0, 16, 34, 0, 8]);
  stop();
});

test('unsubscribing the last surface inside its frame leaves no orphan loop', () => {
  const f = fixture();
  let draws = 0;
  const stop = f.subscribeAmbientFrame(() => {
    draws += 1;
    stop();
  });
  f.advance(0);
  assert.equal(draws, 1);
  assert.equal(f.frames.size, 0);
  f.advance(16);
  assert.equal(draws, 1);
});

test('cached dust drift repeats position and velocity exactly at its twelve-second seam', () => {
  const f = fixture();
  const atStart = f.ambientDustOffset(0);
  const atEnd = f.ambientDustOffset(12);
  const before = f.ambientDustOffset(12 - 0.001);
  const after = f.ambientDustOffset(12 + 0.001);
  const startBefore = f.ambientDustOffset(-0.001);
  const startAfter = f.ambientDustOffset(0.001);
  assert.deepEqual(atEnd, atStart);
  for (const axis of ['x', 'y']) {
    const seamVelocity = (after[axis] - before[axis]) / 0.002;
    const startVelocity = (startAfter[axis] - startBefore[axis]) / 0.002;
    assert.ok(Math.abs(seamVelocity - startVelocity) < 1e-6, `${axis} velocity jumps at loop seam`);
  }
});

test('all variants cap decorative backing resolution at 1.5 DPR', () => {
  for (const variant of ['hud', 'bar', 'menu']) {
    const f = fixture();
    const canvas = f.canvas();
    const scene = f.createAmbientScene(canvas, variant);
    scene.resize(1201, 63, 2.5);
    assert.equal(canvas.width, 1802);
    assert.equal(canvas.height, 95);
    assert.ok(canvas.calls.some((call) => call[0] === 'transform' && call[1] === 1.5));
    assert.ok(canvas.calls.some((call) => call[0] === 'image'));
    scene.resize(801, 47, 3);
    assert.equal(canvas.width, 1202);
    assert.equal(canvas.height, 71);
    scene.dispose();
  }
});

test('background dust is baked once; live draws are throttled and reuse the sprite atlas', () => {
  for (const variant of ['hud', 'bar', 'menu']) {
    const f = fixture();
    const canvas = f.canvas();
    const scene = f.createAmbientScene(canvas, variant);
    scene.resize(1600, variant === 'bar' ? 64 : 900, 2);
    const setup = { ...f.counts };
    canvas.calls.length = 0;
    for (let frame = 0; frame < 120; frame++) scene.draw(1000 / 120);
    assert.equal(f.counts.gradients, setup.gradients);
    assert.equal(f.counts.arrays, setup.arrays);
    const rasterDraws = canvas.calls.filter((call) => call[0] === 'clear').length;
    assert.ok(rasterDraws >= 28 && rasterDraws <= 31, `${variant}: ${rasterDraws} draws`);
    assert.ok(canvas.calls.filter((call) => call[0] === 'image').length < rasterDraws * 40);
    const imageDraws = f.counts.imageDraws - setup.imageDraws;
    const expectedLiveSprites = variant === 'hud' ? 33 : variant === 'bar' ? 13 : 9;
    assert.ok(
      imageDraws <= rasterDraws * expectedLiveSprites + 2,
      `${variant}: ${imageDraws} image draws over ${rasterDraws} paints`
    );
    // Repeated identical geometry/state notifications must not rebuild the atlas.
    const beforeNoop = { ...f.counts };
    scene.resize(1600, variant === 'bar' ? 64 : 900, 2);
    scene.setState('idle');
    assert.deepEqual(f.counts, beforeNoop);
    scene.dispose();
  }
});

test('a long draw stall produces the same particle positions as one bounded frame', () => {
  function positions(delta) {
    const f = fixture();
    const canvas = f.canvas();
    const scene = f.createAmbientScene(canvas, 'hud');
    scene.resize(1280, 720, 1);
    canvas.calls.length = 0;
    scene.draw(delta);
    return canvas.calls;
  }
  assert.deepEqual(positions(5000), positions(34));
  assert.deepEqual(positions(-100), positions(0));
});

test('frame rate does not change particle motion speed', () => {
  function positions(deltas) {
    const f = fixture();
    const canvas = f.canvas();
    const scene = f.createAmbientScene(canvas, 'bar');
    scene.resize(1600, 64, 1);
    for (const delta of deltas) {
      canvas.calls.length = 0;
      scene.draw(delta);
    }
    return canvas.calls;
  }
  assert.deepEqual(positions([16, 16]), positions([32]));
});

test('disposed and zero-size scenes draw nothing and release their backing pixels', () => {
  const f = fixture();
  const canvas = f.canvas();
  const scene = f.createAmbientScene(canvas, 'menu');
  scene.resize(0, 0, 2);
  assert.equal(canvas.calls.filter((call) => call[0] === 'image').length, 0);
  scene.resize(500, 400, 2);
  scene.dispose();
  assert.equal(canvas.width * canvas.height, 1);
  canvas.calls.length = 0;
  scene.draw(16);
  assert.equal(canvas.calls.length, 0);
});
