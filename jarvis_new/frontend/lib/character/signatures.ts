import { type RGBA, css } from './color';
import { clamp01, smooth } from './ease';
import type { ColorSet } from './skin';

export type Rect = { x: number; y: number; w: number; h: number };
export type Anchor = { x: number; y: number; r: number };

export type FxCtx = {
  w: number;
  h: number;
  anchor: Anchor;
  panels: Rect[];
  colors: ColorSet;
  intensity: number;
  maxShake: number;
  maxGlitch: number;
  rand: () => number;
  /** Rolling flash budget (<= max_flash_hz per second). Returns allowed amplitude. */
  flash: (amp: number) => number;
  /** Seconds since the beat started. */
  sec: number;
  scratch: Record<string, unknown>;
};

export type Signature = {
  draw(g: CanvasRenderingContext2D, u: number, c: FxCtx): void;
  /** Spatial reveal for an element rect; null = use the staggered timeline. */
  reveal?(r: Rect, u: number, c: FxCtx): number | null;
  /** Camera shake offset in px. */
  shake?(u: number, c: FxCtx): [number, number];
};

const TAU = Math.PI * 2;
const bell = (u: number, c: number, w: number) => Math.exp(-(((u - c) / w) ** 2));
const easeOut = (t: number) => 1 - (1 - clamp01(t)) ** 3;
const snap = (t: number) => {
  const x = clamp01(t);
  return x * x * x * (x * (6 * x - 15) + 10);
};

export function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function glow(g: CanvasRenderingContext2D, x: number, y: number, r: number, c: RGBA, a: number) {
  if (a <= 0.003 || r <= 1) return;
  const gr = g.createRadialGradient(x, y, 0, x, y, r);
  gr.addColorStop(0, css(c, a));
  gr.addColorStop(0.45, css(c, a * 0.35));
  gr.addColorStop(1, css(c, 0));
  g.fillStyle = gr;
  g.fillRect(x - r, y - r, r * 2, r * 2);
}

function perimeter(r: Rect) {
  return 2 * (r.w + r.h);
}

/* ------------------------------------------------------------ JARVIS */

type Particle = { sx: number; sy: number; tx: number; ty: number; d: number; z: number };

function holoParticles(c: FxCtx): Particle[] {
  if (c.scratch.p) return c.scratch.p as Particle[];
  const n = Math.min(220, 60 + c.panels.length * 22);
  const list: Particle[] = [];
  for (let i = 0; i < n; i++) {
    const p = c.panels[i % Math.max(1, c.panels.length)] ?? { x: 0, y: 0, w: c.w, h: c.h };
    const tx = p.x + c.rand() * p.w;
    const ty = p.y + c.rand() * p.h;
    const a = c.rand() * TAU;
    const d = 70 + c.rand() * 230;
    list.push({
      sx: tx + Math.cos(a) * d,
      sy: ty + Math.sin(a) * d,
      tx,
      ty,
      d: c.rand() * 0.25,
      z: 1 + c.rand(),
    });
  }
  c.scratch.p = list;
  return list;
}

export const holoReassembly: Signature = {
  draw(g, u, c) {
    const { anchor: a, colors } = c;
    const acc = colors.accent;
    const hot = colors.accent_hot;
    g.save();
    // arc-reactor rings spin up
    for (let i = 0; i < 3; i++) {
      const spin = easeOut(u / 0.6);
      const r = a.r * (0.5 + 0.3 * i) * (0.55 + 0.45 * easeOut(u / 0.45));
      const al = smooth(0, 0.12, u) * (1 - 0.65 * smooth(0.78, 1, u));
      g.strokeStyle = css(acc, al * (0.9 - i * 0.18));
      g.lineWidth = 2.2 - i * 0.5;
      const rot = spin * TAU * (1.3 + i * 0.45) * (i % 2 ? -1 : 1);
      const segs = 10 + i * 4;
      for (let k = 0; k < segs; k++) {
        const s0 = rot + (k / segs) * TAU;
        g.beginPath();
        g.arc(a.x, a.y, r, s0, s0 + (TAU / segs) * 0.62);
        g.stroke();
      }
    }
    // tick ring builds around the core
    const ticks = 72;
    const lit = Math.floor(clamp01(u * 1.6) * ticks);
    g.strokeStyle = css(hot, 0.7 * (1 - smooth(0.8, 1, u)));
    g.lineWidth = 1.4;
    g.beginPath();
    for (let k = 0; k < lit; k++) {
      const th = (k / ticks) * TAU;
      const r0 = a.r * 1.12;
      const r1 = r0 + (k % 6 === 0 ? 12 : 6);
      g.moveTo(a.x + Math.cos(th) * r0, a.y + Math.sin(th) * r0);
      g.lineTo(a.x + Math.cos(th) * r1, a.y + Math.sin(th) * r1);
    }
    g.stroke();
    // HUD lines draw in around each panel
    g.lineWidth = 1.2;
    c.panels.forEach((p, i) => {
      const per = perimeter(p);
      const prog = smooth(0.1 + i * 0.03, 0.55 + i * 0.03, u);
      if (prog <= 0) return;
      g.setLineDash([per, per]);
      g.lineDashOffset = per * (1 - prog);
      g.strokeStyle = css(acc, 0.8 * (1 - smooth(0.85, 1, u)));
      g.strokeRect(p.x + 0.5, p.y + 0.5, p.w - 1, p.h - 1);
    });
    g.setLineDash([]);
    // panels resolve out of light particles
    g.fillStyle = css(hot, 1);
    for (const p of holoParticles(c)) {
      const t = easeOut(smooth(0.22 + p.d, 0.82, u));
      const x = p.sx + (p.tx - p.sx) * t;
      const y = p.sy + (p.ty - p.sy) * t;
      const al = Math.sin(clamp01(t) * Math.PI) * 0.85 + 0.1 * (1 - smooth(0.9, 1, u)) * t;
      if (al < 0.02) continue;
      g.globalAlpha = Math.min(1, al);
      g.fillRect(x, y, p.z, p.z);
    }
    g.globalAlpha = 1;
    // soft blue-cyan bloom
    g.globalCompositeOperation = 'lighter';
    glow(g, a.x, a.y, a.r * 2.6, acc, 0.34 * bell(u, 0.86, 0.22));
    g.restore();
  },
};

/* ------------------------------------------------------------ FRIDAY */

type Mote = { x0: number; y0: number; vx: number; vy: number; tx: number; ty: number; z: number };

function sweepMotes(c: FxCtx): Mote[] {
  if (c.scratch.m) return c.scratch.m as Mote[];
  const out: Mote[] = [];
  for (let i = 0; i < 130; i++) {
    const p = c.panels[i % Math.max(1, c.panels.length)] ?? { x: 0, y: 0, w: c.w, h: c.h };
    const edge = c.rand();
    const tx = p.x + c.rand() * p.w;
    const ty = edge < 0.5 ? p.y + (c.rand() < 0.5 ? 0 : p.h) : p.y + c.rand() * p.h;
    out.push({
      x0: tx - 60 - c.rand() * 160,
      y0: ty + (c.rand() - 0.5) * 220,
      vx: 30 + c.rand() * 90,
      vy: -20 - c.rand() * 60,
      tx,
      ty,
      z: 1.2 + c.rand() * 1.6,
    });
  }
  c.scratch.m = out;
  return out;
}

const waveX = (u: number, c: FxCtx) =>
  -c.w * 0.18 + (c.w * 1.36 + 0) * easeGlide(smooth(0, 0.82, u));
const easeGlide = (t: number) => 1 - (1 - clamp01(t)) ** 2.4;

export const luminousSweep: Signature = {
  reveal(r, u, c) {
    const band = c.w * 0.18;
    return clamp01((waveX(u, c) - r.x - r.w * 0.1) / band);
  },
  draw(g, u, c) {
    const { w, h, colors } = c;
    const band = w * 0.18;
    const x = waveX(u, c);
    const warm = colors.accent_hot;
    const cool = colors.accent;
    g.save();
    // warm-to-cool gradient bloom across the screen
    const bloom = 0.17 * bell(u, 0.62, 0.34);
    const gr = g.createLinearGradient(0, 0, w, h * 0.4);
    gr.addColorStop(0, css(warm, bloom));
    gr.addColorStop(1, css(cool, bloom));
    g.fillStyle = gr;
    g.fillRect(0, 0, w, h);
    // the wavefront
    const wv = g.createLinearGradient(x - band, 0, x + band * 0.12, 0);
    wv.addColorStop(0, css(cool, 0));
    wv.addColorStop(0.55, css(cool, 0.2));
    wv.addColorStop(0.9, css([255, 255, 255, 1], 0.75));
    wv.addColorStop(1, css(warm, 0));
    g.fillStyle = wv;
    g.fillRect(x - band, 0, band * 1.12, h);
    // refractive leading edge: split warm/cool hairlines
    g.lineWidth = 1.5;
    g.strokeStyle = css(warm, 0.8);
    g.beginPath();
    g.moveTo(x + 3, 0);
    g.lineTo(x + 3, h);
    g.stroke();
    g.strokeStyle = css(cool, 0.8);
    g.beginPath();
    g.moveTo(x - 3, 0);
    g.lineTo(x - 3, h);
    g.stroke();
    // particles drift off the wave and settle on panel edges
    for (const m of sweepMotes(c)) {
      const born = clamp01((x - m.x0) / band);
      if (born <= 0) continue;
      const t = easeOut(smooth(0.2, 0.95, u) * born);
      const px = m.x0 + m.vx * c.sec * (1 - t) + (m.tx - m.x0) * t;
      const py = m.y0 + m.vy * c.sec * (1 - t) + (m.ty - m.y0) * t;
      g.fillStyle = css(
        t > 0.8 ? cool : warm,
        Math.sin(Math.min(1, born) * Math.PI * 0.5) * (1 - 0.7 * t)
      );
      g.fillRect(px, py, m.z, m.z);
    }
    // glass-like bevel highlight on panel edges once revealed
    g.lineWidth = 1;
    const al = smooth(0.5, 0.85, u) * (1 - smooth(0.9, 1, u));
    for (const p of c.panels) {
      const rev = clamp01((x - p.x) / band);
      if (rev <= 0.3) continue;
      g.strokeStyle = css([255, 255, 255, 1], 0.8 * al);
      g.beginPath();
      g.moveTo(p.x + 6, p.y + 0.5);
      g.lineTo(p.x + p.w - 6, p.y + 0.5);
      g.moveTo(p.x + 0.5, p.y + 6);
      g.lineTo(p.x + 0.5, p.y + p.h - 6);
      g.stroke();
    }
    g.restore();
  },
};

/* ------------------------------------------------------------ ULTRON */

type Shard = {
  x: number;
  y: number;
  len: number;
  th: number;
  horiz: boolean;
  ox: number;
  oy: number;
  d: number;
};

function shards(c: FxCtx): Shard[] {
  if (c.scratch.s) return c.scratch.s as Shard[];
  const list: Shard[] = [];
  const far = Math.max(c.w, c.h);
  for (const p of c.panels) {
    const n = 5 + Math.floor(c.rand() * 3);
    for (let i = 0; i < n; i++) {
      const horiz = c.rand() < 0.55;
      const len = 30 + c.rand() * 90;
      const side = c.rand() < 0.5;
      const x = horiz ? p.x + c.rand() * Math.max(1, p.w - len) : side ? p.x : p.x + p.w - 4;
      const y = horiz ? (side ? p.y : p.y + p.h - 4) : p.y + c.rand() * Math.max(1, p.h - len);
      const a = c.rand() * TAU;
      const d = far * (0.35 + c.rand() * 0.5);
      list.push({
        x,
        y,
        len,
        th: 3 + c.rand() * 3,
        horiz,
        ox: Math.cos(a) * d,
        oy: Math.sin(a) * d,
        d: c.rand() * 0.12,
      });
    }
  }
  c.scratch.s = list;
  return list;
}

export const violentSettle: Signature = {
  shake(u, c) {
    if (u < 0.37 || u > 0.55) return [0, 0];
    const k = (u - 0.37) / 0.18;
    const amp = c.maxShake * c.intensity * (1 - k) ** 2;
    return [Math.sin(k * 31) * amp, Math.cos(k * 27) * amp * 0.7];
  },
  draw(g, u, c) {
    const { w, h, anchor: a, colors } = c;
    const red = colors.accent;
    const hot = colors.accent_hot;
    const dark = colors.void;
    g.save();
    const gl = c.maxGlitch * c.intensity;
    // 1. screen tears with RGB-split and scan-line glitch
    if (u < 0.19) {
      const k = 1 - smooth(0, 0.19, u);
      const flash = c.flash(0.16) * bell(u, 0.01, 0.05);
      if (flash > 0) {
        g.fillStyle = css(red, flash);
        g.fillRect(0, 0, w, h);
      }
      const rnd = mulberry32(Math.floor(u * 40) + 7);
      for (let i = 0; i < 9; i++) {
        const y = rnd() * h;
        const bh = 6 + rnd() * 40;
        const dx = (rnd() - 0.5) * 220 * c.intensity * k;
        g.fillStyle = css(dark, 0.85 * k);
        g.fillRect(0, y, w, bh);
        g.fillStyle = css(red, gl * k);
        g.fillRect(dx - 7, y, w, bh);
        g.fillStyle = css([40, 200, 255, 1], gl * 0.5 * k);
        g.fillRect(dx + 7, y + 1, w, Math.max(1, bh - 2));
      }
      g.fillStyle = css([0, 0, 0, 1], 0.28 * k);
      for (let y = 0; y < h; y += 3) g.fillRect(0, y, w, 1);
    }
    // 2. red core ignites
    const ig = smooth(0.17, 0.4, u);
    if (ig > 0) {
      const pop = c.flash(0.1) * bell(u, 0.24, 0.04);
      if (pop > 0) {
        g.fillStyle = css(red, pop);
        g.fillRect(0, 0, w, h);
      }
      g.globalCompositeOperation = 'lighter';
      glow(g, a.x, a.y, a.r * (0.3 + 1.7 * easeOut(ig)), red, 0.85 * (1 - 0.5 * smooth(0.6, 1, u)));
      glow(g, a.x, a.y, a.r * 0.6 * easeOut(ig), hot, 0.9 * bell(u, 0.3, 0.12));
      g.globalCompositeOperation = 'source-over';
      g.strokeStyle = css(red, 0.8 * (1 - ig));
      g.lineWidth = 3;
      g.beginPath();
      g.arc(a.x, a.y, a.r * (0.4 + 1.5 * easeOut(ig)), 0, TAU);
      g.stroke();
    }
    // 3. angular shards snap into the panel layout
    const fade = 1 - smooth(0.72, 0.95, u);
    for (const s of shards(c)) {
      const t = snap(smooth(0.31 + s.d, 0.66 + s.d, u));
      const x = s.x + s.ox * (1 - t);
      const y = s.y + s.oy * (1 - t);
      const al = Math.min(1, 0.25 + t) * fade;
      if (al < 0.02) continue;
      g.fillStyle = css(t > 0.98 ? hot : red, al);
      g.beginPath();
      const L = s.len;
      const T = s.th;
      if (s.horiz) {
        g.moveTo(x, y);
        g.lineTo(x + L, y);
        g.lineTo(x + L - T * 2, y + T);
        g.lineTo(x, y + T);
      } else {
        g.moveTo(x, y);
        g.lineTo(x + T, y);
        g.lineTo(x + T, y + L - T * 2);
        g.lineTo(x, y + L);
      }
      g.closePath();
      g.fill();
    }
    g.restore();
  },
};

export const SIGNATURES: Record<string, Signature> = {
  holo_reassembly: holoReassembly,
  luminous_sweep: luminousSweep,
  violent_settle: violentSettle,
};
