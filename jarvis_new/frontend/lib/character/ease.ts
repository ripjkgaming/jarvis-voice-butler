import type { Motion } from './types';

type Bez = [number, number, number, number];
export type Ease = (t: number) => number;

/** Cubic-bezier solver (Newton with bisection fallback). */
export function bezier([x1, y1, x2, y2]: Bez): Ease {
  const cx = 3 * x1;
  const bx = 3 * (x2 - x1) - cx;
  const ax = 1 - cx - bx;
  const cy = 3 * y1;
  const by = 3 * (y2 - y1) - cy;
  const ay = 1 - cy - by;
  const sx = (t: number) => ((ax * t + bx) * t + cx) * t;
  const sy = (t: number) => ((ay * t + by) * t + cy) * t;
  const dx = (t: number) => (3 * ax * t + 2 * bx) * t + cx;
  return (x) => {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    let t = x;
    for (let i = 0; i < 6; i++) {
      const e = sx(t) - x;
      if (Math.abs(e) < 1e-4) return sy(t);
      const d = dx(t);
      if (Math.abs(d) < 1e-6) break;
      t -= e / d;
    }
    let lo = 0;
    let hi = 1;
    t = x;
    for (let i = 0; i < 24; i++) {
      const e = sx(t);
      if (Math.abs(e - x) < 1e-4) break;
      if (e < x) lo = t;
      else hi = t;
      t = (lo + hi) / 2;
    }
    return sy(t);
  };
}

export function easings(m: Motion): Record<string, Ease> {
  const out: Record<string, Ease> = { linear: (t) => Math.min(1, Math.max(0, t)) };
  for (const [k, v] of Object.entries(m.easing)) out[k] = bezier(v);
  return out;
}

export const clamp01 = (t: number) => (t < 0 ? 0 : t > 1 ? 1 : t);
export const smooth = (a: number, b: number, t: number) => {
  const u = clamp01((t - a) / (b - a || 1));
  return u * u * (3 - 2 * u);
};
