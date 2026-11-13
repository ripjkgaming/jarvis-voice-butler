'use client';

import { type MutableRefObject, memo, useEffect, useRef } from 'react';
import {
  type GlyphAtlas,
  RAMP,
  type ReactorFrame,
  glyphAtlas,
  hash,
  prefersReducedMotion,
  reactorField,
} from '@/lib/ascii';

export type ReactorMood =
  | 'ringing'
  | 'speaking'
  | 'listening'
  | 'hold'
  | 'owner'
  | 'alert'
  | 'ended';

const SPEED: Record<ReactorMood, number> = {
  ringing: 1.8,
  speaking: 1.2,
  listening: 1,
  hold: 0.28,
  owner: 0.6,
  alert: 1.5,
  ended: 0.18,
};
const DIM: Record<ReactorMood, number> = {
  ringing: 1,
  speaking: 1,
  listening: 0.9,
  hold: 0.5,
  owner: 0.62,
  alert: 1,
  ended: 0.38,
};

/** Shared smoothed level: fast attack, slow release, so packet jitter
 *  from the 20 Hz `speaking` stream reads as a voice envelope. */
function follow(cur: number, target: number, dt: number) {
  const k = target > cur ? 1 - Math.exp(-dt * 28) : 1 - Math.exp(-dt * 6);
  return cur + (target - cur) * k;
}

type Live = { mood: ReactorMood; color: string; rebuild: boolean; shockAt: number };

function useLive(mood: ReactorMood, color: string) {
  const live = useRef<Live>({ mood, color, rebuild: true, shockAt: -1e9 });
  useEffect(() => {
    const L = live.current;
    if (L.color !== color) L.rebuild = true;
    if (L.mood !== mood) L.shockAt = performance.now();
    L.mood = mood;
    L.color = color;
  }, [mood, color]);
  return live;
}

function fitCanvas(canvas: HTMLCanvasElement, ctx: CanvasRenderingContext2D) {
  const rect = canvas.getBoundingClientRect();
  const w = Math.max(1, rect.width);
  const h = Math.max(1, rect.height);
  const dpr = Math.min(1.5, window.devicePixelRatio || 1);
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { w, h, dpr };
}

/** The call arc reactor: the HUD's ASCII reactor field (lib/ascii
 *  reactorField), its core driven by Jarvis's live output level. Ringing
 *  throws a glyph shockwave each ring; hold and ended spin down and dim. */
export const CallReactor = memo(function CallReactor({
  mood,
  color,
  level,
  className,
}: {
  mood: ReactorMood;
  color: string;
  level: MutableRefObject<number>;
  className?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const live = useLive(mood, color);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    let w = 0;
    let h = 0;
    let dpr = 1;
    let cw = 9;
    let ch = 15;
    let atlas: GlyphAtlas | null = null;
    const start = performance.now();
    let last = start;
    let clock = 0;
    let smooth = 0;
    let raf = 0;
    const frame: ReactorFrame = { build: 0, t: 0, boost: 0, speed: 1, shock: null };

    const resize = () => {
      ({ w, h, dpr } = fitCanvas(canvas, ctx));
      cw = Math.max(5, Math.round(Math.min(w, h) / 78));
      ch = Math.round(cw * 1.6);
      live.current.rebuild = true;
    };

    const draw = (now: number) => {
      raf = requestAnimationFrame(draw);
      const L = live.current;
      if (!w) return;
      if (L.rebuild || !atlas) {
        atlas = glyphAtlas(cw, ch, dpr, L.color);
        L.rebuild = false;
      }
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const reduced = prefersReducedMotion();
      smooth = follow(smooth, level.current, dt);
      clock += dt;
      const t = (now - start) / 1000;
      frame.t = reduced ? 3 : clock;
      frame.build = reduced ? 3 : t;
      frame.speed = SPEED[L.mood] * (1 + smooth * 0.8);
      // ring cadence: one shockwave per 1.4 s while ringing
      let shockAge = (now - L.shockAt) / 1000;
      if (L.mood === 'ringing') shockAge = Math.min(shockAge, (t % 1.4) + (t < 1.4 ? 9 : 0));
      if (L.mood === 'alert') shockAge = Math.min(shockAge, t % 0.9);
      const voice = L.mood === 'speaking' ? smooth * 2.2 : 0;
      frame.boost = voice + (shockAge < 1 ? 1.1 * Math.exp(-shockAge * 3) : 0);
      frame.shock = shockAge < 0.9 ? [0.3 + shockAge * 1.1, (1 - shockAge / 0.9) * 0.95] : null;
      ctx.clearRect(0, 0, w, h);
      const dim = DIM[L.mood];
      const cx = w / 2;
      const cy = h / 2;
      const R = (Math.min(w, h) / 2) * 0.84;
      const aa = (0.55 * ch) / R;
      const cols = Math.ceil(w / cw);
      const rows = Math.ceil(h / ch);
      const reach = frame.shock ? Math.max(1.1, frame.shock[0] + 0.12) : 1.1;
      for (let y = 0; y < rows; y++) {
        const ny = ((y + 0.5) * ch - cy) / R;
        if (Math.abs(ny) > reach) continue;
        for (let x = 0; x < cols; x++) {
          const nx = ((x + 0.5) * cw - cx) / R;
          const rr = nx * nx + ny * ny;
          if (rr > reach * reach) continue;
          let [I, hot] = reactorField(nx, ny, frame, aa);
          // voice ripple: a ring pushed out from the core by the level
          if (voice > 0.05) {
            const r = Math.sqrt(rr);
            const ripple = Math.exp(-(((r - 0.42 - ((t * 0.9) % 0.5)) / 0.05) ** 2)) * smooth;
            I += ripple * 0.7;
            hot += ripple * 0.3;
          }
          I *= dim;
          if (I < 0.08) continue;
          const Ic = I < 1 ? I : 1;
          const glyph =
            hot > 0.9
              ? RAMP.length - 1
              : hot > 0.6
                ? RAMP.length - 2
                : 1 + Math.floor(Ic * (RAMP.length - 3.01));
          atlas!.draw(ctx, glyph, Math.min(1, Ic * 0.85 + hot * 0.5 * dim), x * cw, y * ch);
        }
      }
    };

    const sizes = new ResizeObserver(resize);
    sizes.observe(canvas);
    resize();
    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      sizes.disconnect();
      canvas.width = canvas.height = 1;
    };
  }, [level, live]);

  return <canvas ref={ref} className={className} aria-hidden="true" />;
});

/** ASCII voice band: a mirrored glyph oscilloscope. The newest sample sits
 *  at the centre and history spreads outward to both edges, so speech
 *  radiates from the reactor's axis. Flat `·─·` line when nobody talks. */
export const CallWave = memo(function CallWave({
  mood,
  color,
  level,
  className,
}: {
  mood: ReactorMood;
  color: string;
  level: MutableRefObject<number>;
  className?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const live = useLive(mood, color);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    let w = 0;
    let h = 0;
    let dpr = 1;
    const cw = 9;
    const ch = 14;
    let atlas: GlyphAtlas | null = null;
    let hist = new Float32Array(1);
    let acc = 0;
    let last = performance.now();
    let smooth = 0;
    let raf = 0;
    let tick = 0;
    const STEP = 1000 / 30;

    const resize = () => {
      ({ w, h, dpr } = fitCanvas(canvas, ctx));
      const half = Math.ceil(w / cw / 2) + 1;
      const next = new Float32Array(half);
      next.set(hist.subarray(0, Math.min(half, hist.length)));
      hist = next;
      live.current.rebuild = true;
    };

    const draw = (now: number) => {
      raf = requestAnimationFrame(draw);
      const L = live.current;
      if (!w) return;
      if (L.rebuild || !atlas) {
        atlas = glyphAtlas(cw, ch, dpr, L.color);
        L.rebuild = false;
      }
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      smooth = follow(smooth, L.mood === 'speaking' || L.mood === 'alert' ? level.current : 0, dt);
      acc += dt * 1000;
      if (acc < STEP) return;
      acc = 0;
      tick++;
      // shift outward, newest at index 0 (the centre column)
      hist.copyWithin(1, 0, hist.length - 1);
      const flutter = 0.75 + 0.5 * hash(tick, 3);
      hist[0] = smooth * flutter;
      ctx.clearRect(0, 0, w, h);
      const cols = Math.floor(w / cw);
      const rows = Math.floor(h / ch);
      const mid = Math.floor(rows / 2);
      const cxCol = Math.floor(cols / 2);
      const dim = DIM[L.mood];
      for (let x = 0; x < cols; x++) {
        const k = Math.abs(x - cxCol);
        const fall = 1 - k / (cxCol + 1);
        const v = (hist[k] ?? 0) * (0.35 + 0.65 * fall);
        const amp = Math.round(v * mid);
        // baseline: dotted rule, brighter toward the centre
        if (amp === 0) {
          const g = k % 4 === 0 ? 5 : 2; // '=' : '·'
          atlas.draw(ctx, g, (0.18 + 0.3 * fall) * dim, x * cw, mid * ch);
          continue;
        }
        for (let dy = -amp; dy <= amp; dy++) {
          const e = 1 - Math.abs(dy) / (amp + 1);
          const tip = Math.abs(dy) === amp;
          const glyph = tip
            ? 2 + Math.floor(hash(x, tick + dy) * 3)
            : e > 0.75
              ? RAMP.length - 2
              : 3 + Math.floor(e * 6);
          atlas.draw(
            ctx,
            glyph,
            Math.min(1, (0.3 + e * 0.75) * (0.5 + fall * 0.5)) * dim,
            x * cw,
            (mid + dy) * ch
          );
        }
      }
    };

    const sizes = new ResizeObserver(resize);
    sizes.observe(canvas);
    resize();
    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      sizes.disconnect();
      canvas.width = canvas.height = 1;
    };
  }, [level, live]);

  return <canvas ref={ref} className={className} aria-hidden="true" />;
});
