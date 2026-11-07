// Budgets marked † in PLAN.md §11: initial JS ≤ 60 KB gz, CSS ≤ 8 KB gz, fonts ≤ 70 KB.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { gzipSync } from 'node:zlib';

const dist = new URL('../dist/', import.meta.url).pathname;
const assets = join(dist, 'assets');
const files = readdirSync(assets);
const gz = (f) => gzipSync(readFileSync(join(assets, f))).length;
const html = readFileSync(join(dist, 'index.html'), 'utf8');
const initial = files.filter((f) => f.endsWith('.js') && html.includes(f));
const js = initial.reduce((a, f) => a + gz(f), 0);
const css = files.filter((f) => f.endsWith('.css')).reduce((a, f) => a + gz(f), 0);
const fonts = files.filter((f) => f.endsWith('.woff2')).reduce((a, f) => a + statSync(join(assets, f)).size, 0);
const rows = [
  ['initial JS (gz)', js, 60 * 1024],
  ['CSS (gz)', css, 8 * 1024],
  ['fonts', fonts, 70 * 1024],
];
let bad = false;
for (const [name, v, max] of rows) {
  const ok = v <= max;
  bad ||= !ok;
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name.padEnd(16)} ${(v / 1024).toFixed(1)} KB / ${(max / 1024).toFixed(0)} KB`);
}
process.exit(bad ? 1 : 0);
