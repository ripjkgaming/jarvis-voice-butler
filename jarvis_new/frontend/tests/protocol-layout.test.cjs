const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

const scope = { exports: {} };
vm.runInNewContext(
  ts.transpileModule(fs.readFileSync(path.join(__dirname, '../lib/protocol-layout.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText,
  scope
);
const plain = (v) => JSON.parse(JSON.stringify(v));
const {
  boxWidthFor,
  deviceBox,
  deviceCountLine,
  deviceFields,
  humanUptime,
  layoutGrid,
  portLine,
  sortDevices,
} = scope.exports;

const PLACEHOLDER =
  /\b(unknown|n\/a|none|null|undefined|nan|infinity|tbd|placeholder|lorem)\b|--|\?\?/i;

const full = {
  ip: '10.10.27.1',
  mac: 'AA:BB:CC:DD:EE:01',
  vendor: 'TP-Link Technologies',
  hostnames: ['router.lan'],
  os: 'Linux 5.4',
  osAccuracy: 96,
  osAlternatives: [{ name: 'OpenWrt 21.02', accuracy: 92 }],
  type: 'WAP',
  latencyMs: 1.234,
  uptimeSeconds: 3 * 86400 + 4 * 3600 + 5,
  lastBoot: 'Thu Oct 23 08:11:02 2026',
  hops: 1,
  ports: [
    { port: 53, proto: 'tcp', service: 'domain', product: 'dnsmasq', version: '2.89' },
    { port: 443, proto: 'tcp', service: 'https', tunnel: 'ssl', extra: 'web ui' },
  ],
};

test('a device with only an IP renders only the IP row', () => {
  const box = deviceBox({ ip: '10.10.27.9' }, 40);
  assert.deepEqual(plain(box.rows.map((r) => r.label)), ['IP']);
  assert.equal(box.title, '10.10.27.9');
  assert.equal(box.lines.length, 3);
  const text = box.lines.join('\n');
  for (const label of ['MAC', 'VENDOR', 'OS', 'TYPE', 'PORT', 'LATENCY', 'UPTIME', 'HOPS', 'HOST'])
    assert.ok(!text.includes(label), `${label} must not appear`);
});

test('null, empty and non-finite fields are omitted, never placeheld', () => {
  const box = deviceBox(
    {
      ip: '10.10.27.4',
      mac: null,
      vendor: '',
      hostnames: [],
      os: '   ',
      osAccuracy: 90,
      type: null,
      latencyMs: NaN,
      uptimeSeconds: undefined,
      hops: Infinity,
      ports: [{ port: 22, proto: 'tcp', service: null, product: undefined, version: '' }],
    },
    40
  );
  assert.deepEqual(plain(box.rows.map((r) => [r.label, r.value])), [
    ['IP', '10.10.27.4'],
    ['PORT', '22/tcp'],
  ]);
  assert.ok(!PLACEHOLDER.test(box.lines.join('\n')));
});

test('every present field appears with its real value', () => {
  const fields = Object.fromEntries(deviceFields(full).map(([k, v]) => [k + ':' + v, true]));
  for (const key of [
    'IP:10.10.27.1',
    'MAC:AA:BB:CC:DD:EE:01',
    'VENDOR:TP-Link Technologies',
    'HOST:router.lan',
    'OS:Linux 5.4 (96%)',
    'ALT OS:OpenWrt 21.02 (92%)',
    'TYPE:wap',
    'PORT:53/tcp domain dnsmasq 2.89',
    'PORT:443/tcp ssl/https (web ui)',
    'LATENCY:1.23 ms',
    'UPTIME:3d 4h',
    'BOOT:Thu Oct 23 08:11:02 2026',
    'HOPS:1',
  ])
    assert.ok(fields[key], `missing ${key}`);
  const box = deviceBox(full, 44);
  assert.equal(box.title, 'router.lan');
  assert.ok(!PLACEHOLDER.test(box.lines.join('\n')));
});

test('every box line is exactly the box width with rounded corners', () => {
  for (const w of [24, 36, 44, 58]) {
    const box = deviceBox({ ...full, isHost: true }, w);
    for (const line of box.lines) assert.equal(line.length, box.width);
    assert.equal(box.lines[0][0], '╭');
    assert.equal(box.lines[0].at(-1), '╮');
    assert.equal(box.lines.at(-1)[0], '╰');
    assert.equal(box.lines.at(-1).at(-1), '╯');
    assert.ok(box.lines[0].includes('[THIS HOST]'));
  }
  assert.ok(!deviceBox(full, 44).lines[0].includes('[THIS HOST]'));
});

test('long values wrap onto unlabelled continuation rows', () => {
  const port = {
    port: 8080,
    proto: 'tcp',
    service: 'http-proxy',
    product: 'A very long product name indeed',
    version: '12.3.4-beta',
  };
  const box = deviceBox({ ip: '10.10.27.7', ports: [port] }, 30);
  assert.equal(box.rows.filter((r) => r.label === 'PORT').length, 1);
  assert.ok(box.rows.length > 2);
  assert.ok(box.rows.slice(2).every((r) => r.label === ''));
  const joined = box.rows
    .slice(1)
    .map((r) => r.value)
    .join(' ');
  assert.equal(joined, portLine(port));
  for (const line of box.lines) assert.equal(line.length, box.width);
});

test('count line is exactly devices.length', () => {
  assert.equal(deviceCountLine([]), 'DEVICES: 0');
  assert.equal(deviceCountLine(undefined), 'DEVICES: 0');
  const devs = Array.from({ length: 8 }, (_, i) => ({ ip: `10.10.27.${i + 1}` }));
  assert.equal(deviceCountLine(devs), 'DEVICES: 8');
  assert.equal(sortDevices(devs).length, 8);
});

test('uptime formatting', () => {
  assert.equal(humanUptime(0), '0s');
  assert.equal(humanUptime(42.9), '42s');
  assert.equal(humanUptime(60), '1m');
  assert.equal(humanUptime(7 * 60 + 59), '7m');
  assert.equal(humanUptime(3600), '1h');
  assert.equal(humanUptime(5 * 3600 + 12 * 60), '5h 12m');
  assert.equal(humanUptime(86400), '1d');
  assert.equal(humanUptime(12 * 86400 + 3 * 3600 + 59 * 60), '12d 3h');
  assert.equal(humanUptime(-5), '0s');
});

test('host sorts first, then IPv4 numerically', () => {
  const out = sortDevices([
    { ip: '10.10.27.100' },
    { ip: '10.10.27.9' },
    { ip: '10.10.27.20', isHost: true },
    { ip: '10.10.27.10' },
  ]);
  assert.deepEqual(plain(out.map((d) => d.ip)), [
    '10.10.27.20',
    '10.10.27.9',
    '10.10.27.10',
    '10.10.27.100',
  ]);
});

test('grid packs every box once, within the terminal width, without overlap', () => {
  const devs = sortDevices(
    Array.from({ length: 9 }, (_, i) => ({
      ip: `10.10.27.${i + 1}`,
      ports: Array.from({ length: i % 4 }, (_, k) => ({ port: 20 + k, proto: 'tcp' })),
    }))
  );
  for (const cols of [30, 80, 140, 220]) {
    const w = boxWidthFor(cols);
    assert.ok(w <= Math.max(24, cols));
    const boxes = devs.map((d) => deviceBox(d, w));
    const grid = layoutGrid(boxes, cols);
    assert.equal(grid.cells.length, devs.length);
    assert.ok(grid.width <= Math.max(cols, w));
    for (let a = 0; a < grid.cells.length; a++)
      for (let b = a + 1; b < grid.cells.length; b++) {
        const A = grid.cells[a];
        const B = grid.cells[b];
        const overlap = A.x < B.x + B.w && B.x < A.x + A.w && A.y < B.y + B.h && B.y < A.y + A.h;
        assert.ok(!overlap, `cells ${a} and ${b} overlap at ${cols} cols`);
      }
  }
  assert.deepEqual(plain(layoutGrid([], 100)), { cells: [], columns: 0, width: 0, height: 0 });
});
