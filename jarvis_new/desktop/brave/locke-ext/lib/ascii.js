/* Locke ASCII engine (plain ES module port of the Jarvis ascii.ts).
 * Glyph atlas, arc-reactor field, scramble decode and text primitives. */

export const RAMP = ' .·:-=+*#%▓█';
export const NOISE = '▓▒░#%&@$01<>/\\=+*';
const CHARS = RAMP + NOISE;
export const TAU = Math.PI * 2;

export const clamp = (x, a = 0, b = 1) => (x < a ? a : x > b ? b : x);
export const frac = (x) => x - Math.floor(x);
export function sstep(a, b, x) {
  const t = clamp((x - a) / (b - a));
  return t * t * (3 - 2 * t);
}
export function hash(x, y) {
  const n = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
  return n - Math.floor(n);
}
function ring(r, c, w) {
  const d = (r - c) / w;
  return d > -3 && d < 3 ? Math.exp(-d * d) : 0;
}

export function hexRgb(hex) {
  const h = hex.replace('#', '');
  const v = h.length === 3 ? h.replace(/./g, (c) => c + c) : h.slice(0, 6);
  const n = parseInt(v, 16);
  return Number.isFinite(n) ? [(n >> 16) & 255, (n >> 8) & 255, n & 255] : [95, 227, 255];
}
function mixRgb(a, b, t) {
  const u = clamp(t);
  return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u];
}

export const LEVELS = 10;

export function asciiFontFamily() {
  return '"Commit Mono", "JetBrains Mono", "DejaVu Sans Mono", ui-monospace, monospace';
}

/** One sprite per (glyph, brightness level), tinted deep -> accent -> white. */
export function glyphAtlas(cw, ch, dpr, accent) {
  const canvas = document.createElement('canvas');
  const sw = Math.ceil(cw * dpr);
  const sh = Math.ceil(ch * dpr);
  canvas.width = sw * CHARS.length;
  canvas.height = sh * LEVELS;
  const ctx = canvas.getContext('2d');
  const base = hexRgb(accent);
  const deep = mixRgb([2, 9, 15], base, 0.45);
  const hot = mixRgb(base, [255, 255, 255], 0.7);
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
    canvas, sw, sh,
    draw(target, index, bright, x, y) {
      const l = Math.min(LEVELS - 1, Math.max(0, Math.round(bright * (LEVELS - 1))));
      target.drawImage(canvas, index * sw, l * sh, sw, sh, x, y, cw, ch);
    },
  };
}
export const noiseIndex = (r) => RAMP.length + (Math.floor(r * NOISE.length) % NOISE.length);

const NORMALS = [0, 1, 2].map((k) => {
  const a = Math.PI / 2 + (k * TAU) / 3;
  return [Math.cos(a), Math.sin(a)];
});

/** Arc-reactor intensity at normalised (nx, ny); returns [intensity, hot]. */
export function reactorField(nx, ny, f, aa) {
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
export function scramble(target, p, seed = 0) {
  const n = Math.floor(target.length * clamp(p));
  let out = target.slice(0, n);
  for (let i = n; i < target.length; i++) {
    const c = target[i];
    out += c === ' ' || c === '\n' ? c : NOISE[Math.floor(hash(i, seed) * NOISE.length)];
  }
  return out;
}

export function prefersReducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export const BOX = {
  light: { tl: '┌', tr: '┐', bl: '└', br: '┘', h: '─', v: '│', lt: '├', rt: '┤' },
  heavy: { tl: '┏', tr: '┓', bl: '┗', br: '┛', h: '━', v: '┃', lt: '┣', rt: '┫' },
  double: { tl: '╔', tr: '╗', bl: '╚', br: '╝', h: '═', v: '║', lt: '╠', rt: '╣' },
};

export const SPARK = '▁▂▃▄▅▆▇█';
export const EIGHTHS = ' ▏▎▍▌▋▊▉█';
export const SPIN = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏';

const finite = (x) => (Number.isFinite(x) ? x : 0);

export function rule(n, ch = '─') {
  return ch.repeat(Math.max(0, Math.floor(finite(n))));
}

export function fit(text, n, align = 'l') {
  const w = Math.max(0, Math.floor(finite(n)));
  const t = String(text ?? '');
  if (t.length > w) return w <= 1 ? t.slice(0, w) : t.slice(0, w - 1) + '…';
  const gap = w - t.length;
  if (align === 'r') return ' '.repeat(gap) + t;
  if (align === 'c') return ' '.repeat(gap >> 1) + t + ' '.repeat(gap - (gap >> 1));
  return t + ' '.repeat(gap);
}

export function barParts(pct, width = 20, { fine = false, on = '█', off = '░' } = {}) {
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
export function bar(pct, width = 20, opts) {
  return barParts(pct, width, opts).join('');
}

function resample(values, width) {
  const w = Math.max(1, Math.floor(finite(width)));
  const n = values.length;
  if (n <= w) return [...Array(w - n).fill(null), ...values];
  const out = [];
  for (let i = 0; i < w; i++) {
    const a = Math.floor((i * n) / w);
    const b = Math.max(a + 1, Math.floor(((i + 1) * n) / w));
    let sum = 0;
    for (let k = a; k < b; k++) sum += values[k];
    out.push(sum / (b - a));
  }
  return out;
}

export function sparkline(values, width = values.length, lo = 0, hi = 100, pad = '·') {
  const span = hi - lo || 1;
  return resample(values.map(finite), width)
    .map((v) => (v === null ? pad : SPARK[Math.round(clamp((v - lo) / span) * (SPARK.length - 1))]))
    .join('');
}

export function blockChart(values, width, rows, lo, hi) {
  const cols = resample(values.map(finite), width);
  const real = cols.filter((v) => v !== null);
  const min = lo ?? (real.length ? Math.min(...real) : 0);
  const max = hi ?? (real.length ? Math.max(...real) : 1);
  const span = max - min || 1;
  const h = Math.max(1, Math.floor(rows));
  const lines = [];
  for (let r = h - 1; r >= 0; r--) {
    let line = '';
    for (const v of cols) {
      if (v === null) { line += ' '; continue; }
      const level = Math.max(1, clamp((v - min) / span) * h * 8);
      const cell = Math.round(clamp(level - r * 8, 0, 8));
      line += cell === 0 ? ' ' : SPARK[cell - 1];
    }
    lines.push(line);
  }
  return lines;
}

export function table(rows, cols, { sep = ' │ ', ruleAfter } = {}) {
  const out = [];
  rows.forEach((row, i) => {
    out.push(cols.map((c, k) => fit(row[k] ?? '', c.width, c.align)).join(sep));
    if (i === ruleAfter) {
      const cross = sep.replace(/│/g, '┼').replace(/ /g, '─');
      out.push(cols.map((c) => rule(c.width)).join(cross));
    }
  });
  return out;
}

export function frame(lines, width, { title, style = 'light' } = {}) {
  const b = BOX[style];
  const inner = Math.max(2, Math.floor(finite(width)) - 2);
  const head = title ? `${b.h}[ ${fit(title, Math.max(0, inner - 5))} ]` : '';
  const top = b.tl + head + rule(inner - head.length, b.h) + b.tr;
  const body = lines.map((l) => b.v + ' ' + fit(l, inner - 2) + ' ' + b.v);
  return [top, ...body, b.bl + rule(inner, b.h) + b.br];
}

export function lamp(state) {
  return state === 'on' ? '■' : state === 'warn' ? '▲' : '□';
}
export function spin(i) {
  return SPIN[((Math.floor(finite(i)) % SPIN.length) + SPIN.length) % SPIN.length];
}
