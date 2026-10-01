// Actual procedural Three geometry, with no browser, WebGL or native UI actions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');
const THREE = require('three');

function load(relative, dependencies = {}) {
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, relative), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const context = { exports: {}, require: (name) => dependencies[name] ?? require(name) };
  vm.runInNewContext(code, context);
  return context.exports;
}
const model = load('../lib/suit-diagnostics.ts');
const { createSuitGeometry } = load('../components/suit/suit-geometry.ts', {
  '@/lib/suit-diagnostics': model,
});

function resources(suit) {
  const geometry = new Set();
  const material = new Set();
  suit.root.traverse((object) => {
    if (object.geometry) geometry.add(object.geometry);
    if (object.material) {
      for (const item of Array.isArray(object.material) ? object.material : [object.material])
        material.add(item);
    }
  });
  return { geometry, material };
}

test('complete articulated armor has finite bounds, real extruded plates and all seven pickable regions', () => {
  const suit = createSuitGeometry();
  assert.deepEqual(
    [...new Set(suit.pickable.map((mesh) => mesh.userData.suitRegion))].sort(),
    Array.from(model.SUIT_REGIONS, ({ id }) => id).sort()
  );
  for (const n of [...suit.bounds.min.toArray(), ...suit.bounds.max.toArray()])
    assert.ok(Number.isFinite(n));
  assert.ok(suit.size.y > 7 && suit.size.y < 9, 'full body height');
  assert.ok(suit.size.x > 3 && suit.size.x < 5, 'spaced articulated arms');
  const plates = suit.pickable.filter((mesh) => mesh.geometry.type === 'ExtrudeGeometry');
  assert.ok(plates.length >= 30, 'layered beveled armor, not a flat silhouette');
  assert.ok(
    plates.some((mesh) => mesh.geometry.parameters.shapes.holes.length === 2),
    'helmet optical openings'
  );
  const ray = new THREE.Raycaster(new THREE.Vector3(0, 5.47, 8), new THREE.Vector3(0, 0, -1));
  assert.equal(ray.intersectObjects(suit.pickable, false)[0].object.userData.suitRegion, 'reactor');
  suit.dispose();
});

test('cyan optics remain in front of the shell through both faceplate openings', () => {
  const suit = createSuitGeometry();
  for (const yaw of [0, 0.48]) {
    for (const x of [-0.2, 0.2]) {
      const target = new THREE.Vector3(x, 7.15, 0.47);
      const origin = target
        .clone()
        .add(new THREE.Vector3(Math.sin(yaw) * 8, 0.4, Math.cos(yaw) * 8));
      const ray = new THREE.Raycaster(origin, target.clone().sub(origin).normalize());
      const hit = ray.intersectObjects(suit.pickable, false)[0];
      assert.equal(hit.object.userData.suitRegion, 'head');
      assert.equal(
        hit.object.material.color.getHexString(),
        'd6fcff',
        `visible cyan optical surface at yaw ${yaw}, x ${x}`
      );
    }
  }
  suit.dispose();
});

test('regional damage changes the chosen armor without recoloring other limbs', () => {
  const suit = createSuitGeometry();
  const neutral = structuredClone(model.SUIT_PRESETS.nominal.snapshot);
  for (const { id } of model.SUIT_REGIONS) neutral.damage[id] = 0;
  suit.update(neutral, 'head');
  const redPlate = (region) =>
    suit.pickable.find(
      (mesh) =>
        mesh.userData.suitRegion === region &&
        mesh.geometry.type === 'ExtrudeGeometry' &&
        mesh.material.color.getHexString() === 'a91d30'
    );
  const left = redPlate('leftArm');
  const right = redPlate('rightArm');
  assert.ok(left && right);
  const rightBefore = right.material.color.getHexString();
  neutral.damage.leftArm = 100;
  suit.update(neutral, 'leftArm');
  assert.notEqual(left.material.color.getHexString(), 'a91d30');
  assert.equal(right.material.color.getHexString(), rightBefore);
  assert.ok(left.material.roughness > right.material.roughness);
  assert.ok(
    left.children.some((line) => line.visible),
    'selected/damaged left armor overlay'
  );
  suit.dispose();
});

test('updates reuse geometry and materials, and closing disposes every GPU resource exactly once', () => {
  const suit = createSuitGeometry();
  const before = resources(suit);
  const disposal = new Map();
  for (const resource of [...before.geometry, ...before.material])
    resource.addEventListener('dispose', () =>
      disposal.set(resource, (disposal.get(resource) ?? 0) + 1)
    );
  for (const preset of Object.values(model.SUIT_PRESETS))
    for (const { id } of model.SUIT_REGIONS) suit.update(preset.snapshot, id);
  const after = resources(suit);
  assert.deepEqual(after.geometry, before.geometry);
  assert.deepEqual(after.material, before.material);
  suit.dispose();
  suit.dispose();
  assert.equal(disposal.size, before.geometry.size + before.material.size);
  assert.ok([...disposal.values()].every((count) => count === 1));
  assert.equal(suit.pickable.length, 0);
  assert.equal(suit.root.children.length, 0);
});
