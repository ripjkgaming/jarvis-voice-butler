'use client';

import { type RefObject, useEffect, useRef, useState } from 'react';

/**
 * Baked-frame animation kit: every animated canvas in the HUD pre-renders
 * ("bakes") its full loop once into offscreen frame canvases, then plays
 * back by blitting (`drawImage`) — no per-frame particle math, gradients,
 * or shadow passes at runtime. Reduced motion shows frame 0 only; the
 * loop parks when the tab hides or the canvas scrolls off-view.
 */

export function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

/** Deterministic RNG so a bake is identical on every re-bake. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Deterministic [0,1) hash of small ints — frame-stable per-particle seeds. */
export function hash01(...nums: number[]): number {
  let h = 2166136261;
  for (const n of nums) {
    h ^= Math.floor(n) & 0xffff;
    h = Math.imul(h, 16777619);
    h ^= h >>> 13;
  }
  h = Math.imul(h ^ (h >>> 16), 0x45d9f3b);
  h = Math.imul(h ^ (h >>> 16), 0x45d9f3b);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

export type BakedFilm = {
  frames: HTMLCanvasElement[];
  width: number;
  height: number;
};

/**
 * Render a full loop once. `render(ctx, w, h, i, n)` draws frame `i` of
 * `n`. Returns the stitched frame strip (array of canvases).
 */
export function bakeFilm(
  count: number,
  width: number,
  height: number,
  render: (ctx: CanvasRenderingContext2D, w: number, h: number, i: number, n: number) => void
): BakedFilm {
  const frames: HTMLCanvasElement[] = [];
  const w = Math.max(1, Math.floor(width));
  const h = Math.max(1, Math.floor(height));
  for (let i = 0; i < count; i += 1) {
    const c = document.createElement('canvas');
    c.width = w;
    c.height = h;
    const ctx = c.getContext('2d');
    if (ctx) {
      ctx.clearRect(0, 0, w, h);
      render(ctx, w, h, i, count);
    }
    frames.push(c);
  }
  return { frames, width: w, height: h };
}

/**
 * Blit-playback for a baked film. Only work per rAF tick is one
 * `drawImage` (+ a canvas resize copy when the film size mismatches).
 */
export function useBakedPlayback(film: BakedFilm | null, fps: number, paused = false) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || !film || film.frames.length === 0) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    if (canvas.width !== film.width || canvas.height !== film.height) {
      canvas.width = film.width;
      canvas.height = film.height;
    }
    // Parked (HUD idle): hold frame 0, zero wakeups. Resumes on unpause.
    if (paused || film.frames.length === 1) {
      const f = film.frames[0];
      if (f) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(f, 0, 0);
      }
      return;
    }
    let timer: ReturnType<typeof setTimeout> | null = null;
    let inView = true;
    const root = document.documentElement;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const visible = () =>
      inView &&
      !document.hidden &&
      !root.classList.contains('hud-hidden') &&
      !root.classList.contains('school-tx') &&
      !(root.classList.contains('suit-open') && canvas.closest('.hud, .sbar-root'));
    const t0 = performance.now();
    const paint = (frameIdx: number) => {
      const f = film.frames[frameIdx % film.frames.length];
      if (f) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(f, 0, 0);
      }
    };
    // Timer cadence matches the film's 12–24 FPS instead of waking this
    // callback on every 60–144 Hz display refresh.
    let last = -1;
    const loop = () => {
      if (!visible() || reduced.matches) {
        timer = null;
        return;
      }
      const now = performance.now();
      const idx = Math.floor(((now - t0) / 1000) * fps) % film.frames.length;
      if (idx !== last) {
        paint(idx);
        last = idx;
      }
      // These films advance at 12–24 FPS. A permanent rAF loop woke 60–144
      // times per second just to rediscover that the frame had not changed.
      timer = setTimeout(loop, Math.max(8, 1000 / Math.max(1, fps)));
    };
    const kick = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      if (!visible()) return;
      if (reduced.matches) {
        if (last !== 0) paint(0);
        last = 0;
      } else {
        timer = setTimeout(loop, 0);
      }
    };
    // Native hiding and modal coverage do not necessarily change document.hidden.
    // Observe existing ownership flags instead of spending ticks behind them.
    const classes = new MutationObserver(kick);
    classes.observe(root, { attributes: true, attributeFilter: ['class'] });
    reduced.addEventListener('change', kick);
    const onVisibility = () => kick();
    document.addEventListener('visibilitychange', onVisibility);
    const observer =
      typeof IntersectionObserver !== 'undefined'
        ? new IntersectionObserver((entries) => {
            inView = entries[0]?.isIntersecting !== false;
            kick();
          })
        : null;
    observer?.observe(canvas);
    if (reduced.matches) kick();
    else loop();
    return () => {
      if (timer) clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
      observer?.disconnect();
      classes.disconnect();
      reduced.removeEventListener('change', kick);
    };
  }, [film, fps, paused]);

  return ref;
}

export type BakeRender = (
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  i: number,
  n: number,
  rng: () => number
) => void;

export type UseBakedCanvasOpts = {
  /** Frames in the baked loop. */
  frames: number;
  /** Playback rate. */
  fps: number;
  /** Park on frame 0 with zero wakeups (HUD idle). */
  paused?: boolean;
  /** Bake resolution scale vs the CSS box (glow art upscales cleanly). */
  scale?: number;
  /** Re-bake when this key changes (e.g. state color). */
  bakeKey?: string;
  /** Seed for the deterministic bake RNG. */
  seed?: number;
  /** Draws frame `i` of `n` (must be pure for a given rng stream). */
  render: BakeRender;
  /** Size from the layout box (clientWidth/Height), ignoring CSS transforms.
   *  Needed for canvases that spin or scale via CSS animations. */
  layoutBox?: boolean;
  /** Keep finished bakes in a shared LRU under this id, so flipping back
   *  to a seen state/size blits instantly instead of re-baking. */
  cacheId?: string;
  /** Yield between frames on expensive cold bakes; keep playback visible. */
  incremental?: boolean;
  /** Spread independent decorative layer bakes across the handoff. */
  bakeDelay?: number;
};

/** Shared bake cache, evicted oldest-first past a byte budget. */
const FILM_CACHE = new Map<string, BakedFilm>();
const FILM_CACHE_BUDGET = 64 * 1024 * 1024;

function filmBytes(f: BakedFilm): number {
  return f.width * f.height * 4 * f.frames.length;
}

function cacheFilm(key: string, film: BakedFilm): BakedFilm {
  FILM_CACHE.set(key, film);
  let total = 0;
  for (const f of FILM_CACHE.values()) total += filmBytes(f);
  for (const [k, f] of FILM_CACHE) {
    if (total <= FILM_CACHE_BUDGET || k === key) break;
    FILM_CACHE.delete(k);
    total -= filmBytes(f);
  }
  return film;
}

function cachedFilm(key: string, make: () => BakedFilm): BakedFilm {
  const hit = FILM_CACHE.get(key);
  if (hit) {
    FILM_CACHE.delete(key);
    FILM_CACHE.set(key, hit);
    return hit;
  }
  return cacheFilm(key, make());
}

/**
 * All-in-one baked canvas: measures its box (re-measuring when the
 * hidden overlay is summoned), bakes the loop once per size/bakeKey,
 * then blit-plays it. Returns the canvas ref to spread onto `<canvas>`.
 */
export function useBakedCanvas({
  frames,
  fps,
  scale = 0.5,
  bakeKey = '',
  seed = 1,
  render,
  paused = false,
  layoutBox = false,
  cacheId,
  incremental = false,
  bakeDelay = 0,
}: UseBakedCanvasOpts): RefObject<HTMLCanvasElement | null> {
  const renderRef = useRef(render);
  renderRef.current = render;
  const [film, setFilm] = useState<BakedFilm | null>(null);
  const [box, setBox] = useState<{ w: number; h: number } | null>(null);
  const canvasRef = useBakedPlayback(film, fps, paused);

  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const measure = () => {
      // Measure the canvas CSS box itself (parents may be taller).
      const r = el.getBoundingClientRect();
      const w = Math.max(1, Math.floor(layoutBox ? el.clientWidth : r.width));
      const h = Math.max(1, Math.floor(layoutBox ? el.clientHeight : r.height));
      setBox((prev) => (prev && prev.w === w && prev.h === h ? prev : { w, h }));
    };
    measure();
    let t: ReturnType<typeof setTimeout> | null = null;
    const onResize = () => {
      if (t) clearTimeout(t);
      t = setTimeout(measure, 300);
    };
    // The overlay starts hidden (zero-size box): re-measure on summon.
    const onShown = () => measure();
    const onVisibility = () => {
      if (!document.hidden) measure();
    };
    window.addEventListener('resize', onResize);
    window.addEventListener('focus', onShown);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.removeEventListener('resize', onResize);
      window.removeEventListener('focus', onShown);
      document.removeEventListener('visibilitychange', onVisibility);
      if (t) clearTimeout(t);
    };
    // Parent element identity is stable; measure via listeners only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!box) return;
    const W = Math.max(1, Math.floor(box.w * scale));
    const H = Math.max(1, Math.floor(box.h * scale));
    // Capture the renderer for this key: a later state must not recolor an
    // unfinished film before its effect cleanup cancels the next slice.
    const draw = renderRef.current;
    const key = cacheId ? `${cacheId}|${W}x${H}|${frames}|${seed}|${bakeKey}` : null;
    const bake = () => {
      const rng = mulberry32(seed);
      return bakeFilm(frames, W, H, (ctx, w, h, i, n) => draw(ctx, w, h, i, n, rng));
    };
    if (!incremental || (key && FILM_CACHE.has(key))) {
      setFilm(key ? cachedFilm(key, bake) : bake());
      return;
    }
    const rng = mulberry32(seed);
    const next: BakedFilm = { frames: [], width: W, height: H };
    let timer: ReturnType<typeof setTimeout>;
    const slice = () => {
      const i = next.frames.length;
      const frame = bakeFilm(1, W, H, (ctx, w, h) => draw(ctx, w, h, i, frames, rng));
      next.frames.push(frame.frames[0]);
      // A matching-color still is available after one frame. Subsequent
      // frames stay private until the entire loop can be played/cached.
      if (i === 0) setFilm({ ...next, frames: [...next.frames] });
      if (next.frames.length < frames) timer = setTimeout(slice, 16);
      else setFilm(key ? cacheFilm(key, next) : next);
    };
    timer = setTimeout(slice, bakeDelay);
    return () => clearTimeout(timer);
    // Re-bake on size / bakeKey / seed / frame-count changes only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [box, bakeKey, seed, frames, scale, cacheId, incremental, bakeDelay]);

  return canvasRef;
}
