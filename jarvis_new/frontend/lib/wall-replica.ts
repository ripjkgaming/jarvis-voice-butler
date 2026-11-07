/**
 * Pixel replica of the desktop wallpaper's ASCII emblem (~/deck/assets/
 * locke-wall-ascii.png, rendered by ~/deck/lib/jarvis_wall.py from the glyph
 * engine jarvis_ascii.py). DOM-free so it can be unit-checked in Node.
 *
 * The wallpaper is a cell grid (7 x 15 px cells, JetBrains Mono Nerd Font 12 px)
 * of glyphs over a flat background, plus a bloom. `public/wall-cells.json`
 * (`jarvis_wall.py --dump-cells`) carries the exact grid, the glyph masks as
 * PIL rasterised them and the field parameters. This module
 *  - paints cells with PIL's exact mask blend, so the static frame is
 *    pixel-identical to the PNG apart from the bloom (see below);
 *  - re-evaluates the reactor field (a port of jarvis_ascii.reactor) for the
 *    emblem cells at any time `t`, so the emblem can animate FROM the exact
 *    static pose (t = data.reactor.t) with no jump: static = energy 0;
 *  - applies the bloom (Gaussian blur 9 px -> brightness 1.6 -> screen) at
 *    quarter resolution (a smooth glow, so the loss is far below 1/255 on
 *    average).
 */

export type WallData = {
  version: number;
  src: [number, number];
  cw: number;
  lh: number;
  cols: number;
  rows: number;
  ox: number;
  oy: number;
  bg: [number, number, number];
  palette: Record<string, [number, number, number]>;
  ramp: string;
  reactor: { cx: number; cy: number; R: number; ax: number; t: number; b: number };
  bloom: { blur: number; brightness: number };
  glyphs: string;
  masks: Record<string, string>;
  cells: string;
};

export type Rgb = [number, number, number];

/** Everything the animation can vary. Static pose: t = reactor.t, boost 0,
 *  shock null, reveal 0. */
export type ReplicaFrame = {
  t: number;
  /** Optional per-component phases [rim, dash, coil, beam, triangle, pulse] in
   *  field-time units. Lets the wind-down glide each component onto the next
   *  pose-equivalent time (see PHASE_PERIODS) so it lands exactly on the
   *  static pose. Absent = every component runs on `t`. */
  tt?: readonly number[];
  /** extra core brightness (ignition pulse) */
  boost: number;
  /** expanding shock ring: [radius, strength] */
  shock: [number, number] | null;
  /** 0 = every emblem glyph shown; 1 = all cleared (hand-over to the live orb) */
  reveal: number;
};

const TAU = Math.PI * 2;

/** Field-time period after which each animated component repeats its pose:
 *  rim comb, dash ring, coils, beam, core triangle (3-fold symmetric), pulse. */
export const PHASE_PERIODS = [
  1 / 0.09,
  1 / 0.22,
  1 / 0.55,
  1 / 0.42,
  TAU / 0.6 / 3,
  TAU / 7,
] as const;
/** Cells inside this normalised radius belong to the reactor (animated). */
const REACTOR_R = 1.08;
/** Region half-extent in cells around the emblem centre. */
const HALF_COLS = 68;
const HALF_ROWS = 33;
/** Bloom is computed at 1/BLOOM_DIV resolution. */
const BLOOM_DIV = 4;
/** Opaque plate radius (normalised): the replica hides the PNG inside it. */
const PLATE_R = 1.5;

const clamp = (x: number, a = 0, b = 1): number => (x < a ? a : x > b ? b : x);
const sstep = (a: number, b: number, x: number): number => {
  const t = clamp((x - a) / (b - a));
  return t * t * (3 - 2 * t);
};
const frac = (x: number): number => x - Math.floor(x);
const ring = (r: number, c: number, w: number): number => {
  const d = (r - c) / w;
  return d > -3 && d < 3 ? Math.exp(-d * d) : 0;
};
/** jarvis_ascii.mix with int() truncation. */
const mix = (a: Rgb, b: Rgb, t: number): Rgb => {
  const k = clamp(t);
  return [
    Math.floor(a[0] + (b[0] - a[0]) * k),
    Math.floor(a[1] + (b[1] - a[1]) * k),
    Math.floor(a[2] + (b[2] - a[2]) * k),
  ];
};
/** PIL DIV255 */
const div255 = (a: number): number => {
  const tmp = a + 128;
  return ((tmp >> 8) + tmp) >> 8;
};

function b64(s: string): Uint8Array {
  if (typeof atob === 'function') {
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  return new Uint8Array(Buffer.from(s, 'base64'));
}

/** Pillow's box length for a Gaussian of `radius` made of `passes` boxes. */
function pilBoxRadius(radius: number, passes: number): number {
  const sigma2 = (radius * radius) / passes;
  const L = Math.sqrt(12 * sigma2 + 1);
  const l = Math.floor((L - 1) / 2);
  let a = (2 * l + 1) * (l * (l + 1) - 3 * sigma2);
  a /= 6 * (sigma2 - (l + 1) * (l + 1));
  return l + a;
}

/** One line of Pillow's ImagingLineBoxBlur32 (BoxBlur.c) on one channel. */
function boxLine(
  src: Uint8Array,
  dst: Uint8Array,
  start: number,
  stride: number,
  n: number,
  radius: number,
  ww: number,
  fw: number
): void {
  const last = n - 1;
  const edgeA = Math.min(radius + 1, n);
  const edgeB = Math.max(n - radius - 1, 0);
  const at = (i: number) => src[start + i * stride];
  let acc = at(0) * (radius + 1);
  for (let x = 0; x < edgeA - 1; x++) acc += at(x);
  acc += at(last) * (radius - edgeA + 1);
  const save = (x: number, left: number, right: number) => {
    const bulk = acc * ww + (at(left) + at(right)) * fw;
    dst[start + x * stride] = Math.floor((bulk + 8388608) / 16777216);
  };
  for (let x = 0; x < edgeA; x++) {
    acc += at(x + radius) - at(0);
    save(x, 0, x + radius + 1);
  }
  for (let x = edgeA; x < edgeB; x++) {
    acc += at(x + radius) - at(x - radius - 1);
    save(x, x - radius - 1, x + radius + 1);
  }
  for (let x = edgeB; x <= last; x++) {
    acc += at(last) - at(x - radius - 1);
    save(x, x - radius - 1, last);
  }
}

/** Pillow ImageFilter.GaussianBlur(radius) on an interleaved RGB image
 *  (3 box passes, horizontal then vertical each, 8-bit rounding per pass). */
export function pilGaussianBlur(
  img: Uint8Array,
  w: number,
  h: number,
  radius: number,
  passes = 3
): Uint8Array {
  const fr = pilBoxRadius(radius, passes);
  const r = Math.floor(fr);
  const ww = Math.floor(16777216 / (fr * 2 + 1));
  const fw = Math.floor((16777216 - (r * 2 + 1) * ww) / 2);
  let a: Uint8Array = img.slice();
  let b: Uint8Array = new Uint8Array(img.length);
  for (let p = 0; p < passes; p++) {
    for (let y = 0; y < h; y++)
      for (let c = 0; c < 3; c++) boxLine(a, b, y * w * 3 + c, 3, w, r, ww, fw);
    const t = a;
    a = b;
    b = t;
  }
  for (let p = 0; p < passes; p++) {
    for (let x = 0; x < w; x++)
      for (let c = 0; c < 3; c++) boxLine(a, b, x * 3 + c, w * 3, h, r, ww, fw);
    const t = a;
    a = b;
    b = t;
  }
  return a;
}

type DynCell = {
  /** cell position (grid) */
  x: number;
  y: number;
  /** normalised polar coords in the engine's metric */
  nx: number;
  ny: number;
  r: number;
  u: number;
};

export class WallReplica {
  readonly data: WallData;
  /** region origin and size in source pixels */
  readonly rx0: number;
  readonly ry0: number;
  readonly rw: number;
  readonly rh: number;
  /** emblem centre / radius in source pixels (cell centre, ascii R in px) */
  readonly cxPx: number;
  readonly cyPx: number;
  readonly rPx: number;

  private masks: Uint8Array[] = [];
  private glyphIndex = new Map<string, number>();
  private rampGlyph: number[] = [];
  private base: Uint8Array; // RGB, static cells painted
  private img: Uint8Array; // RGB scratch
  private dyn: DynCell[] = [];
  private rnorm: Float32Array; // normalised radius per pixel (plate feather)
  private alpha: Uint8Array;
  private dsW: number;
  private dsH: number;
  private ds: Float32Array;
  private tmp: Float32Array;
  /** animated bounding box (region px, aligned to the bloom grid) */
  readonly bx: number;
  readonly by: number;
  readonly bw: number;
  readonly bh: number;
  readonly dsBW: number;
  readonly dsBH: number;
  /** pre-bloom pixels of the animated box (RGB) */
  readonly boxRgb: Uint8Array;
  /** glow of the animated box at 1/BLOOM_DIV resolution (RGBA, a = 255) */
  readonly glowRgba: Uint8ClampedArray;
  private dsB: Float32Array;
  private tmpB: Float32Array;
  private kern: Float32Array;
  private lo: Rgb;
  private mid: Rgb;
  private hi: Rgb;
  private white: Rgb;
  private bg: Rgb;

  constructor(data: WallData) {
    this.data = data;
    const { cw, lh, cols, rows, reactor } = data;
    this.bg = data.bg;
    this.lo = data.palette.DIM;
    this.mid = data.palette.CY;
    this.hi = data.palette.ICE;
    this.white = data.palette.WHITE;
    for (let i = 0; i < data.glyphs.length; i++) {
      const c = data.glyphs[i];
      this.glyphIndex.set(c, i);
      this.masks.push(b64(data.masks[c]));
    }
    this.rampGlyph = Array.from(data.ramp).map((c) => (c === ' ' ? -1 : this.glyphIndex.get(c)!));

    const c0 = Math.max(0, reactor.cx - HALF_COLS);
    const c1 = Math.min(cols - 1, reactor.cx + HALF_COLS);
    const r0 = Math.max(0, reactor.cy - HALF_ROWS);
    const r1 = Math.min(rows - 1, reactor.cy + HALF_ROWS);
    this.rx0 = data.ox + c0 * cw;
    this.ry0 = data.oy + r0 * lh;
    this.rw = (c1 - c0 + 1) * cw;
    this.rh = (r1 - r0 + 1) * lh;
    this.cxPx = data.ox + reactor.cx * cw + cw / 2;
    this.cyPx = data.oy + reactor.cy * lh + lh / 2;
    this.rPx = reactor.R * lh;

    // static cells (everything outside the animated reactor disc)
    this.base = new Uint8Array(this.rw * this.rh * 3);
    for (let i = 0; i < this.base.length; i += 3) {
      this.base[i] = this.bg[0];
      this.base[i + 1] = this.bg[1];
      this.base[i + 2] = this.bg[2];
    }
    this.img = new Uint8Array(this.base.length);
    const cells = b64(data.cells);
    const R = reactor.R;
    for (let i = 0; i + 6 < cells.length; i += 7) {
      const x = cells[i] | (cells[i + 1] << 8);
      const y = cells[i + 2];
      if (x < c0 || x > c1 || y < r0 || y > r1) continue;
      const nx = (x - reactor.cx) / (R * reactor.ax);
      const ny = (y - reactor.cy) / R;
      if (Math.hypot(nx, ny) <= REACTOR_R) continue; // animated, see paintDynamic
      this.paintCell(this.base, x, y, cells[i + 3], [cells[i + 4], cells[i + 5], cells[i + 6]], 1);
    }
    // animated cells: every grid cell inside the reactor disc
    for (let y = r0; y <= r1; y++) {
      for (let x = c0; x <= c1; x++) {
        const nx = (x - reactor.cx) / (R * reactor.ax);
        const ny = (y - reactor.cy) / R;
        const r = Math.hypot(nx, ny);
        if (r > REACTOR_R) continue;
        this.dyn.push({ x, y, nx, ny, r, u: frac(Math.atan2(ny, nx) / TAU) });
      }
    }
    // animated bounding box: the reactor disc plus a glow margin, on the bloom grid
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const c of this.dyn) {
      minX = Math.min(minX, data.ox + c.x * cw - this.rx0);
      minY = Math.min(minY, data.oy + c.y * lh - this.ry0);
      maxX = Math.max(maxX, data.ox + (c.x + 1) * cw - this.rx0);
      maxY = Math.max(maxY, data.oy + (c.y + 1) * lh - this.ry0);
    }
    const margin = Math.ceil(data.bloom.blur * 3);
    const bx0 = Math.max(0, Math.floor((minX - margin) / BLOOM_DIV) * BLOOM_DIV);
    const by0 = Math.max(0, Math.floor((minY - margin) / BLOOM_DIV) * BLOOM_DIV);
    const bx1 = Math.min(this.rw, Math.ceil((maxX + margin) / BLOOM_DIV) * BLOOM_DIV);
    const by1 = Math.min(this.rh, Math.ceil((maxY + margin) / BLOOM_DIV) * BLOOM_DIV);
    this.bx = bx0;
    this.by = by0;
    this.bw = bx1 - bx0;
    this.bh = by1 - by0;
    this.dsBW = Math.ceil(this.bw / BLOOM_DIV);
    this.dsBH = Math.ceil(this.bh / BLOOM_DIV);
    this.boxRgb = new Uint8Array(this.bw * this.bh * 3);
    this.glowRgba = new Uint8ClampedArray(this.dsBW * this.dsBH * 4);
    this.dsB = new Float32Array(this.dsBW * this.dsBH * 3);
    this.tmpB = new Float32Array(this.dsBW * this.dsBH * 3);
    {
      const sigma = data.bloom.blur / BLOOM_DIV;
      const rad = Math.ceil(sigma * 3);
      this.kern = new Float32Array(rad * 2 + 1);
      let sum = 0;
      for (let i = -rad; i <= rad; i++) {
        this.kern[i + rad] = Math.exp(-(i * i) / (2 * sigma * sigma));
        sum += this.kern[i + rad];
      }
      for (let i = 0; i < this.kern.length; i++) this.kern[i] /= sum;
    }
    // plate feather
    this.rnorm = new Float32Array(this.rw * this.rh);
    for (let py = 0; py < this.rh; py++) {
      for (let px = 0; px < this.rw; px++) {
        const nx = (this.rx0 + px + 0.5 - this.cxPx) / (R * reactor.ax * cw);
        const ny = (this.ry0 + py + 0.5 - this.cyPx) / (R * lh);
        this.rnorm[py * this.rw + px] = Math.hypot(nx, ny);
      }
    }
    this.alpha = new Uint8Array(this.rw * this.rh);
    for (let i = 0; i < this.alpha.length; i++) {
      const r = this.rnorm[i];
      this.alpha[i] =
        r <= PLATE_R - 0.07
          ? 255
          : r >= PLATE_R
            ? 0
            : Math.round(255 * (1 - sstep(PLATE_R - 0.07, PLATE_R, r)));
    }
    this.dsW = Math.ceil(this.rw / BLOOM_DIV);
    this.dsH = Math.ceil(this.rh / BLOOM_DIV);
    this.ds = new Float32Array(this.dsW * this.dsH * 3);
    this.tmp = new Float32Array(this.dsW * this.dsH * 3);
  }

  /** The static pose (what the wallpaper shows). */
  staticFrame(): ReplicaFrame {
    return { t: this.data.reactor.t, boost: 0, shock: null, reveal: 0 };
  }

  private paintCell(buf: Uint8Array, x: number, y: number, g: number, col: Rgb, a: number): void {
    const { cw, lh, ox, oy } = this.data;
    const mask = this.masks[g];
    if (!mask) return;
    const px0 = ox + x * cw - this.rx0;
    const py0 = oy + y * lh - this.ry0;
    for (let j = 0; j < lh; j++) {
      const py = py0 + j;
      if (py < 0 || py >= this.rh) continue;
      for (let i = 0; i < cw; i++) {
        const px = px0 + i;
        if (px < 0 || px >= this.rw) continue;
        let m = mask[j * cw + i];
        if (a < 1) m = Math.round(m * a);
        if (m === 0) continue;
        const o = (py * this.rw + px) * 3;
        if (m === 255) {
          buf[o] = col[0];
          buf[o + 1] = col[1];
          buf[o + 2] = col[2];
        } else {
          buf[o] = div255(buf[o] * (255 - m) + col[0] * m);
          buf[o + 1] = div255(buf[o + 1] * (255 - m) + col[1] * m);
          buf[o + 2] = div255(buf[o + 2] * (255 - m) + col[2] * m);
        }
      }
    }
  }

  /** jarvis_ascii.reactor for one cell; returns ramp glyph index or -1 plus colour. */
  private field(
    c: DynCell,
    f: ReplicaFrame,
    k: ReturnType<WallReplica['consts']>,
    out: { col: Rgb }
  ): number {
    const { r, u, nx, ny } = c;
    let I = 0;
    let hot = 0;
    if (r < 1.08) {
      if (u < k.aRim) {
        I += ring(r, 0.94, k.wRim) * 0.85;
        if (r > 0.995 && r < 1.07 && frac(u * 48 + k.spin) < 0.3) I += 0.42;
      }
      if (k.aDash && frac(u * 36 - k.tDash * 0.22) < 0.55)
        I += ring(r, 0.81, k.wDash) * 0.7 * k.aDash;
      if (r > 0.45 && r < 0.73 && k.aCoil > 0) {
        const sp = u * 10 + k.tCoil * 0.55;
        const fp = frac(sp);
        if (fp > 0.1 && fp < 0.9 && Math.trunc(sp) % 10 < k.aCoil) {
          const band = sstep(0.45, 0.5, r) * (1 - sstep(0.68, 0.73, r));
          I += band * (fp < 0.2 || fp > 0.8 ? 0.78 : 0.5);
        }
      }
      if (r > 0.4 && r < 0.92) I += Math.exp(-frac(k.beam - u) * 7) * 0.32 * Math.min(k.aCoil, 1);
      if (k.aInner) I += ring(r, 0.38, k.wIn) * 0.95 * k.aInner;
      if (k.aTri && r < 0.4) {
        const px = nx * k.cr - ny * k.sr;
        const py = nx * k.sr + ny * k.cr;
        let m = -Infinity;
        for (const [a, cc] of k.normals) m = Math.max(m, px * a + py * cc);
        const e = Math.exp(-(((m - 0.15) / k.wTri) ** 2)) * k.aTri;
        I += e + (m < 0.15 ? 0.14 * k.aTri : 0);
        hot += e;
      }
      if (k.aCore && r < 0.6) {
        const cv = Math.exp(-((r / 0.13) ** 2)) * k.aCore * k.pulse * (1 + f.boost);
        I += cv + Math.exp(-((r / 0.55) ** 2)) * 0.13 * k.aCore * (1 + f.boost * 2);
        hot += cv;
      }
    }
    if (f.shock) {
      const s = ring(r, f.shock[0], 0.06) * f.shock[1];
      I += s;
      hot += s * 0.6;
    }
    if (I < 0.08) return -1;
    const Ic = I < 1 ? I : 1;
    const g = this.rampGlyph[1 + Math.floor(Ic * (this.data.ramp.length - 2))];
    let col =
      Ic < 0.55 ? mix(this.lo, this.mid, Ic / 0.55) : mix(this.mid, this.hi, (Ic - 0.55) / 0.45);
    if (hot > 0.45) col = mix(col, this.white, hot - 0.45);
    out.col = col;
    return g;
  }

  consts(f: ReplicaFrame) {
    const { reactor } = this.data;
    const [tRim, tDash, tCoil, tBeam, tTri, tPulse] = f.tt ?? [f.t, f.t, f.t, f.t, f.t, f.t];
    const b = reactor.b;
    const aa = 0.55 / Math.max(reactor.R, 1);
    return {
      tDash,
      tCoil,
      aRim: sstep(0, 0.8, b),
      aDash: sstep(0.4, 1, b),
      aCoil: clamp((b - 0.7) / 0.7) * 10,
      aInner: sstep(1.2, 1.5, b),
      aTri: sstep(1.4, 1.9, b),
      aCore: sstep(1.7, 2.2, b),
      spin: tRim * 0.09,
      beam: frac(tBeam * 0.42),
      pulse: 0.88 + 0.12 * Math.sin(tPulse * 7),
      cr: Math.cos(-tTri * 0.6),
      sr: Math.sin(-tTri * 0.6),
      wRim: Math.max(0.03, aa),
      wDash: Math.max(0.02, aa * 0.8),
      wIn: Math.max(0.025, aa * 0.8),
      wTri: Math.max(0.022, aa * 0.7),
      normals: [0, 1, 2].map((i) => {
        const a = Math.PI / 2 + (i * TAU) / 3;
        return [Math.cos(a), Math.sin(a)] as [number, number];
      }),
    };
  }

  /** Per-cell visibility during the hand-over: core first, rim last. Lags the
   *  live layers' own fade-in (orb-morph.ts layerWindow) so the emblem glyphs
   *  keep texture under the translucent live orb until it is established
   *  (no dark valley mid cross-fade). */
  private cellAlpha(r: number, reveal: number): number {
    if (reveal <= 0) return 1;
    const s = 0.22 + 0.45 * Math.min(r, 1.08);
    return 1 - sstep(s, s + 0.3, reveal);
  }

  /** Paint the emblem (RGB, before bloom) for a frame into this.img. */
  paintRgb(f: ReplicaFrame): Uint8Array {
    const img = this.img;
    img.set(this.base);
    const k = this.consts(f);
    const out = { col: this.bg };
    for (const c of this.dyn) {
      const a = this.cellAlpha(c.r, f.reveal);
      if (a <= 0) continue;
      const g = this.field(c, f, k, out);
      if (g < 0) continue;
      this.paintCell(img, c.x, c.y, g, out.col, a);
    }
    return img;
  }

  /** Bloom of `img` (jarvis_wall.render: blur 9 -> brightness 1.6 -> screen)
   *  composited in place. */
  bloomInPlace(img: Uint8Array, exact = false): void {
    if (exact) {
      // Pillow's own box-blur algorithm at full resolution: bit-exact glow.
      const blurred = pilGaussianBlur(img, this.rw, this.rh, this.data.bloom.blur, 3);
      const gain = this.data.bloom.brightness;
      for (let i = 0; i < img.length; i++) {
        let glow = Math.floor(blurred[i] * gain);
        if (glow > 255) glow = 255;
        img[i] = 255 - Math.floor(((255 - img[i]) * (255 - glow)) / 255);
      }
      return;
    }
    const { rw, rh, dsW, dsH, ds, tmp } = this;
    const D = BLOOM_DIV;
    // box downsample
    for (let y = 0; y < dsH; y++) {
      for (let x = 0; x < dsW; x++) {
        let r = 0;
        let g = 0;
        let b = 0;
        let n = 0;
        for (let j = 0; j < D; j++) {
          const py = y * D + j;
          if (py >= rh) break;
          for (let i = 0; i < D; i++) {
            const px = x * D + i;
            if (px >= rw) break;
            const o = (py * rw + px) * 3;
            r += img[o];
            g += img[o + 1];
            b += img[o + 2];
            n++;
          }
        }
        const o = (y * dsW + x) * 3;
        ds[o] = r / n;
        ds[o + 1] = g / n;
        ds[o + 2] = b / n;
      }
    }
    // gaussian blur, sigma = blur / D, edge clamped
    const sigma = this.data.bloom.blur / D;
    const rad = Math.ceil(sigma * 3);
    const kern = new Float32Array(rad * 2 + 1);
    let sum = 0;
    for (let i = -rad; i <= rad; i++) {
      kern[i + rad] = Math.exp(-(i * i) / (2 * sigma * sigma));
      sum += kern[i + rad];
    }
    for (let i = 0; i < kern.length; i++) kern[i] /= sum;
    for (let y = 0; y < dsH; y++) {
      for (let x = 0; x < dsW; x++) {
        let r = 0;
        let g = 0;
        let b = 0;
        for (let i = -rad; i <= rad; i++) {
          const xx = x + i < 0 ? 0 : x + i >= dsW ? dsW - 1 : x + i;
          const o = (y * dsW + xx) * 3;
          const w = kern[i + rad];
          r += ds[o] * w;
          g += ds[o + 1] * w;
          b += ds[o + 2] * w;
        }
        const o = (y * dsW + x) * 3;
        tmp[o] = r;
        tmp[o + 1] = g;
        tmp[o + 2] = b;
      }
    }
    for (let y = 0; y < dsH; y++) {
      for (let x = 0; x < dsW; x++) {
        let r = 0;
        let g = 0;
        let b = 0;
        for (let i = -rad; i <= rad; i++) {
          const yy = y + i < 0 ? 0 : y + i >= dsH ? dsH - 1 : y + i;
          const o = (yy * dsW + x) * 3;
          const w = kern[i + rad];
          r += tmp[o] * w;
          g += tmp[o + 1] * w;
          b += tmp[o + 2] * w;
        }
        const o = (y * dsW + x) * 3;
        ds[o] = Math.round(r);
        ds[o + 1] = Math.round(g);
        ds[o + 2] = Math.round(b);
      }
    }
    // upsample (bilinear), brightness, screen
    const gain = this.data.bloom.brightness;
    for (let py = 0; py < rh; py++) {
      const fy = (py + 0.5) / D - 0.5;
      const y0 = Math.max(0, Math.min(dsH - 1, Math.floor(fy)));
      const y1 = Math.min(dsH - 1, y0 + 1);
      const wy = Math.max(0, Math.min(1, fy - y0));
      for (let px = 0; px < rw; px++) {
        const fx = (px + 0.5) / D - 0.5;
        const x0 = Math.max(0, Math.min(dsW - 1, Math.floor(fx)));
        const x1 = Math.min(dsW - 1, x0 + 1);
        const wx = Math.max(0, Math.min(1, fx - x0));
        const o = (py * rw + px) * 3;
        for (let ch = 0; ch < 3; ch++) {
          const a = ds[(y0 * dsW + x0) * 3 + ch] * (1 - wx) + ds[(y0 * dsW + x1) * 3 + ch] * wx;
          const b = ds[(y1 * dsW + x0) * 3 + ch] * (1 - wx) + ds[(y1 * dsW + x1) * 3 + ch] * wx;
          let glow = Math.floor(Math.round(a * (1 - wy) + b * wy) * gain);
          if (glow > 255) glow = 255;
          const v = img[o + ch];
          img[o + ch] = 255 - Math.floor(((255 - v) * (255 - glow)) / 255);
        }
      }
    }
  }

  /** Animated box only: pre-bloom pixels into `boxRgb` and its glow (blur 9,
   *  brightness 1.6) at 1/BLOOM_DIV resolution into `glowRgba`. The caller
   *  puts boxRgb on the plate and screen-blends the upscaled glow over it
   *  (canvas drawImage does the bilinear upsample on the GPU). */
  paintBox(f: ReplicaFrame): void {
    const { rw, bx, by, bw, bh, base, img, boxRgb } = this;
    for (let y = 0; y < bh; y++) {
      const o = ((by + y) * rw + bx) * 3;
      img.set(base.subarray(o, o + bw * 3), o);
    }
    const k = this.consts(f);
    const out = { col: this.bg };
    for (const c of this.dyn) {
      const a = this.cellAlpha(c.r, f.reveal);
      if (a <= 0) continue;
      const g = this.field(c, f, k, out);
      if (g < 0) continue;
      this.paintCell(img, c.x, c.y, g, out.col, a);
    }
    for (let y = 0; y < bh; y++) {
      const o = ((by + y) * rw + bx) * 3;
      boxRgb.set(img.subarray(o, o + bw * 3), y * bw * 3);
    }
    // downsample
    const D = BLOOM_DIV;
    const { dsBW, dsBH, dsB, tmpB, kern } = this;
    for (let y = 0; y < dsBH; y++) {
      for (let x = 0; x < dsBW; x++) {
        let r = 0;
        let g = 0;
        let b = 0;
        let n = 0;
        for (let j = 0; j < D; j++) {
          const py = y * D + j;
          if (py >= bh) break;
          let o = (py * bw + x * D) * 3;
          for (let i = 0; i < D; i++) {
            if (x * D + i >= bw) break;
            r += boxRgb[o];
            g += boxRgb[o + 1];
            b += boxRgb[o + 2];
            o += 3;
            n++;
          }
        }
        const o = (y * dsBW + x) * 3;
        dsB[o] = r / n;
        dsB[o + 1] = g / n;
        dsB[o + 2] = b / n;
      }
    }
    const rad = (kern.length - 1) / 2;
    for (let y = 0; y < dsBH; y++) {
      for (let x = 0; x < dsBW; x++) {
        let r = 0;
        let g = 0;
        let b = 0;
        for (let i = -rad; i <= rad; i++) {
          const xx = x + i < 0 ? 0 : x + i >= dsBW ? dsBW - 1 : x + i;
          const o = (y * dsBW + xx) * 3;
          const w = kern[i + rad];
          r += dsB[o] * w;
          g += dsB[o + 1] * w;
          b += dsB[o + 2] * w;
        }
        const o = (y * dsBW + x) * 3;
        tmpB[o] = r;
        tmpB[o + 1] = g;
        tmpB[o + 2] = b;
      }
    }
    const gain = this.data.bloom.brightness;
    for (let y = 0; y < dsBH; y++) {
      for (let x = 0; x < dsBW; x++) {
        let r = 0;
        let g = 0;
        let b = 0;
        for (let i = -rad; i <= rad; i++) {
          const yy = y + i < 0 ? 0 : y + i >= dsBH ? dsBH - 1 : y + i;
          const o = (yy * dsBW + x) * 3;
          const w = kern[i + rad];
          r += tmpB[o] * w;
          g += tmpB[o + 1] * w;
          b += tmpB[o + 2] * w;
        }
        const o = (y * dsBW + x) * 4;
        this.glowRgba[o] = Math.floor(Math.round(r) * gain);
        this.glowRgba[o + 1] = Math.floor(Math.round(g) * gain);
        this.glowRgba[o + 2] = Math.floor(Math.round(b) * gain);
        this.glowRgba[o + 3] = 255;
      }
    }
  }

  /** Full frame as RGBA (plate feather in alpha) into `out` (rw * rh * 4). */
  render(f: ReplicaFrame, out: Uint8ClampedArray | Uint8Array, exact = false): void {
    const img = this.paintRgb(f);
    this.bloomInPlace(img, exact);
    for (let i = 0, j = 0, n = this.rw * this.rh; i < n; i++, j += 3) {
      const o = i * 4;
      out[o] = img[j];
      out[o + 1] = img[j + 1];
      out[o + 2] = img[j + 2];
      out[o + 3] = this.alpha[i];
    }
  }
}
