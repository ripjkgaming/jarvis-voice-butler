// Pure renderer tests: no browser, GPU, bridge, or native desktop actions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');

function fixture() {
  const setup = { arrays: 0, gradients: 0, canvases: 0 };
  const calls = [];
  function canvas() {
    setup.canvases += 1;
    const ctx = { globalAlpha: 1 };
    for (const name of ['clearRect', 'setTransform', 'moveTo', 'lineTo', 'arc']) {
      ctx[name] = (...n) => calls.push([name, ...n]);
    }
    for (const name of [
      'beginPath',
      'closePath',
      'fill',
      'stroke',
      'fillRect',
      'quadraticCurveTo',
      'bezierCurveTo',
    ])
      ctx[name] = () => {};
    ctx.drawImage = (_atlas, ...n) => calls.push(['sprite', ...n]);
    ctx.createRadialGradient = () => {
      setup.gradients += 1;
      return { addColorStop() {} };
    };
    return {
      width: 0,
      height: 0,
      getContext: () => ctx,
      getBoundingClientRect() {
        throw Error('No frame-time layout reads');
      },
    };
  }
  const source = fs.readFileSync(path.join(__dirname, '../lib/school-entry-scene.ts'), 'utf8');
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const scope = {
    exports: {},
    document: { createElement: canvas },
    Float32Array: class extends Float32Array {
      constructor(...n) {
        super(...n);
        setup.arrays += 1;
      }
    },
    requestAnimationFrame() {
      throw Error('Caller owns frame clock');
    },
    getComputedStyle() {
      throw Error('No frame-time style reads');
    },
  };
  vm.runInNewContext(code, scope);
  const screen = canvas();
  return { screen, calls, setup, scene: scope.exports.createEntryScene(screen, '#5fe3ff') };
}
const center = (f) =>
  f.calls
    .filter((c) => c[0] === 'arc')
    .at(-1)
    .slice(1, 3);
const near = (a, b) => a.forEach((v, i) => assert.ok(Math.abs(v - b[i]) < 1e-8));

test('all phases reuse native-DPR sprites and seeded storage without frame-time gradients', () => {
  const f = fixture();
  f.scene.resize(1201, 901, 2.5);
  assert.equal(f.screen.width, 3003);
  assert.equal(f.screen.height, 2253);
  assert.ok(f.calls.some((c) => c[0] === 'setTransform' && c[1] === 2.5));
  const allocated = { ...f.setup };
  for (const phase of [
    'aperture',
    'compress',
    'transfer-out',
    'transfer-in',
    'descent',
    'forge',
    'reveal',
  ]) {
    f.calls.length = 0;
    for (let i = 0; i <= 12; i++) f.scene.draw(phase, i / 12, 530, 400, 64, 8, 'left');
    assert.ok(
      f.calls.some((c) => c[0] === 'sprite'),
      phase
    );
    assert.equal(f.calls.filter((c) => c[0] === 'clearRect').length, 13);
    for (const call of f.calls)
      for (const n of call.slice(1)) assert.ok(Number.isFinite(n), `${phase}: ${call}`);
  }
  assert.deepEqual(f.setup, allocated);
  f.scene.resize(1201, 901, 2.5);
  assert.deepEqual(f.setup, allocated);
});

test('cross-monitor core leaves and receives through opposite facing edges in every direction', () => {
  const f = fixture();
  f.scene.resize(1600, 900, 1);
  for (const [dir, leaving, arriving] of [
    ['left', [-70, 380], [1670, 380]],
    ['right', [1670, 380], [-70, 380]],
    ['up', [720, -70], [720, 970]],
    ['down', [720, 970], [720, -70]],
  ]) {
    for (const [phase, progress, wanted] of [
      ['transfer-out', 0, [720, 380]],
      ['transfer-out', 1, leaving],
      ['transfer-in', 0, arriving],
      ['transfer-in', 1, [720, 380]],
    ]) {
      f.calls.length = 0;
      f.scene.draw(phase, progress, 720, 380, 64, 0, dir);
      near(center(f), wanted);
    }
  }
});

test('native viewport resizes reuse the glow atlas until DPR changes', () => {
  const f = fixture();
  f.scene.resize(1200, 700, 2);
  const initial = f.setup.gradients;
  f.scene.resize(1600, 900, 2);
  assert.equal(f.setup.gradients, initial);
  assert.equal(f.screen.width, 3200);
  assert.equal(f.screen.height, 1800);
  f.scene.resize(1600, 64, 2);
  assert.equal(f.setup.gradients, initial);
  f.scene.resize(1600, 900, 2.5);
  assert.equal(f.setup.gradients, initial + 3);
  assert.equal(f.screen.width, 4000);
  assert.equal(f.screen.height, 2250);
  f.scene.resize(1600, 900, 2.5);
  assert.equal(f.setup.gradients, initial + 3);
});

test('compression anchors reactor and descent lands precisely on target taskbar center', () => {
  const f = fixture();
  f.scene.resize(1920, 1080, 1.5);
  for (const [phase, progress, wanted] of [
    ['compress', 0.5, [648, 410]],
    ['descent', 0, [648, 410]],
    ['descent', 1, [960, 1042]],
  ]) {
    f.calls.length = 0;
    f.scene.draw(phase, progress, 648, 410, 60, 8);
    near(center(f), wanted);
  }
});

test('articulated wings seat on the exact inset/bar rectangle and final reveal is transparent', () => {
  const f = fixture();
  f.scene.resize(1920, 1080, 2);
  f.calls.length = 0;
  f.scene.draw('forge', 1, 650, 400, 60, 8);
  assert.deepEqual(
    f.calls.find((c) => c[0] === 'moveTo'),
    ['moveTo', 16, 1012]
  );
  assert.ok(f.calls.some((c) => c[0] === 'lineTo' && c[1] === 1904 && c[2] === 1012));
  f.calls.length = 0;
  f.scene.draw('reveal', 1, 650, 400, 60, 8);
  assert.deepEqual(f.calls, [['clearRect', 0, 0, 1920, 1080]]);
});

test('wing panels hinge above the dock before locking inside its final rectangle', () => {
  const f = fixture();
  f.scene.resize(1600, 900, 2);
  f.calls.length = 0;
  f.scene.draw('forge', 0.3, 720, 380, 64, 8);
  const points = f.calls.filter((c) => c[0] === 'moveTo' || c[0] === 'lineTo');
  assert.ok(
    points.some((c) => c[2] < 900 - 64 - 8 - 25),
    'unfolded panels have a readable raised silhouette'
  );
  assert.ok(points.some((c) => c[1] < 800) && points.some((c) => c[1] > 800), 'both wings unfold');
  f.calls.length = 0;
  f.scene.draw('forge', 1, 720, 380, 64, 8);
  for (const c of f.calls.filter((c) => c[0] === 'moveTo' || c[0] === 'lineTo')) {
    assert.ok(c[1] >= 8 && c[1] <= 1592, `locked wing x remains on the dock: ${c}`);
    assert.ok(c[2] >= 828 && c[2] <= 892, `locked wing y remains on the dock: ${c}`);
  }
});

test('seeking through another phase cannot leave geometry behind on a repeated pose', () => {
  const f = fixture();
  f.scene.resize(1440, 900, 1.5);
  for (const phase of ['aperture', 'compress', 'transfer-in', 'descent', 'forge', 'reveal']) {
    f.calls.length = 0;
    f.scene.draw(phase, 0.45, 620, 370, 60, 8, 'up');
    const wanted = [...f.calls];
    f.scene.draw('forge', 1, 620, 370, 60, 8, 'down');
    f.scene.draw('aperture', 0.1, 620, 370, 60, 8, 'left');
    f.calls.length = 0;
    f.scene.draw(phase, 0.45, 620, 370, 60, 8, 'up');
    assert.deepEqual(f.calls, wanted, phase);
  }
});

test('bounded progress is deterministic and disposal releases backing pixels', () => {
  const f = fixture();
  f.scene.resize(1280, 720, 1);
  f.calls.length = 0;
  f.scene.draw('compress', 1, 600, 320, 64, 0);
  const complete = [...f.calls];
  f.calls.length = 0;
  f.scene.draw('compress', 7, 600, 320, 64, 0);
  assert.deepEqual(f.calls, complete);
  f.scene.dispose();
  assert.equal(f.screen.width * f.screen.height, 1);
  f.calls.length = 0;
  f.scene.draw('forge', 0.5, 600, 320, 64, 0);
  f.scene.resize(1920, 1080, 2);
  f.scene.clear();
  assert.deepEqual(f.calls, []);
});
