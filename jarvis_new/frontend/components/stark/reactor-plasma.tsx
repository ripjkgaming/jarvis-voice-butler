'use client';

import { hash01, useBakedCanvas } from '@/hooks/hud/use-baked-frames';
import type { JarvisState } from '@/hooks/hud/use-jarvis-state';
import type { reactorWarnings } from './metrics';
import s from './stark.module.css';

/**
 * Particle field around the arc-reactor core. Nothing here does particle
 * math at runtime:
 *  - four still layers (dust shells, tilted orbits, spiral arms, radial
 *    flow) are baked once per colour into a single frame each, then moved
 *    by CSS transforms on their own compositor layers;
 *  - the hot plasma at the centre is a small baked loop per state, blitted
 *    with one drawImage per tick.
 * Bakes are cached, so a state Jarvis has already been in swaps instantly.
 */

type RGB = readonly [number, number, number];

const TAU = Math.PI * 2;

function rgbOf(hex: string): RGB {
  let h = hex.replace('#', '');
  if (h.length === 3) {
    h = h
      .split('')
      .map((c) => c + c)
      .join('');
  }
  const n = Number.parseInt(h.slice(0, 6), 16) || 0;
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Mix toward white: particle hot spots read whiter than their tint. */
function lift(c: RGB, t: number): RGB {
  return [c[0] + (255 - c[0]) * t, c[1] + (255 - c[1]) * t, c[2] + (255 - c[2]) * t];
}

function rgba(c: RGB, a: number): string {
  return `rgba(${Math.round(c[0])},${Math.round(c[1])},${Math.round(c[2])},${Math.max(0, Math.min(1, a)).toFixed(3)})`;
}

const frac = (v: number) => v - Math.floor(v);

/** Roughly unit-variance gaussian from three frame-stable hashes. */
const gauss = (k: number, salt: number) =>
  (hash01(k, salt) + hash01(k, salt + 1) + hash01(k, salt + 2) - 1.5) * 2;

const SPRITES = new Map<string, HTMLCanvasElement>();

/** Soft glow stamp, made once per colour; every glowing particle is a blit of it. */
function glowSprite(c: RGB): HTMLCanvasElement {
  const key = c.join(',');
  const hit = SPRITES.get(key);
  if (hit) return hit;
  const el = document.createElement('canvas');
  SPRITES.set(key, el);
  el.width = 64;
  el.height = 64;
  const ctx = el.getContext('2d');
  if (ctx) {
    const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
    g.addColorStop(0, 'rgba(255,255,255,1)');
    g.addColorStop(0.16, rgba(lift(c, 0.55), 0.9));
    g.addColorStop(0.42, rgba(c, 0.32));
    g.addColorStop(1, rgba(c, 0));
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 64, 64);
  }
  return el;
}

function stamp(
  ctx: CanvasRenderingContext2D,
  sprite: HTMLCanvasElement,
  x: number,
  y: number,
  r: number,
  a: number
) {
  ctx.globalAlpha = Math.max(0, Math.min(1, a));
  ctx.drawImage(sprite, x - r, y - r, r * 2, r * 2);
  ctx.globalAlpha = 1;
}

function dot(ctx: CanvasRenderingContext2D, x: number, y: number, size: number, fill: string) {
  ctx.fillStyle = fill;
  ctx.fillRect(x - size / 2, y - size / 2, size, size);
}

/** Origin at the centre, `span` units across the shorter side, additive
 *  blending. Returns one device pixel in those units. */
function setup(ctx: CanvasRenderingContext2D, w: number, h: number, span: number): number {
  const k = Math.min(w, h) / span;
  ctx.translate(w / 2, h / 2);
  ctx.scale(k, k);
  ctx.globalCompositeOperation = 'lighter';
  return 1 / k;
}

/** Radius bands that line up with the SVG rings (coils, state arcs, segments). */
const BANDS: readonly (readonly [number, number, number])[] = [
  [100, 13, 0.2],
  [142, 5, 0.12],
  [178, 9, 0.2],
  [206, 3.5, 0.1],
  [238, 11, 0.22],
];

function bandRadius(k: number, salt: number): number {
  const pick = hash01(k, salt);
  let acc = 0;
  for (const [r, sd, wt] of BANDS) {
    acc += wt;
    if (pick < acc) return r + gauss(k, salt + 1) * sd;
  }
  return 40 + hash01(k, salt + 5) * 222;
}

// ------------------------------------------------------------ still layers

function drawDust(ctx: CanvasRenderingContext2D, w: number, h: number, c: RGB) {
  const px = setup(ctx, w, h, 600);
  const sprite = glowSprite(c);
  for (let k = 0; k < 2600; k += 1) {
    const r = bandRadius(k, 1);
    const a = hash01(k, 10) * TAU;
    const size = Math.max(px, 0.45 + hash01(k, 11) * 0.95);
    dot(
      ctx,
      Math.cos(a) * r,
      Math.sin(a) * r,
      size,
      rgba(lift(c, hash01(k, 12) * 0.6), 0.1 + hash01(k, 13) * 0.5)
    );
  }
  // Faint radial striations through the outer shells.
  ctx.lineWidth = Math.max(px, 0.6);
  for (let k = 0; k < 72; k += 1) {
    const a = (k / 72) * TAU + hash01(k, 20) * 0.05;
    const r0 = 146 + hash01(k, 21) * 50;
    const r1 = r0 + 18 + hash01(k, 22) * 70;
    ctx.strokeStyle = rgba(c, 0.04 + hash01(k, 23) * 0.08);
    ctx.beginPath();
    ctx.moveTo(Math.cos(a) * r0, Math.sin(a) * r0);
    ctx.lineTo(Math.cos(a) * r1, Math.sin(a) * r1);
    ctx.stroke();
  }
  for (let k = 0; k < 190; k += 1) {
    const r = bandRadius(k, 30);
    const a = hash01(k, 40) * TAU;
    stamp(
      ctx,
      sprite,
      Math.cos(a) * r,
      Math.sin(a) * r,
      2.2 + hash01(k, 41) * 5,
      0.18 + hash01(k, 42) * 0.5
    );
  }
}

const ORBITS = [
  { a: 198, tilt: 66, rot: 18, heads: 2 },
  { a: 170, tilt: 74, rot: 78, heads: 3 },
  { a: 228, tilt: 60, rot: 138, heads: 2 },
  { a: 150, tilt: 52, rot: 104, heads: 1 },
] as const;

/** Tilted orbits seen in perspective: comet trails fade behind the core. */
function drawOrbits(ctx: CanvasRenderingContext2D, w: number, h: number, c: RGB) {
  const px = setup(ctx, w, h, 600);
  const sprite = glowSprite(c);
  ORBITS.forEach((o, j) => {
    const b = o.a * Math.cos((o.tilt * Math.PI) / 180);
    const rot = (o.rot * Math.PI) / 180;
    const cr = Math.cos(rot);
    const sr = Math.sin(rot);
    ctx.lineWidth = Math.max(px, 0.6);
    ctx.strokeStyle = rgba(c, 0.09);
    ctx.beginPath();
    ctx.ellipse(0, 0, o.a, b, rot, 0, TAU);
    ctx.stroke();
    const place = (th: number, scatter: number): [number, number, number] => {
      const x0 = Math.cos(th) * (o.a + scatter);
      const y0 = Math.sin(th) * (b + scatter * 0.5);
      // Lower half of the ellipse is the near side.
      const depth = 0.4 + 0.6 * (Math.sin(th) * 0.5 + 0.5);
      return [x0 * cr - y0 * sr, x0 * sr + y0 * cr, depth];
    };
    const N = 700;
    for (let p = 0; p < N; p += 1) {
      const k = j * 1000 + p;
      const th = (p / N) * TAU + hash01(k, 50) * 0.006;
      const trail = frac((th / TAU) * o.heads);
      const bright = trail ** 4;
      if (bright < 0.03 && hash01(k, 51) < 0.55) continue;
      const [x, y, depth] = place(th, gauss(k, 52) * (1.2 + (1 - trail) * 3.5));
      const size = Math.max(px, 0.5 + bright * 1.7);
      dot(ctx, x, y, size, rgba(lift(c, bright * 0.75), (0.1 + bright * 0.8) * depth));
    }
    for (let hh = 0; hh < o.heads; hh += 1) {
      const [x, y, depth] = place((TAU * (hh + 1)) / o.heads - 0.004, 0);
      stamp(ctx, sprite, x, y, 10, 0.85 * depth);
      stamp(ctx, sprite, x, y, 3.2, depth);
    }
  });
}

/** Spiral arms feeding the core, plus an inner dust cloud. */
function drawSpiral(ctx: CanvasRenderingContext2D, w: number, h: number, c: RGB) {
  const px = setup(ctx, w, h, 600);
  const sprite = glowSprite(c);
  const ARMS = 6;
  for (let arm = 0; arm < ARMS; arm += 1) {
    for (let p = 0; p < 320; p += 1) {
      const k = arm * 1000 + p;
      const u = p / 320;
      const phi = u * 5.4;
      const r = 20 * Math.exp(0.3 * phi) + gauss(k, 60) * 2.4 * u;
      const ang = (arm / ARMS) * TAU + phi + gauss(k, 63) * 0.05 * (0.4 + u);
      const x = Math.cos(ang) * r;
      const y = Math.sin(ang) * r;
      const heat = 1 - u;
      if (p % 11 === 0) stamp(ctx, sprite, x, y, 1.8 + heat * 3.2, 0.5 * heat + 0.08);
      dot(
        ctx,
        x,
        y,
        Math.max(px, 0.5 + heat * 1.1),
        rgba(lift(c, heat * 0.65), 0.06 + 0.75 * heat ** 1.2)
      );
    }
  }
  for (let k = 0; k < 600; k += 1) {
    const r = 16 + hash01(k, 70) ** 0.7 * 96;
    const a = hash01(k, 71) * TAU;
    dot(
      ctx,
      Math.cos(a) * r,
      Math.sin(a) * r,
      Math.max(px, 0.4 + hash01(k, 72) * 0.8),
      rgba(lift(c, 0.4), 0.08 + hash01(k, 73) * 0.32)
    );
  }
}

/** Radial streaks: CSS drives them inward (listening), outward
 *  (speaking) or round (thinking). */
function drawFlow(salt: number) {
  return (ctx: CanvasRenderingContext2D, w: number, h: number, c: RGB) => {
    const px = setup(ctx, w, h, 600);
    ctx.lineWidth = Math.max(px, 0.7);
    for (let k = 0; k < 340; k += 1) {
      const a = hash01(k, salt) * TAU;
      const r = 112 + hash01(k, salt + 1) * 140;
      const len = 3 + hash01(k, salt + 2) * 13;
      ctx.strokeStyle = rgba(lift(c, hash01(k, salt + 3) * 0.55), 0.14 + hash01(k, salt + 4) * 0.5);
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * r, Math.sin(a) * r);
      ctx.lineTo(Math.cos(a) * (r + len), Math.sin(a) * (r + len));
      ctx.stroke();
    }
    for (let k = 0; k < 160; k += 1) {
      const a = hash01(k, salt + 5) * TAU;
      const r = 110 + hash01(k, salt + 6) * 145;
      dot(
        ctx,
        Math.cos(a) * r,
        Math.sin(a) * r,
        Math.max(px, 1.1),
        rgba(lift(c, 0.5), 0.25 + hash01(k, salt + 7) * 0.45)
      );
    }
  };
}

// ------------------------------------------------------------ plasma loop

type Mode = JarvisState | 'muted';

function drawPlasma(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  i: number,
  n: number,
  c: RGB,
  mode: Mode
) {
  const px = setup(ctx, w, h, 200);
  const sprite = glowSprite(c);
  const t = i / n;
  const T = TAU * t;
  // Every motion term below uses whole harmonics of T so the loop is seamless.
  const spin = mode === 'thinking' ? 3 : mode === 'idle' || mode === 'muted' ? 1 : 2;

  const pulse =
    mode === 'speaking'
      ? 0.8 + 0.2 * Math.sin(T * 4)
      : mode === 'thinking'
        ? 0.85 + 0.15 * Math.sin(T * 6)
        : 0.9 + 0.1 * Math.sin(T);
  const bloom = ctx.createRadialGradient(0, 0, 0, 0, 0, 96);
  bloom.addColorStop(0, rgba([255, 255, 255], 0.9 * pulse));
  bloom.addColorStop(0.12, rgba(lift(c, 0.5), 0.7 * pulse));
  bloom.addColorStop(0.38, rgba(c, 0.26 * pulse));
  bloom.addColorStop(1, rgba(c, 0));
  ctx.fillStyle = bloom;
  ctx.fillRect(-100, -100, 200, 200);

  // Plasma filaments licking out toward the coils.
  const F = mode === 'thinking' ? 14 : mode === 'idle' || mode === 'muted' ? 8 : 11;
  for (let f = 0; f < F; f += 1) {
    const a0 = (f / F) * TAU + 0.4 * Math.sin(T + hash01(f, 31) * TAU);
    const reach = 60 + 28 * (0.5 + 0.5 * Math.sin(T * spin + hash01(f, 32) * TAU));
    const bend = 0.9 * Math.sin(T * spin + hash01(f, 33) * TAU);
    const flick = 0.55 + 0.45 * Math.sin(T * spin * 2 + hash01(f, 34) * TAU);
    const sx = Math.cos(a0) * 8;
    const sy = Math.sin(a0) * 8;
    const qx = Math.cos(a0 + bend) * reach * 0.55;
    const qy = Math.sin(a0 + bend) * reach * 0.55;
    const ex = Math.cos(a0 + bend * 0.35) * reach;
    const ey = Math.sin(a0 + bend * 0.35) * reach;
    const path = () => {
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.quadraticCurveTo(qx, qy, ex, ey);
      ctx.stroke();
    };
    ctx.lineWidth = 5;
    ctx.strokeStyle = rgba(c, 0.1 * flick);
    path();
    ctx.lineWidth = Math.max(px, 1.1);
    ctx.strokeStyle = rgba(lift(c, 0.7), 0.7 * flick);
    path();
    stamp(ctx, sprite, ex, ey, 5, 0.6 * flick);
  }

  // Hot particles: orbiting, drawn in (listening) or thrown out (speaking).
  for (let k = 0; k < 380; k += 1) {
    const dir = hash01(k, 42) < 0.5 ? 1 : -1;
    const harm = 1 + Math.floor(hash01(k, 43) * spin);
    let ang = hash01(k, 41) * TAU + dir * harm * T;
    let r: number;
    let alpha: number;
    if (mode === 'listening' || mode === 'speaking') {
      const u = frac(t + hash01(k, 44));
      r = mode === 'listening' ? 96 - u * 86 : 10 + u * 88;
      ang += mode === 'listening' ? u * 1.8 : -u * 1.2;
      alpha = Math.sin(u * Math.PI) * (0.5 + 0.5 * hash01(k, 46));
    } else {
      r = 14 + hash01(k, 45) ** 0.8 * 80 + 3 * Math.sin(T * 2 + hash01(k, 41) * TAU);
      alpha = 0.3 + 0.5 * (0.5 + 0.5 * Math.sin(T * harm + hash01(k, 46) * TAU));
    }
    const heat = Math.max(0, 1 - r / 100);
    const x = Math.cos(ang) * r;
    const y = Math.sin(ang) * r;
    if (k % 7 === 0) stamp(ctx, sprite, x, y, 2.4 + heat * 3, alpha * 0.7);
    else dot(ctx, x, y, Math.max(px, 0.6 + heat * 0.9), rgba(lift(c, heat * 0.8), alpha));
  }

  if (mode === 'speaking') {
    // Voice ripples rolling out of the heart.
    for (let j = 0; j < 4; j += 1) {
      const u = frac(t * 2 + j / 4);
      ctx.lineWidth = 0.4 + 1.6 * (1 - u);
      ctx.strokeStyle = rgba(lift(c, 0.4), 0.55 * (1 - u));
      ctx.beginPath();
      ctx.arc(0, 0, 12 + u * 86, 0, TAU);
      ctx.stroke();
    }
  } else if (mode === 'listening') {
    // Rings collapsing inward as sound is taken in.
    for (let j = 0; j < 3; j += 1) {
      const u = frac(t + j / 3);
      ctx.lineWidth = 0.5 + u;
      ctx.strokeStyle = rgba(lift(c, 0.4), 1.6 * u * (1 - u));
      ctx.beginPath();
      ctx.arc(0, 0, 96 - u * 84, 0, TAU);
      ctx.stroke();
    }
  } else if (mode === 'thinking') {
    // Arcing discharges to the coil ring; each bolt holds for two frames.
    const bucket = Math.floor(i / 2);
    for (let b = 0; b < 3; b += 1) {
      if (hash01(bucket, b, 51) > 0.55) continue;
      const a = hash01(bucket, b, 52) * TAU;
      const pts: [number, number][] = [];
      for (let sgm = 0; sgm <= 9; sgm += 1) {
        const r = 14 + (sgm / 9) * 83;
        const da = sgm === 0 ? 0 : (hash01(bucket, b, sgm, 53) - 0.5) * 0.38;
        pts.push([Math.cos(a + da) * r, Math.sin(a + da) * r]);
      }
      const bolt = () => {
        ctx.beginPath();
        pts.forEach(([x, y], q) => (q === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
        ctx.stroke();
      };
      ctx.lineWidth = 4;
      ctx.strokeStyle = rgba(c, 0.22);
      bolt();
      ctx.lineWidth = Math.max(px, 1);
      ctx.strokeStyle = 'rgba(255,255,255,0.9)';
      bolt();
      const [lx, ly] = pts[pts.length - 1];
      stamp(ctx, sprite, lx, ly, 6, 0.9);
    }
  }

  stamp(ctx, sprite, 0, 0, 24, 0.9);
  stamp(ctx, sprite, 0, 0, 9, 1);
}

// ------------------------------------------------------------ component

function crispScale(cap: number): number {
  if (typeof window === 'undefined') return 1;
  return Math.min(cap, window.devicePixelRatio || 1);
}

function useStill(
  id: string,
  color: string,
  draw: (ctx: CanvasRenderingContext2D, w: number, h: number, c: RGB) => void
) {
  return useBakedCanvas({
    frames: 1,
    fps: 1,
    paused: true,
    scale: crispScale(1.5),
    bakeKey: color,
    layoutBox: true,
    cacheId: `rx-${id}`,
    render: (ctx, w, h) => draw(ctx, w, h, rgbOf(color)),
  });
}

const flowA = drawFlow(80);
const flowB = drawFlow(90);

/** Layers sit between the reactor's SVG rings; see Reactor for order. */
export function useReactorPlasma(color: string, jarvis: JarvisState, muted: boolean) {
  const mode: Mode = muted ? 'muted' : jarvis;
  const calm = mode === 'idle' || mode === 'muted';
  const dust = useStill('dust', color, drawDust);
  const orbits = useStill('orbits', color, drawOrbits);
  const spiral = useStill('spiral', color, drawSpiral);
  const flowARef = useStill('flow-a', color, flowA);
  const flowBRef = useStill('flow-b', color, flowB);
  const plasma = useBakedCanvas({
    // Idle loops over 4 s so its slowest orbit stays lazy; active states 2 s.
    frames: 48,
    fps: calm ? 12 : 24,
    paused: muted,
    scale: crispScale(1.25),
    seed: 4242,
    bakeKey: `${color}|${mode}`,
    layoutBox: true,
    cacheId: 'rx-plasma',
    render: (ctx, w, h, i, n) => drawPlasma(ctx, w, h, i, n, rgbOf(color), mode),
  });

  return {
    dust: <canvas ref={dust} className={`${s.rxLayer} ${s.pxDust}`} aria-hidden="true" />,
    orbits: <canvas ref={orbits} className={`${s.rxLayer} ${s.pxOrbits}`} aria-hidden="true" />,
    inner: (
      <>
        <canvas ref={spiral} className={`${s.rxLayer} ${s.pxSpiral}`} aria-hidden="true" />
        <canvas ref={flowARef} className={`${s.rxLayer} ${s.pxFlow}`} aria-hidden="true" />
        <canvas
          ref={flowBRef}
          className={`${s.rxLayer} ${s.pxFlow} ${s.pxFlowB}`}
          aria-hidden="true"
        />
        <canvas ref={plasma} className={s.pxPlasma} aria-hidden="true" />
      </>
    ),
  };
}

/** One cached still, shared by every warning combination. No new frame loop,
 * colour rebakes, or SVG filters as telemetry changes. The existing voice
 * particles remain beneath this additive warm field. */
function drawWarning(ctx: CanvasRenderingContext2D, w: number, h: number, c: RGB) {
  ctx.save();
  drawDust(ctx, w, h, c);
  ctx.restore();
  ctx.save();
  drawSpiral(ctx, w, h, c);
  ctx.restore();
}

export function ReactorWarningLight({ warning }: { warning: ReturnType<typeof reactorWarnings> }) {
  const particles = useStill('warning', '#ffb347', drawWarning);
  return (
    <div
      className={s.rxWarning}
      data-reactor-warning={warning.lit ? 'true' : 'false'}
      data-warning-sources={warning.active.join(' ')}
      aria-hidden="true"
    >
      <canvas ref={particles} className={`${s.rxLayer} ${s.rxWarningParticles}`} />
      <div className={s.rxWarningGlow} />
      {(['cpu', 'mem', 'temp', 'power'] as const).map((key) => (
        <div
          key={key}
          className={s.rxWarningSource}
          data-source={key}
          data-on={warning.warnings[key] ? 'true' : undefined}
        />
      ))}
    </div>
  );
}
