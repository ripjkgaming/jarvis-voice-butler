'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { type SchoolGeom, bridgeSchoolGeom } from '@/lib/bridge';
import { invoke, isTauri } from '@/lib/tauri';

/* School mode entry, in one continuous line of light:
 *
 *   fold    the HUD splits down the middle; each half squeezes into its
 *           side, leaving two glowing edge lines (in the HUD's own window)
 *   fly     the window covers the screen; the lines fly out to its sides
 *   hop     (HUD on another monitor only) the lines leave that screen and
 *           slide in from the edge of the primary facing it
 *   trace   each line splits into fluid tracers that run the screen's rim,
 *           the upward ones crossing over the top, all draining to the
 *           bottom centre
 *   pool    they pool there and spread into a band exactly the panel's
 *           height; the real taskbar surfaces out of it
 *   dock    the shell shrinks the window onto the panel under the bar
 *
 * The shell (school.rs) only moves the window: the page calls
 * `school_stage` for each step, so every animation starts from where the
 * last one ended. If anything stalls, the shell docks on its own. */

const FOLD_MS = 650;
const FLY_MS = 380;
/** Crossing monitors, as a beam of data packets: how long the lines take
 *  to stream out, and the incoming stream to pack into a line. */
export const UPLINK_MS = 520;
export const DOWNLINK_MS = 560;
const IGNITE_MS = 420;
/** Tracer time = base + per-pixel: long laps run a little faster. */
const TRACE_BASE_MS = 420;
const TRACE_MS_PER_PX = 0.23;
const POOL_RISE_MS = 520;
const POOL_SETTLE_MS = 380;
const REVEAL_MS = 380;
/** Resting tail length of a tracer (px). */
const TAIL_PX = 300;
/** How far inside the screen edge the tracers run (px). */
const RIM_INSET = 2.5;
const RIM_RADIUS = 22;
/** Pool height when the primary has no bottom panel to match. */
const FALLBACK_BAR_PX = 48;

/** Where the HUD was when school mode began (screen coordinates, as
 *  [x, y, w, h]): its window frame (GTK title bar included), its content,
 *  and its screen. The return transition lands it back there. */
export type HudRect = {
  frame: number[];
  content: number[];
  screen: number[];
  /** The primary screen, where the bar docks (absent in older records). */
  primary?: number[];
};
const HUD_RECT_KEY = 'jarvis.school.hudRect';

function saveHudRect(r: HudRect) {
  try {
    localStorage.setItem(HUD_RECT_KEY, JSON.stringify(r));
  } catch {
    /* storage unavailable: the return falls back to a centred HUD */
  }
}

export function loadHudRect(): HudRect | null {
  try {
    const r = JSON.parse(localStorage.getItem(HUD_RECT_KEY) ?? 'null');
    const ok = (a: unknown) => Array.isArray(a) && a.length === 4 && a.every(Number.isFinite);
    return r && ok(r.frame) && ok(r.content) && ok(r.screen) ? r : null;
  } catch {
    return null;
  }
}

export type SchoolTx = {
  kind: 'collapse' | 'arrive';
  id: number;
  /** Frozen left/right copies of the HUD (collapse only). */
  halves: HTMLElement[] | null;
};

/** Two static copies of the live HUD, one per half, taken synchronously
 *  before React swaps the HUD out. Canvas pixels are copied across (a WebGL
 *  canvas without a preserved buffer stays blank; it is gone in 0.6 s). */
function freezeHud(): HTMLElement[] | null {
  const hud = document.querySelector<HTMLElement>('.hud');
  if (!hud) return null;
  // The HUD's dark backdrop is its own layer behind it (JarvisBackground);
  // without it the copies are see-through.
  const backdrop = document.querySelector<HTMLElement>('.jarvis-background');
  const width = window.innerWidth;
  const height = window.innerHeight;
  return ['left', 'right'].map((side) => {
    const half = document.createElement('div');
    half.className = `stx-half stx-half--${side}`;
    if (backdrop) half.appendChild(backdrop.cloneNode(true));
    const copy = hud.cloneNode(true) as HTMLElement;
    const from = hud.querySelectorAll('canvas');
    const to = copy.querySelectorAll('canvas');
    from.forEach((c, i) => {
      const d = to[i];
      if (!d) return;
      try {
        d.width = c.width;
        d.height = c.height;
        d.getContext('2d')?.drawImage(c, 0, 0);
      } catch {
        /* tainted or WebGL: leave blank */
      }
    });
    copy.style.width = `${width}px`;
    copy.style.height = `${height}px`;
    half.appendChild(copy);
    return half;
  });
}

/** Listens for the shell's school signals and owns the transition state.
 *  `barH` is the bar height once the pool has formed (kept until school
 *  mode ends, so the bar never replays its own opening). */
export function useSchoolTransition() {
  const [tx, setTx] = useState<SchoolTx | null>(null);
  const [barH, setBarH] = useState<number | null>(null);
  useEffect(() => {
    const onSignal = (e: Event) => {
      const phase = String((e as CustomEvent).detail);
      if (phase === 'collapse' || phase === 'arrive') {
        setBarH(null);
        setTx({
          kind: phase,
          id: Date.now(),
          halves: phase === 'collapse' ? freezeHud() : null,
        });
      } else if (phase === 'expand') {
        setTx(null);
        setBarH(null);
      }
    };
    window.addEventListener('jarvis-school', onSignal);
    return () => window.removeEventListener('jarvis-school', onSignal);
  }, []);
  const done = useCallback(() => setTx(null), []);
  return { tx, barH, showBar: setBarH, done };
}

/* ------------------------------ geometry ------------------------------ */

export type Seg = { x0: number; y0: number; x1: number; y1: number; a: number };

type Rim = {
  x: Float32Array;
  y: Float32Array;
  nx: Float32Array;
  ny: Float32Array;
  n: number;
  len: number;
};

const RIM_STEP = 2;

/** The screen's rim as evenly spaced points, clockwise from the bottom
 *  centre (left along the bottom, up the left side, over the top, down the
 *  right side, back along the bottom), with inward normals. */
function buildRim(w: number, h: number): Rim {
  const l = RIM_INSET;
  const r = w - RIM_INSET;
  const t = RIM_INSET;
  const b = h - RIM_INSET;
  const k = Math.min(RIM_RADIUS, (r - l) / 2, (b - t) / 2);
  const xs: number[] = [];
  const ys: number[] = [];
  const nxs: number[] = [];
  const nys: number[] = [];
  const line = (ax: number, ay: number, bx: number, by: number, nx: number, ny: number) => {
    const len = Math.hypot(bx - ax, by - ay);
    const steps = Math.max(1, Math.round(len / RIM_STEP));
    for (let i = 0; i < steps; i++) {
      xs.push(ax + ((bx - ax) * i) / steps);
      ys.push(ay + ((by - ay) * i) / steps);
      nxs.push(nx);
      nys.push(ny);
    }
  };
  const arc = (cx: number, cy: number, from: number, to: number) => {
    const steps = Math.max(1, Math.round((k * Math.abs(to - from)) / RIM_STEP));
    for (let i = 0; i < steps; i++) {
      const th = from + ((to - from) * i) / steps;
      xs.push(cx + k * Math.cos(th));
      ys.push(cy + k * Math.sin(th));
      nxs.push(-Math.cos(th));
      nys.push(-Math.sin(th));
    }
  };
  const q = Math.PI / 2;
  line(w / 2, b, l + k, b, 0, -1);
  arc(l + k, b - k, q, 2 * q);
  line(l, b - k, l, t + k, 1, 0);
  arc(l + k, t + k, 2 * q, 3 * q);
  line(l + k, t, r - k, t, 0, 1);
  arc(r - k, t + k, 3 * q, 4 * q);
  line(r, t + k, r, b - k, -1, 0);
  arc(r - k, b - k, 0, q);
  line(r - k, b, w / 2, b, 0, -1);
  const n = xs.length;
  return {
    x: Float32Array.from(xs),
    y: Float32Array.from(ys),
    nx: Float32Array.from(nxs),
    ny: Float32Array.from(nys),
    n,
    len: n * RIM_STEP,
  };
}

const rimIndex = (rim: Rim, s: number) => {
  const i = Math.round(s / RIM_STEP) % rim.n;
  return i < 0 ? i + rim.n : i;
};

/** Arc-length position on the rim nearest to (px, py). */
function rimAt(rim: Rim, px: number, py: number): number {
  let best = 0;
  let bestD = Infinity;
  for (let i = 0; i < rim.n; i++) {
    const d = (rim.x[i] - px) ** 2 + (rim.y[i] - py) ** 2;
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  }
  return best * RIM_STEP;
}

type Tracer = {
  s0: number;
  dir: 1 | -1;
  dist: number;
  tail0: number;
  t0: number;
  dur: number;
  seed: number;
  landed: boolean;
};

type Pool = {
  ph: number;
  total: number;
  arrived: number;
  level: number;
  spread: number;
  settle: number;
  alpha: number;
  /** Set once the run takes over the rise from the arrivals. */
  held: boolean;
};

type Scene = {
  segs: Seg[];
  rim: Rim | null;
  tracers: Tracer[];
  pool: Pool | null;
  /** Called in the draw loop the frame the viewport changes size. */
  onResize: ((w: number, h: number) => void) | null;
  last: number;
  packets: Packet[];
};

/* ------------------------------ drawing ------------------------------ */

export const clamp01 = (v: number) => Math.max(0, Math.min(1, v));
export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
export const easeInOut = (u: number) => 0.5 - 0.5 * Math.cos(Math.PI * u);
export const easeOut = (u: number) => 1 - (1 - u) ** 3;
export const easeIn = (u: number) => u * u * u;
export const smooth = (e0: number, e1: number, v: number) => {
  const t = clamp01((v - e0) / (e1 - e0));
  return t * t * (3 - 2 * t);
};

export function rgb(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return [95, 227, 255];
  const v = parseInt(m[1], 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

export function drawSeg(ctx: CanvasRenderingContext2D, s: Seg, c: string) {
  if (s.a <= 0.001) return;
  ctx.lineCap = 'round';
  ctx.strokeStyle = c;
  for (const [width, alpha] of [
    [14, 0.07],
    [6, 0.18],
    [2, 1],
  ] as const) {
    ctx.globalAlpha = alpha * s.a;
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(s.x0, s.y0);
    ctx.lineTo(s.x1, s.y1);
    ctx.stroke();
  }
  ctx.globalAlpha = 0.8 * s.a;
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = 0.8;
  ctx.beginPath();
  ctx.moveTo(s.x0, s.y0);
  ctx.lineTo(s.x1, s.y1);
  ctx.stroke();
}

/** One fluid tracer: a tapering, gently rippling tail behind a bright
 *  head. Marks it landed (and feeds the pool) when it reaches the end. */
function drawTracer(
  ctx: CanvasRenderingContext2D,
  scene: Scene,
  tr: Tracer,
  now: number,
  c: string
) {
  const rim = scene.rim;
  if (!rim || now < tr.t0) return;
  const u = clamp01((now - tr.t0) / tr.dur);
  const head = tr.dist * easeInOut(u);
  let tail = lerp(tr.tail0, TAIL_PX, smooth(0, 280, head));
  if (u >= 1) {
    if (!tr.landed) {
      tr.landed = true;
      if (scene.pool) scene.pool.arrived += 1;
    }
    tail *= 1 - clamp01((now - tr.t0 - tr.dur) / 280);
  }
  tail = Math.min(tail, head + tr.tail0);
  if (tail < 1) return;

  const count = Math.max(2, Math.ceil(tail / 3));
  const px: number[] = [];
  const py: number[] = [];
  for (let k = 0; k <= count; k++) {
    const f = k / count;
    const s = tr.s0 + tr.dir * (head - tail * (1 - f));
    const i = rimIndex(rim, s);
    const wob = (0.5 + 0.5 * Math.sin(s * 0.05 - now * 0.011 + tr.seed)) * 2.4 * f;
    px.push(rim.x[i] + rim.nx[i] * wob);
    py.push(rim.y[i] + rim.ny[i] * wob);
  }
  ctx.lineCap = 'round';
  ctx.strokeStyle = c;
  for (const pass of [0, 1]) {
    for (let k = 1; k <= count; k++) {
      const f = k / count;
      const w = 0.5 + 3.4 * f ** 1.6;
      ctx.globalAlpha = pass === 0 ? 0.1 * f : f ** 1.3;
      ctx.lineWidth = pass === 0 ? w * 4.5 : w;
      ctx.beginPath();
      ctx.moveTo(px[k - 1], py[k - 1]);
      ctx.lineTo(px[k], py[k]);
      ctx.stroke();
    }
  }
  // Head: a soft bloom, fading as it drains into the pool.
  const hx = px[count];
  const hy = py[count];
  const bloom = 16 * (u >= 1 ? 1 - clamp01((now - tr.t0 - tr.dur) / 200) : 1);
  if (bloom > 0.5) {
    const g = ctx.createRadialGradient(hx, hy, 0, hx, hy, bloom);
    g.addColorStop(0, 'rgba(255,255,255,0.95)');
    g.addColorStop(0.25, c);
    g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.globalAlpha = 0.9;
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(hx, hy, bloom, 0, Math.PI * 2);
    ctx.fill();
  }
}

/** The pool: liquid gathering at the bottom centre, spreading into a band
 *  the bar's height with a rippling surface that calms as it settles. */
function drawPool(
  ctx: CanvasRenderingContext2D,
  p: Pool,
  w: number,
  h: number,
  now: number,
  dt: number,
  accent: [number, number, number]
) {
  if (!p.held) {
    const share = p.total ? p.arrived / p.total : 0;
    const ease = Math.min(1, dt * 5);
    p.level += (share * 0.6 - p.level) * ease;
    p.spread += (share * 0.75 - p.spread) * ease;
  }
  const half = p.spread * (w / 2 + 40);
  if (half < 1 || p.alpha <= 0.001) return;
  const cx = w / 2;
  const amp = 3.2 * (1 - p.settle);
  const surf = (x: number) => {
    const edge = smooth(half, half - 70, Math.abs(x - cx));
    const wave =
      (Math.sin(x * 0.021 + now * 0.005) + 0.6 * Math.sin(x * 0.053 - now * 0.0037)) * amp;
    return h - (p.level * p.ph + wave * p.level) * edge;
  };
  const [r, g, b] = accent;
  ctx.globalAlpha = p.alpha;
  ctx.beginPath();
  ctx.moveTo(cx - half, h);
  for (let x = cx - half; x <= cx + half; x += 4) ctx.lineTo(x, surf(x));
  ctx.lineTo(cx + half, h);
  ctx.closePath();
  const body = ctx.createLinearGradient(0, h - p.ph, 0, h);
  body.addColorStop(0, 'rgba(7,24,36,0.97)');
  body.addColorStop(1, 'rgba(3,11,18,0.99)');
  ctx.fillStyle = body;
  ctx.fill();
  const glow = ctx.createLinearGradient(0, h - p.ph, 0, h);
  glow.addColorStop(0, `rgba(${r},${g},${b},0.32)`);
  glow.addColorStop(1, `rgba(${r},${g},${b},0.04)`);
  ctx.fillStyle = glow;
  ctx.fill();
  // The surface: a bright meniscus with a soft bloom.
  ctx.beginPath();
  for (let x = cx - half; x <= cx + half; x += 4) {
    if (x === cx - half) ctx.moveTo(x, surf(x));
    else ctx.lineTo(x, surf(x));
  }
  ctx.strokeStyle = `rgb(${r},${g},${b})`;
  ctx.lineWidth = 6;
  ctx.globalAlpha = 0.14 * p.alpha;
  ctx.stroke();
  ctx.lineWidth = 1.4;
  ctx.globalAlpha = 0.9 * p.alpha;
  ctx.stroke();
}

function drawScene(
  canvas: HTMLCanvasElement,
  ctx: CanvasRenderingContext2D,
  scene: Scene,
  now: number,
  color: string
) {
  const dpr = window.devicePixelRatio || 1;
  const w = window.innerWidth;
  const h = window.innerHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    scene.onResize?.(w, h);
  }
  const dt = Math.min(0.1, (now - scene.last) / 1000);
  scene.last = now;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = 'source-over';
  ctx.clearRect(0, 0, w, h);
  if (scene.pool) drawPool(ctx, scene.pool, w, h, now, dt, rgb(color));
  ctx.globalCompositeOperation = 'lighter';
  for (const s of scene.segs) drawSeg(ctx, s, color);
  drawPackets(ctx, scene.packets, now, color);
  for (const t of scene.tracers) drawTracer(ctx, scene, t, now, color);
  ctx.globalCompositeOperation = 'source-over';
  ctx.globalAlpha = 1;
}

/* ------------------------------ data beam ------------------------------ */

export type Dir = 'left' | 'right' | 'up' | 'down';

/** One packet of the beam between monitors: a streak (sometimes carrying a
 *  glyph) moving from `a` to `b`, accelerating out (uplink) or settling in
 *  (downlink). */
export type Packet = {
  ax: number;
  ay: number;
  bx: number;
  by: number;
  t0: number;
  dur: number;
  out: boolean;
  glyph: string | null;
  size: number;
};

const GLYPHS = '0101100110ABCDEF#';
const PACKETS = 150;
const glyph = () => (Math.random() < 0.28 ? GLYPHS[(Math.random() * GLYPHS.length) | 0] : null);
const vec = (d: Dir): [number, number] =>
  d === 'left' ? [-1, 0] : d === 'right' ? [1, 0] : d === 'up' ? [0, -1] : [0, 1];

/** Lines dissolving into packets that race off the screen's `dir` edge. */
export function uplink(segs: Seg[], dir: Dir, w: number, h: number, now: number): Packet[] {
  const [dx, dy] = vec(dir);
  const out: Packet[] = [];
  for (let i = 0; i < PACKETS && segs.length; i++) {
    const s = segs[i % segs.length];
    const f = Math.random();
    const ax = s.x0 + (s.x1 - s.x0) * f;
    const ay = s.y0 + (s.y1 - s.y0) * f;
    // Straight out through the edge, lanes drifting a little.
    const reach = dx ? (dx > 0 ? w - ax : ax) + 120 : (dy > 0 ? h - ay : ay) + 120;
    const drift = (Math.random() - 0.5) * 60;
    out.push({
      ax,
      ay,
      bx: ax + dx * reach + (dx ? 0 : drift),
      by: ay + dy * reach + (dy ? 0 : drift),
      t0: now + Math.random() * UPLINK_MS * 0.8,
      dur: 300 + Math.random() * 260,
      out: true,
      glyph: glyph(),
      size: 0.6 + Math.random(),
    });
  }
  return out;
}

/** Packets pouring in through the edge opposite `dir` (they travel toward
 *  `dir`) and packing onto `seg`. */
export function downlink(seg: Seg, dir: Dir, w: number, h: number, now: number): Packet[] {
  const [dx, dy] = vec(dir);
  const out: Packet[] = [];
  for (let i = 0; i < PACKETS; i++) {
    const f = Math.random();
    const bx = seg.x0 + (seg.x1 - seg.x0) * f;
    const by = seg.y0 + (seg.y1 - seg.y0) * f;
    const spread = (Math.random() - 0.5) * (dx ? h : w) * 0.5;
    out.push({
      ax: dx ? (dx > 0 ? -60 - Math.random() * 160 : w + 60 + Math.random() * 160) : bx + spread,
      ay: dy ? (dy > 0 ? -60 - Math.random() * 160 : h + 60 + Math.random() * 160) : by + spread,
      bx,
      by,
      t0: now + Math.random() * DOWNLINK_MS * 0.55,
      dur: 340 + Math.random() * 220,
      out: false,
      glyph: glyph(),
      size: 0.6 + Math.random(),
    });
  }
  return out;
}

const packetAt = (p: Packet, u: number): [number, number] => {
  const e = p.out ? u ** 2.2 : easeOut(u);
  return [p.ax + (p.bx - p.ax) * e, p.ay + (p.by - p.ay) * e];
};

/** All packets still in flight: a streak whose length follows its speed,
 *  a bright head, and its glyph riding along. */
export function drawPackets(ctx: CanvasRenderingContext2D, ps: Packet[], now: number, c: string) {
  ctx.lineCap = 'round';
  ctx.strokeStyle = c;
  ctx.fillStyle = c;
  ctx.font = '600 9px ui-monospace, monospace';
  for (const p of ps) {
    const u = (now - p.t0) / p.dur;
    if (u <= 0 || u >= 1) continue;
    const [x, y] = packetAt(p, u);
    const [tx, ty] = packetAt(p, Math.max(0, u - 0.12));
    const fade = p.out ? 1 - u * 0.3 : Math.min(1, u * 3);
    ctx.globalAlpha = 0.18 * fade;
    ctx.lineWidth = 4 * p.size;
    ctx.beginPath();
    ctx.moveTo(tx, ty);
    ctx.lineTo(x, y);
    ctx.stroke();
    ctx.globalAlpha = 0.85 * fade;
    ctx.lineWidth = 1.1 * p.size;
    ctx.stroke();
    if (p.glyph) {
      ctx.globalAlpha = 0.9 * fade;
      ctx.fillText(p.glyph, x + 3, y - 3);
    }
  }
}

/** When the last packet in `ps` lands (ms timestamp). */
export const beamEnd = (ps: Packet[]) => Math.max(0, ...ps.map((p) => p.t0 + p.dur));

/* ------------------------------ the run ------------------------------ */

export const stage = (name: string, nonce?: string, rect?: number[]) =>
  isTauri()
    ? invoke('school_stage', {
        stage: name,
        nonce,
        rect: rect?.map((v) => Math.round(v)),
      }).catch(() => undefined)
    : Promise.resolve(undefined);

/** Tracers out of a line: one from each end, running away from the
 *  line's middle along the rim to the bottom centre. The line itself
 *  becomes their first tails. */
function tracersFrom(rim: Rim, seg: Seg, now: number): Tracer[] {
  const mid = rimAt(rim, (seg.x0 + seg.x1) / 2, (seg.y0 + seg.y1) / 2);
  const tail0 = Math.hypot(seg.x1 - seg.x0, seg.y1 - seg.y0) / 2;
  return [
    [seg.x0, seg.y0],
    [seg.x1, seg.y1],
  ].map(([x, y]) => {
    const s0 = rimAt(rim, x, y);
    let delta = s0 - mid;
    if (delta > rim.len / 2) delta -= rim.len;
    if (delta < -rim.len / 2) delta += rim.len;
    const dir: 1 | -1 = delta >= 0 ? 1 : -1;
    const dist = dir > 0 ? rim.len - s0 : s0;
    return {
      s0,
      dir,
      dist,
      tail0,
      t0: now,
      dur: TRACE_BASE_MS + dist * TRACE_MS_PER_PX,
      seed: Math.random() * 10,
      landed: false,
    };
  });
}

export function SchoolTransition({
  tx,
  color,
  onBar,
  onDone,
}: {
  tx: SchoolTx;
  color: string;
  /** The pool has formed: show the real bar at this height (px). */
  onBar: (height: number) => void;
  onDone: () => void;
}) {
  const layer = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const colorRef = useRef(color);
  colorRef.current = color;

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    let alive = true;
    const root = document.documentElement;
    root.classList.add('school-tx');
    const halves = tx.halves ?? [];
    const scene: Scene = {
      segs: [],
      rim: null,
      tracers: [],
      pool: null,
      onResize: null,
      last: performance.now(),
      packets: [],
    };

    let raf = 0;
    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      drawScene(canvas, ctx, scene, now, colorRef.current);
    };
    raf = requestAnimationFrame(frame);

    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
    const until = async (cond: () => boolean, timeout: number) => {
      const t0 = performance.now();
      while (alive && !cond()) {
        if (performance.now() - t0 > timeout) return false;
        await sleep(30);
      }
      return alive;
    };
    const tween = (ms: number, fn: (e: number) => void, ease: (u: number) => number = easeInOut) =>
      new Promise<void>((done) => {
        const t0 = performance.now();
        const step = () => {
          if (!alive) return done();
          const u = clamp01((performance.now() - t0) / ms);
          fn(ease(u));
          if (u < 1) setTimeout(step, 16);
          else done();
        };
        step();
      });
    const measure = async (): Promise<SchoolGeom | null> => {
      const nonce = Math.random().toString(36).slice(2, 12);
      await stage('measure', nonce);
      const t0 = performance.now();
      while (alive && performance.now() - t0 < 1600) {
        const geom = await bridgeSchoolGeom(nonce).catch(() => null);
        if (geom) return geom;
        await sleep(70);
      }
      return null;
    };
    const fills = (r: { w: number; h: number } | undefined) =>
      !!r && window.innerWidth >= r.w - 2 && window.innerHeight >= r.h - 2;

    const run = async () => {
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        await stage('dock');
        await until(() => window.innerHeight < 300, 2000);
        if (alive) onDone();
        return;
      }

      let geom: SchoolGeom | null = null;
      let left: Seg;
      let right: Seg;

      if (tx.kind === 'collapse') {
        // Measure first: GTK draws the HUD's title bar inside the window,
        // so dropping it needs the exact content rect to pin the window to
        // (else the content would jump up by the bar's height).
        const w0 = window.innerWidth;
        const h0 = window.innerHeight;
        geom = await measure();
        if (!alive) return;
        let content: SchoolGeom['hud'] | null = null;
        if (geom) {
          const bar = Math.max(0, geom.hud.h - h0);
          content = { x: geom.hud.x, y: geom.hud.y + bar, w: geom.hud.w, h: geom.hud.h - bar };
          saveHudRect({
            frame: [geom.out.x + geom.hud.x, geom.out.y + geom.hud.y, geom.hud.w, geom.hud.h],
            content: [geom.out.x + content.x, geom.out.y + content.y, content.w, content.h],
            screen: [geom.out.x, geom.out.y, geom.out.w, geom.out.h],
            primary: [geom.primary.x, geom.primary.y, geom.primary.w, geom.primary.h],
          });
          void stage('unframe', undefined, [
            geom.out.x + content.x,
            geom.out.y + content.y,
            content.w,
            content.h,
          ]);
        } else {
          void stage('unframe');
        }

        // Fold: the halves squeeze into the HUD's sides, their light
        // gathering into two edge lines.
        left = { x0: 1, y0: 0, x1: 1, y1: h0, a: 0 };
        right = { x0: w0 - 1, y0: 0, x1: w0 - 1, y1: h0, a: 0 };
        scene.segs = [left, right];
        if (layer.current) {
          for (const half of halves) layer.current.appendChild(half);
          const keys = [{ transform: 'scaleX(1)' }, { transform: 'scaleX(0.003)' }];
          for (const half of halves) {
            half.animate(keys, {
              duration: FOLD_MS,
              easing: 'cubic-bezier(0.7, 0, 0.84, 0)',
              fill: 'forwards',
            });
          }
        }
        await tween(FOLD_MS, (e) => (left.a = right.a = e), easeIn);
        for (const half of halves) half.style.visibility = 'hidden';
        if (!alive) return;

        // Cover the screen: the lines stay put on screen (now in screen
        // coordinates) and fly out to its sides.
        const hud = content;
        scene.onResize = (w, h) => {
          if (hud && fills(geom?.out)) {
            left.x0 = left.x1 = hud.x + 1;
            right.x0 = right.x1 = hud.x + hud.w - 1;
            left.y0 = right.y0 = hud.y;
            left.y1 = right.y1 = hud.y + hud.h;
          } else if (!hud && (w > w0 + 40 || h > h0 + 40)) {
            left.x0 = left.x1 = 1.5;
            right.x0 = right.x1 = w - 1.5;
          }
        };
        await stage('cover');
        await until(
          () =>
            geom ? fills(geom.out) : window.innerWidth > w0 + 40 || window.innerHeight > h0 + 40,
          1500
        );
        scene.onResize(window.innerWidth, window.innerHeight);
        scene.onResize = null;
        const w = window.innerWidth;
        const lx = left.x0;
        const rx = right.x0;
        await tween(
          FLY_MS,
          (e) => {
            left.x0 = left.x1 = lerp(lx, 1.5, e);
            right.x0 = right.x1 = lerp(rx, w - 1.5, e);
          },
          easeOut
        );
      } else {
        // Arrive (HUD was hidden): cover the primary, then light the
        // lines at its sides.
        await stage('primary');
        await sleep(380);
        geom = await measure();
        await until(() => fills(geom?.primary), 1200);
        const w = window.innerWidth;
        const h = window.innerHeight;
        left = { x0: 1.5, y0: h / 2, x1: 1.5, y1: h / 2, a: 0 };
        right = { x0: w - 1.5, y0: h / 2, x1: w - 1.5, y1: h / 2, a: 0 };
        scene.segs = [left, right];
        await tween(
          IGNITE_MS,
          (e) => {
            left.a = right.a = e;
            left.y0 = right.y0 = h / 2 - h * 0.22 * e;
            left.y1 = right.y1 = h / 2 + h * 0.22 * e;
          },
          easeOut
        );
      }
      if (!alive) return;

      let sources: Seg[] = [left, right];
      if (geom && !geom.same && geom.dir && tx.kind === 'collapse') {
        // Beam across: the lines dissolve into packets streaming off
        // toward the primary...
        const dir = geom.dir;
        const w = window.innerWidth;
        const h = window.innerHeight;
        scene.packets = uplink(scene.segs, dir, w, h, performance.now());
        const outEnd = beamEnd(scene.packets);
        await tween(UPLINK_MS, (e) => scene.segs.forEach((seg) => (seg.a = 1 - e)), easeIn);
        await until(() => performance.now() >= outEnd, 1500);
        scene.segs = [];
        scene.packets = [];
        await stage('primary');
        await sleep(420);
        await until(() => fills(geom?.primary), 1200);
        // ...and pour in through its facing edge, packing into a line down
        // the middle that splits out to the sides.
        const pw = window.innerWidth;
        const ph = window.innerHeight;
        const mid: Seg = { x0: pw / 2, y0: ph * 0.28, x1: pw / 2, y1: ph * 0.72, a: 0 };
        scene.segs = [mid];
        scene.packets = downlink(mid, dir, pw, ph, performance.now());
        const inEnd = beamEnd(scene.packets);
        await tween(DOWNLINK_MS, (e) => (mid.a = e), easeIn);
        await until(() => performance.now() >= inEnd, 1500);
        scene.packets = [];
        const l: Seg = { ...mid };
        const r: Seg = { ...mid };
        scene.segs = [l, r];
        await tween(
          FLY_MS,
          (e) => {
            l.x0 = l.x1 = lerp(pw / 2, 1.5, e);
            r.x0 = r.x1 = lerp(pw / 2, pw - 1.5, e);
          },
          easeOut
        );
        sources = [l, r];
      }
      if (!alive) return;

      // Trace: the lines become fluid tracers running the rim.
      const w = window.innerWidth;
      const h = window.innerHeight;
      const rim = buildRim(w, h);
      scene.rim = rim;
      const bar = geom && geom.panel >= 24 ? geom.panel : FALLBACK_BAR_PX;
      const now = performance.now();
      scene.tracers = sources.flatMap((s) => tracersFrom(rim, s, now));
      scene.pool = {
        ph: bar,
        total: scene.tracers.length,
        arrived: 0,
        level: 0,
        spread: 0,
        settle: 0,
        alpha: 1,
        held: false,
      };
      scene.segs = [];
      const longest = Math.max(...scene.tracers.map((t) => t.dur));
      await until(() => scene.tracers.every((t) => t.landed), longest + 1500);
      if (!alive) return;

      // Pool: rise to the bar's height across the whole bottom, then calm.
      const pool = scene.pool;
      pool.held = true;
      const l0 = pool.level;
      const s0 = pool.spread;
      await tween(POOL_RISE_MS, (e) => {
        pool.level = lerp(l0, 1, e);
        pool.spread = lerp(s0, 1, e);
      });
      await tween(POOL_SETTLE_MS, (e) => (pool.settle = e), easeOut);
      if (!alive) return;

      // The real bar surfaces out of the pool, then the window docks.
      onBar(bar);
      await tween(REVEAL_MS, (e) => (pool.alpha = 1 - e));
      await stage('dock');
      await until(() => window.innerHeight < 300, 2500);
      if (alive) onDone();
    };
    void run();

    return () => {
      alive = false;
      cancelAnimationFrame(raf);
      root.classList.remove('school-tx');
      for (const half of halves) half.remove();
    };
  }, [tx, onBar, onDone]);

  return (
    <div className="stx" aria-hidden="true">
      <div ref={layer} className="stx-halves" />
      <canvas ref={canvasRef} className="stx-canvas" />
    </div>
  );
}
