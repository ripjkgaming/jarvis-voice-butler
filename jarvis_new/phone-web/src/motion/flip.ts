/** FLIP geometry (PLAN.md §4.2–4.3). A Pose is an element's untransformed
 * layout box (centre + size) plus its resting rotation. A T maps one pose
 * onto another; transforms are written as translate · rotate · scale about
 * the element centre, so lerping T component-wise moves, turns and resizes
 * the element along one continuous path. Layout is read only in measure(). */

export type Pose = { cx: number; cy: number; w: number; h: number; rot: number };
export type T = { tx: number; ty: number; sx: number; sy: number; r: number };
export const IDENTITY: T = { tx: 0, ty: 0, sx: 1, sy: 1, r: 0 };

export const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);
export const lerp = (a: number, b: number, p: number) => a + (b - a) * p;
export function sstep(a: number, b: number, x: number) {
  const t = clamp01((x - a) / (b - a));
  return t * t * (3 - 2 * t);
}

/** Signed shortest angle from a to b, degrees in (−180, 180]. */
export function shortest(a: number, b: number) {
  let d = (b - a) % 360;
  if (d > 180) d -= 360;
  if (d <= -180) d += 360;
  return d;
}

/** Δ that places an element resting at `to` onto the rect `from`. */
export function delta(from: Pose, to: Pose): T {
  return {
    tx: from.cx - to.cx,
    ty: from.cy - to.cy,
    sx: to.w ? from.w / to.w : 1,
    sy: to.h ? from.h / to.h : 1,
    r: shortest(to.rot, from.rot),
  };
}

export function invert(t: T): T {
  return { tx: -t.tx, ty: -t.ty, sx: t.sx ? 1 / t.sx : 1, sy: t.sy ? 1 / t.sy : 1, r: -t.r };
}

export function lerpT(a: T, b: T, p: number): T {
  return { tx: lerp(a.tx, b.tx, p), ty: lerp(a.ty, b.ty, p), sx: lerp(a.sx, b.sx, p), sy: lerp(a.sy, b.sy, p), r: lerp(a.r, b.r, p) };
}

const n = (v: number) => Math.round(v * 1000) / 1000;
/** CSS transform for an element whose resting rotation is `baseRot`. */
export function css(t: T, baseRot = 0) {
  return `translate(${n(t.tx)}px,${n(t.ty)}px) rotate(${n(baseRot + t.r)}deg) scale(${n(t.sx)},${n(t.sy)})`;
}

/** Read an element's untransformed pose. Only called at rest (never in rAF). */
export function measure(el: HTMLElement | SVGElement): Pose {
  const prev = el.style.transform;
  el.style.transform = 'none';
  const r = el.getBoundingClientRect();
  el.style.transform = prev;
  return { cx: r.left + r.width / 2, cy: r.top + r.height / 2, w: r.width, h: r.height, rot: Number(el.dataset.rot || 0) };
}
