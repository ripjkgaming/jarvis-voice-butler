/** Jarvis ASCII engine: glyph atlas, arc-reactor field, glyph wipe and text
 * decode. Every surface paints pre-rendered glyph sprites (one drawImage per
 * cell), so a frame costs no text shaping, gradients or allocations. */

export const RAMP = ' .·:-=+*#%▓█';
export const NOISE = '▓▒░#%&@$01<>/\\=+*';
const CHARS = RAMP + NOISE;
export const TAU = Math.PI * 2;

export const clamp = (x: number, a = 0, b = 1) => (x < a ? a : x > b ? b : x);
export const frac = (x: number) => x - Math.floor(x);
export function sstep(a: number, b: number, x: number) {
  const t = clamp((x - a) / (b - a));
  return t * t * (3 - 2 * t);
}
export function hash(x: number, y: number) {
  const n = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
  return n - Math.floor(n);
}
function ring(r: number, c: number, w: number) {
  const d = (r - c) / w;
  return d > -3 && d < 3 ? Math.exp(-d * d) : 0;
}

type RGB = [number, number, number];
export function hexRgb(hex: string): RGB {
  const h = hex.replace('#', '');
  const v = h.length === 3 ? h.replace(/./g, (c) => c + c) : h.slice(0, 6);
  const n = parseInt(v, 16);
  return Number.isFinite(n) ? [(n >> 16) & 255, (n >> 8) & 255, n & 255] : [95, 227, 255];
}
function mixRgb(a: RGB, b: RGB, t: number): RGB {
  const u = clamp(t);
  return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u];
}

export const LEVELS = 10;

/** One sprite per (glyph, brightness level), tinted from deep → state → white. */
export function glyphAtlas(cw: number, ch: number, dpr: number, accent: string) {
  const canvas = document.createElement('canvas');
  const sw = Math.ceil(cw * dpr);
  const sh = Math.ceil(ch * dpr);
  canvas.width = sw * CHARS.length;
  canvas.height = sh * LEVELS;
  const ctx = canvas.getContext('2d');
  const base = hexRgb(accent);
  const deep = mixRgb([2, 9, 15], base, 0.45);
  const hot: RGB = mixRgb(base, [255, 255, 255], 0.7);
  if (ctx) {
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = `600 ${Math.round(ch * 0.86 * dpr)}px ${asciiFontFamily()}`;
    for (let l = 0; l < LEVELS; l++) {
      const t = l / (LEVELS - 1);
      const c = t < 0.6 ? mixRgb(deep, base, t / 0.6) : mixRgb(base, hot, (t - 0.6) / 0.4);
      ctx.fillStyle = `rgb(${c[0] | 0},${c[1] | 0},${c[2] | 0})`;
      for (let i = 0; i < CHARS.length; i++) {
        ctx.fillText(CHARS[i], i * sw + sw / 2, l * sh + sh / 2 + dpr * 0.5);
      }
    }
  }
  return {
    canvas,
    sw,
    sh,
    /** Draw glyph `index` (into RAMP+NOISE) at brightness 0..1. */
    draw(target: CanvasRenderingContext2D, index: number, bright: number, x: number, y: number) {
      const l = Math.min(LEVELS - 1, Math.max(0, Math.round(bright * (LEVELS - 1))));
      target.drawImage(canvas, index * sw, l * sh, sw, sh, x, y, cw, ch);
    },
  };
}
export type GlyphAtlas = ReturnType<typeof glyphAtlas>;
export const noiseIndex = (r: number) =>
  RAMP.length + (Math.floor(r * NOISE.length) % NOISE.length);

const NORMALS = [0, 1, 2].map((k) => {
  const a = Math.PI / 2 + (k * TAU) / 3;
  return [Math.cos(a), Math.sin(a)];
});

export type ReactorFrame = {
  /** seconds since assembly began; ≥ 2.2 fully built */
  build: number;
  t: number;
  /** extra core brightness (speaking, activation) */
  boost: number;
  /** spin multiplier (thinking spins faster) */
  speed: number;
  /** expanding shockwave: radius and strength, or null */
  shock: [number, number] | null;
};

/** Arc-reactor intensity at normalised (nx, ny), r = 1 at the outer rim.
 * Returns [intensity, hot] where hot pushes the sprite toward white. */
export function reactorField(
  nx: number,
  ny: number,
  f: ReactorFrame,
  aa: number
): [number, number] {
  const r = Math.hypot(nx, ny);
  let I = 0;
  let hot = 0;
  const b = f.build;
  const t = f.t * f.speed;
  if (r < 1.08) {
    const u = (((Math.atan2(ny, nx) / TAU) % 1) + 1) % 1;
    const aRim = sstep(0, 0.8, b);
    if (u < aRim) {
      I += ring(r, 0.94, Math.max(0.03, aa)) * 0.85;
      if (r > 0.995 && r < 1.07 && frac(u * 48 + t * 0.09) < 0.3) I += 0.42;
    }
    const aDash = sstep(0.4, 1, b);
    if (aDash && frac(u * 36 - t * 0.22) < 0.55)
      I += ring(r, 0.81, Math.max(0.02, aa * 0.8)) * 0.7 * aDash;
    const aCoil = clamp((b - 0.7) / 0.7) * 10;
    if (r > 0.45 && r < 0.73 && aCoil > 0) {
      const sp = u * 10 + t * 0.55;
      const fp = frac(sp);
      if (fp > 0.1 && fp < 0.9 && Math.floor(sp) % 10 < aCoil) {
        const band = sstep(0.45, 0.5, r) * (1 - sstep(0.68, 0.73, r));
        I += band * (fp < 0.2 || fp > 0.8 ? 0.78 : 0.5);
      }
    }
    if (r > 0.4 && r < 0.92)
      I += Math.exp(-frac(frac(t * 0.42) - u) * 7) * 0.32 * Math.min(aCoil, 1);
    const aInner = sstep(1.2, 1.5, b);
    if (aInner) I += ring(r, 0.38, Math.max(0.025, aa * 0.8)) * 0.95 * aInner;
    const aTri = sstep(1.4, 1.9, b);
    if (aTri && r < 0.4) {
      const cr = Math.cos(-t * 0.6);
      const sr = Math.sin(-t * 0.6);
      const px = nx * cr - ny * sr;
      const py = nx * sr + ny * cr;
      let m = -1;
      for (const [a, c] of NORMALS) m = Math.max(m, px * a + py * c);
      const e = 0.72 * Math.exp(-(((m - 0.15) / Math.max(0.022, aa * 0.7)) ** 2)) * aTri;
      I += e + (m < 0.15 ? 0.14 * aTri : 0);
      hot += e;
    }
    const aCore = sstep(1.7, 2.2, b);
    if (aCore && r < 0.6) {
      const pulse = 0.88 + 0.12 * Math.sin(f.t * 7);
      const c = Math.exp(-((r / 0.13) ** 2)) * aCore * pulse * (1 + f.boost);
      I += c + Math.exp(-((r / 0.55) ** 2)) * 0.13 * aCore * (1 + f.boost * 2);
      hot += c;
    }
  }
  if (f.shock) {
    const s = ring(r, f.shock[0], 0.06) * f.shock[1];
    I += s;
    hot += s * 0.6;
  }
  return [I, hot];
}

/** Scrambled text that resolves left to right (progress 0..1). */
export function scramble(target: string, p: number, seed = 0) {
  const n = Math.floor(target.length * clamp(p));
  let out = target.slice(0, n);
  for (let i = n; i < target.length; i++) {
    const c = target[i];
    out += c === ' ' ? ' ' : NOISE[Math.floor(hash(i, seed) * NOISE.length)];
  }
  return out;
}

export function prefersReducedMotion() {
  return (
    typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

/* ------------------------------------------------------------------ */
/* Text primitives: pure string builders for DOM read-outs. Every HUD  */
/* panel renders its meters, sparklines, tables and frames from these, */
/* so one glyph vocabulary covers the whole surface. All are cheap     */
/* (no DOM, no allocation beyond the output string) and unit-tested.   */
/* ------------------------------------------------------------------ */

/** The one monospace face that carries every box/block glyph (Commit
 *  Mono, vendored). Resolves the next/font family for canvas painting. */
export function asciiFontFamily() {
  const fallback = '"Commit Mono", "JetBrains Mono", ui-monospace, monospace';
  if (typeof document === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue('--font-commit-mono');
  return v.trim() ? `${v.trim()}, ${fallback}` : fallback;
}

export const BOX = {
  light: { tl: '┌', tr: '┐', bl: '└', br: '┘', h: '─', v: '│', lt: '├', rt: '┤' },
  heavy: { tl: '┏', tr: '┓', bl: '┗', br: '┛', h: '━', v: '┃', lt: '┣', rt: '┫' },
  double: { tl: '╔', tr: '╗', bl: '╚', br: '╝', h: '═', v: '║', lt: '╠', rt: '╣' },
} as const;
export type BoxStyle = keyof typeof BOX;

/** Eight-level vertical blocks for sparklines. */
export const SPARK = '▁▂▃▄▅▆▇█';
/** Horizontal eighths for sub-cell bar precision. */
export const EIGHTHS = ' ▏▎▍▌▋▊▉█';
/** Braille spinner frames. */
export const SPIN = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏';

const finite = (x: number) => (Number.isFinite(x) ? x : 0);

/** `n` copies of `ch` (n clamped ≥ 0). */
export function rule(n: number, ch = '─') {
  return ch.repeat(Math.max(0, Math.floor(finite(n))));
}

/** Pad or truncate `text` to exactly `n` cells (… on overflow). */
export function fit(text: string, n: number, align: 'l' | 'r' | 'c' = 'l') {
  const w = Math.max(0, Math.floor(finite(n)));
  const t = String(text ?? '');
  if (t.length > w) return w <= 1 ? t.slice(0, w) : t.slice(0, w - 1) + '…';
  const gap = w - t.length;
  if (align === 'r') return ' '.repeat(gap) + t;
  if (align === 'c') return ' '.repeat(gap >> 1) + t + ' '.repeat(gap - (gap >> 1));
  return t + ' '.repeat(gap);
}

/** A glyph meter split into its lit and unlit runs so CSS can colour
 *  them apart: `[lit, rest]`, together exactly `width` cells. `fine`
 *  uses eighth blocks for the boundary cell; otherwise whole cells. */
export function barParts(
  pct: number,
  width = 20,
  { fine = false, on = '█', off = '░' }: { fine?: boolean; on?: string; off?: string } = {}
): [string, string] {
  const w = Math.max(1, Math.floor(finite(width)));
  const f = clamp(finite(pct) / 100) * w;
  const full = Math.floor(f);
  if (!fine || full >= w) {
    const n = Math.round(f);
    return [rule(n, on), rule(w - n, off)];
  }
  const part = EIGHTHS[Math.round((f - full) * 8)];
  const lit = rule(full, on) + (part === ' ' ? '' : part);
  return [lit, rule(w - full - (part === ' ' ? 0 : 1), off)];
}

/** `bar(62, 10)` → `██████░░░░`. */
export function bar(pct: number, width = 20, opts?: Parameters<typeof barParts>[2]) {
  return barParts(pct, width, opts).join('');
}

/** Resample `values` into `width` columns (mean of each bucket; the
 *  newest sample is the last column). Shorter input is right-aligned. */
function resample(values: number[], width: number): (number | null)[] {
  const w = Math.max(1, Math.floor(finite(width)));
  const n = values.length;
  if (n <= w) return [...Array<null>(w - n).fill(null), ...values];
  const out: number[] = [];
  for (let i = 0; i < w; i++) {
    const a = Math.floor((i * n) / w);
    const b = Math.max(a + 1, Math.floor(((i + 1) * n) / w));
    let sum = 0;
    for (let k = a; k < b; k++) sum += values[k];
    out.push(sum / (b - a));
  }
  return out;
}

/** `▁▂▄▇█` sparkline over [lo, hi]; missing history renders as `pad`. */
export function sparkline(values: number[], width = values.length, lo = 0, hi = 100, pad = '·') {
  const span = hi - lo || 1;
  return resample(values.map(finite), width)
    .map((v) => (v === null ? pad : SPARK[Math.round(clamp((v - lo) / span) * (SPARK.length - 1))]))
    .join('');
}

/** Multi-row block chart: `rows` lines (top first) of `width` columns,
 *  each column filled bottom-up in eighths. For equity curves etc. */
export function blockChart(
  values: number[],
  width: number,
  rows: number,
  lo?: number,
  hi?: number
) {
  const cols = resample(values.map(finite), width);
  const real = cols.filter((v): v is number => v !== null);
  const min = lo ?? (real.length ? Math.min(...real) : 0);
  const max = hi ?? (real.length ? Math.max(...real) : 1);
  const span = max - min || 1;
  const h = Math.max(1, Math.floor(rows));
  const lines: string[] = [];
  for (let r = h - 1; r >= 0; r--) {
    let line = '';
    for (const v of cols) {
      if (v === null) {
        line += ' ';
        continue;
      }
      // every real sample shows at least one eighth on the bottom row
      const level = Math.max(1, clamp((v - min) / span) * h * 8);
      const cell = Math.round(clamp(level - r * 8, 0, 8));
      line += cell === 0 ? ' ' : SPARK[cell - 1];
    }
    lines.push(line);
  }
  return lines;
}

export type Column = { width: number; align?: 'l' | 'r' | 'c' };

/** Fixed-width table rows: each cell fitted to its column, joined by
 *  `sep`. Pass a header row first and `ruleAfter: 0` for a `─┼─` rule. */
export function table(
  rows: string[][],
  cols: Column[],
  { sep = ' │ ', ruleAfter }: { sep?: string; ruleAfter?: number } = {}
): string[] {
  const out: string[] = [];
  rows.forEach((row, i) => {
    out.push(cols.map((c, k) => fit(row[k] ?? '', c.width, c.align)).join(sep));
    if (i === ruleAfter) {
      const cross = sep.replace(/│/g, '┼').replace(/ /g, '─');
      out.push(cols.map((c) => rule(c.width)).join(cross));
    }
  });
  return out;
}

/** Box `lines` in a frame `width` cells wide (inner width - 2 padding),
 *  with an optional title set into the top edge: `┌─[ TITLE ]───┐`. */
export function frame(
  lines: string[],
  width: number,
  { title, style = 'light' }: { title?: string; style?: BoxStyle } = {}
): string[] {
  const b = BOX[style];
  const inner = Math.max(2, Math.floor(finite(width)) - 2);
  const head = title ? `${b.h}[ ${fit(title, Math.max(0, inner - 5))} ]` : '';
  const top = b.tl + head + rule(inner - head.length, b.h) + b.tr;
  const body = lines.map((l) => b.v + ' ' + fit(l, inner - 2) + ' ' + b.v);
  return [top, ...body, b.bl + rule(inner, b.h) + b.br];
}

/** Status lamp glyphs: on / warn / off. */
export function lamp(state: 'on' | 'warn' | 'off') {
  return state === 'on' ? '■' : state === 'warn' ? '▲' : '□';
}

/** Spinner frame for tick `i`. */
export function spin(i: number) {
  return SPIN[((Math.floor(finite(i)) % SPIN.length) + SPIN.length) % SPIN.length];
}

/** Box glyph for a ring cell whose radius points along (dx, dy) on screen. */
function tangentGlyph(dx: number, dy: number) {
  const a = Math.atan2(dy, dx);
  const s = Math.abs(Math.sin(a));
  const c = Math.abs(Math.cos(a));
  if (s > 0.92) return '─';
  if (c > 0.92) return '│';
  return dx * dy > 0 ? '╱' : '╲';
}

/** Targeting reticle as text rows: outer and inner solid rings, a dotted
 *  middle ring, crosshair stubs and a centre mark. `aspect` is the cell
 *  height / width ratio so the rings come out round. Static art for
 *  standby screens (rendered once, no per-frame work). */
export function reticleArt(cols: number, rows: number, aspect = 2): string[] {
  const w = Math.max(5, Math.floor(finite(cols)));
  const h = Math.max(3, Math.floor(finite(rows)));
  const cx = (w - 1) / 2;
  const cy = (h - 1) / 2;
  const R = Math.min(cx / 1.2, (cy * aspect) / 1.25);
  const grid = Array.from({ length: h }, () => Array<string>(w).fill(' '));
  const put = (x: number, y: number, g: string) => {
    const xi = Math.round(x);
    const yi = Math.round(y);
    if (xi >= 0 && xi < w && yi >= 0 && yi < h) grid[yi][xi] = g;
  };
  // solid rings: scan columns and rows so the outline never breaks
  for (const k of [1, 0.36]) {
    const r = R * k;
    for (let x = Math.ceil(cx - r); x <= cx + r; x++) {
      const dx = x - cx;
      const yy = Math.sqrt(Math.max(0, r * r - dx * dx));
      put(x, cy - yy / aspect, tangentGlyph(dx, -yy));
      put(x, cy + yy / aspect, tangentGlyph(dx, yy));
    }
    for (let y = Math.ceil(cy - r / aspect); y <= cy + r / aspect; y++) {
      const dy = (y - cy) * aspect;
      const xx = Math.sqrt(Math.max(0, r * r - dy * dy));
      put(cx - xx, y, tangentGlyph(-xx, dy));
      put(cx + xx, y, tangentGlyph(xx, dy));
    }
  }
  // dotted middle ring
  const steps = Math.max(12, Math.round(R * 2.2));
  for (let i = 0; i < steps; i++) {
    const a = (i / steps) * TAU;
    put(cx + Math.cos(a) * R * 0.68, cy + (Math.sin(a) * R * 0.68) / aspect, '·');
  }
  // crosshair stubs outside the rim and inside the core
  for (let x = 0; x < w; x++) {
    const d = Math.abs(x - cx) / R;
    if ((d > 1.05 && d < 1.2) || (d > 0 && d < 0.18)) put(x, cy, '─');
  }
  for (let y = 0; y < h; y++) {
    const d = (Math.abs(y - cy) * aspect) / R;
    if ((d > 1.05 && d < 1.25) || (d > 0 && d < 0.2)) put(cx, y, '│');
  }
  put(cx, cy, '┼');
  return grid.map((r) => r.join(''));
}
