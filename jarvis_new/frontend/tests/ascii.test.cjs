/* eslint-disable @typescript-eslint/no-require-imports */
const assert = require('node:assert/strict'),
  fs = require('node:fs'),
  path = require('node:path'),
  vm = require('node:vm'),
  test = require('node:test'),
  ts = require('typescript');

function load() {
  const scope = { exports: {} };
  vm.runInNewContext(
    ts.transpileModule(fs.readFileSync(path.join(__dirname, '../lib/ascii.ts'), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    }).outputText,
    scope
  );
  return scope.exports;
}
const A = load();

test('fit pads, aligns and truncates to exact width', () => {
  assert.equal(A.fit('ab', 4), 'ab  ');
  assert.equal(A.fit('ab', 4, 'r'), '  ab');
  assert.equal(A.fit('ab', 5, 'c'), ' ab  ');
  assert.equal(A.fit('abcdef', 4), 'abc…');
  assert.equal(A.fit('abc', 0), '');
});

test('bar is always exactly width cells and clamps', () => {
  for (const p of [-20, 0, 3, 50, 99, 100, 180, NaN]) {
    for (const fine of [false, true]) {
      const [on, off] = A.barParts(p, 12, { fine });
      assert.equal([...(on + off)].length, 12, `pct=${p} fine=${fine}`);
    }
  }
  assert.equal(A.bar(50, 10), '█████░░░░░');
  assert.equal(A.bar(100, 4), '████');
  assert.equal(A.bar(0, 4), '░░░░');
  assert.equal(A.barParts(55, 10, { fine: true }).join('|'), '█████▌|░░░░');
});

test('sparkline maps range, resamples and right-aligns short history', () => {
  assert.equal(A.sparkline([0, 100], 2), '▁█');
  assert.equal(A.sparkline([50], 4), '···▅');
  assert.equal(
    [
      ...A.sparkline(
        Array.from({ length: 100 }, (_, i) => i),
        10
      ),
    ].length,
    10
  );
  assert.equal(A.sparkline([], 3), '···');
});

test('blockChart returns rows x width with a filled bottom row', () => {
  const rows = A.blockChart([1, 2, 3, 4], 4, 3);
  assert.equal(rows.length, 3);
  rows.forEach((r) => assert.equal([...r].length, 4));
  assert.equal(rows[0][3], '█');
  assert.notEqual(rows[2][0], ' ');
});

test('table fits columns and draws a cross rule', () => {
  const out = A.table(
    [
      ['ID', 'NAME'],
      ['1', 'reactor'],
    ],
    [{ width: 3, align: 'r' }, { width: 5 }],
    { ruleAfter: 0 }
  );
  assert.equal(out.join('\n'), [' ID │ NAME ', '────┼──────', '  1 │ reac…'].join('\n'));
});

test('frame boxes lines at an exact width with a title', () => {
  const out = A.frame(['hi'], 14, { title: 'SYS' });
  out.forEach((l) => assert.equal([...l].length, 14));
  assert.ok(out[0].startsWith('┌─[ SYS'));
  assert.equal(out[2], '└────────────┘');
});

test('lamp and spin', () => {
  assert.equal(A.lamp('on'), '■');
  assert.equal(A.lamp('off'), '□');
  assert.equal(A.spin(-1), A.SPIN[A.SPIN.length - 1]);
});

test('reticleArt is rows x cols with a centre mark and ring glyphs', () => {
  const art = A.reticleArt(31, 15);
  assert.equal(art.length, 15);
  art.forEach((l) => assert.equal([...l].length, 31));
  assert.equal(art[7][15], '┼');
  assert.ok(art.join('').includes('─') && art.join('').includes('│'));
});
