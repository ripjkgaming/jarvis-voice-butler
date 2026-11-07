// Subset the vendored HUD fonts for the phone app (PLAN.md §1 "Fonts").
// Uses fontTools from the repo's uv venv (already a dependency). Outputs are
// committed in src/fonts/, so this only re-runs when the glyph set changes.
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, '../..');
const out = resolve(here, '../src/fonts');
mkdirSync(out, { recursive: true });

// Every non-ASCII glyph that frontend/lib/ascii.ts can emit, plus the app's own.
const ascii = readFileSync(resolve(repo, 'frontend/lib/ascii.ts'), 'utf8');
const extra = new Set('⌗⌬◁▲▶●○◐↵⚡─│┌┐└┘·…▓▒░█');
for (const m of ascii.matchAll(/'((?:[^'\\]|\\.)*)'/g)) for (const c of m[1]) if (c > '\x7f') extra.add(c);
const unicodes = ['U+0020-007E', ...[...extra].map((c) => 'U+' + c.codePointAt(0).toString(16).toUpperCase())];
const LABEL = 'U+0020-007E';

const jobs = [
  ['frontend/fonts/CommitMono-400-Regular.otf', 'commit-400.woff2', unicodes.join(',')],
  ['frontend/fonts/CommitMono-700-Regular.otf', 'commit-700.woff2', unicodes.join(',')],
  ['frontend/fonts/Rajdhani-SemiBold.ttf', 'rajdhani-600.woff2', LABEL],
];
for (const [src, name, set] of jobs) {
  execFileSync(
    'uv',
    [
      'run', '--project', repo, 'python', '-m', 'fontTools.subset', resolve(repo, src),
      `--unicodes=${set}`, '--flavor=woff2', '--layout-features=kern,liga,calt',
      '--no-hinting', '--desubroutinize', `--output-file=${resolve(out, name)}`,
    ],
    { stdio: 'inherit', cwd: repo },
  );
  console.log('wrote', name);
}
