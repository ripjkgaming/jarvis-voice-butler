// Rasterise the launcher icons from docs/phone-app/icon/*.svg (PLAN.md §13)
// with inkscape, and copy mark-glyphs.json into the app. Outputs are committed.
import { execFileSync } from 'node:child_process';
import { copyFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, '../../docs/phone-app/icon');
const out = resolve(here, '../public/icons');
mkdirSync(out, { recursive: true });

const jobs = [
  ['icon-squircle.svg', 'icon-192.png', 192],
  ['icon-squircle.svg', 'icon-512.png', 512],
  ['icon-maskable.svg', 'icon-maskable-512.png', 512],
  ['ic_launcher_monochrome.svg', 'icon-mono-512.png', 512],
  ['icon-squircle.svg', 'apple-touch-icon.png', 180],
];
for (const [svg, png, size] of jobs) {
  execFileSync(
    'inkscape',
    [resolve(src, svg), '--export-type=png', `--export-filename=${resolve(out, png)}`, `-w`, `${size}`, `-h`, `${size}`, '--export-background-opacity=0'],
    { stdio: ['ignore', 'ignore', 'inherit'] },
  );
  console.log('wrote', png);
}
copyFileSync(resolve(src, 'mark-glyphs.json'), resolve(here, '../src/motion/mark-glyphs.json'));
console.log('copied mark-glyphs.json');
