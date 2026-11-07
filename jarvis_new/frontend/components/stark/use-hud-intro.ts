'use client';

import { type RefObject, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { onBridgeRecover } from '@/lib/shared-poll';
import { type IntroSfxBox, playIntroSfx } from './intro-sfx';
import styles from './intro.module.css';
import { MORPH_MS, layerWindow, orbMorph } from './orb-morph';

// Where the wallpaper's static emblem sits (the live orb is registered onto it
// by reactor.tsx and morphs out of it here). Tune in wall-emblem.ts.
export { WALL_BG_RGB, WALL_EMBLEM_CENTER, WALL_EMBLEM_RADIUS_FRAC } from './wall-emblem';

/** false = play the intro even when the OS reports prefers-reduced-motion
 *  (some Linux desktops report it whenever "animations" are switched off). */
const RESPECT_REDUCED_MOTION = false;

/** Minimum gap between two bridge-reconnect replays (also ignored while one runs). */
const REPLAY_COOLDOWN_MS = 8000;

// ---- timeline (ms) -------------------------------------------------------
const D_DESCEND = 700;
const D_SLIDE = 550;
const D_EXPAND = 340;
const D_GLITCH = 620;
const START_SCALE = 0.3;
const WAVE_MS = 1000;

// ---- orb sequence: wallpaper emblem -> live orb morph -> flare (ms) -------
// The wallpaper's static ASCII emblem is the orb's resting state (the replica
// canvas draws it pixel-identically). A tiny spark wakes at its nucleus, then
// one scalar m (orb-morph.ts) takes it from still to live over MORPH_MS: glyphs
// start moving from the static pose, the live layers cross-fade in over them.
const ORB_PAUSE = 250; // after the last box, before the spark appears (boot)
const WAKE_ORB_AT = 150; // a wake starts the orb first, boxes follow
const WAKE_BOX_AT = 450; // ...boxes begin this long after a wake starts
const WAKE_STAGGER = 45; // ...with a tighter stagger (per-box timing unchanged)
const DOT_MS = 700; // spark at the nucleus before the morph begins
const MORPH_SCALE = 0.12; // live layers scale in from this fraction of the old unfold
const MORPH_SCALE_OUTER = 0.35; // callouts / scale ring bloom outward a little more
const MORPH_SPIN = 0.3; // fraction of the old spin-in angle (rings spin up)
const IGNITE_AT = 0.2; // core ignition (bloom pulse + shock ring) at this share of the morph
const FLARE_AT = 0.7; // settle flare at this share of the morph
const EASE_SPIN = 'cubic-bezier(0.25, 0.6, 0.3, 1)'; // no overshoot: nothing pops

// ---- background spawn ------------------------------------------------------
const GRID_AT = 150; // hairline grid / vignette cross-fades in...
const GRID_MS = 1800; // ...over this long
const RIPPLE1_MS = 1100; // glyph field spawn when the orb ignites
const RIPPLE2_MS = 1300; // second, deeper ramp at the settle flare

/** Per-layer start scale, spin-in angle (deg), and sub-element stagger (ms). */
const L_SCALE: Record<number, number> = {
  0: 0.2,
  1: 0.45,
  2: 0.5,
  3: 0.35,
  4: 0.6,
  5: 0.7,
  6: 1.18,
  7: 0.9,
};
const L_ROT: Record<number, number> = { 1: -90, 3: -14, 4: -240, 5: 25, 6: -20 };
const L_SUB: Record<number, number> = { 2: 40, 3: 32, 7: 90 };

/**
 * 'pre'   server render / before the layout effect: targets hidden by CSS
 *         (with a CSS failsafe that reveals them if JS never starts).
 * 'run'   boxes are choreographed by WAAPI (elements tagged data-ix).
 * 'late'  all boxes done, the orb is on its way.
 * 'done'  finished / cancelled: no intro state left on the page.
 * 'off'   reduced motion: final state immediately.
 */
export type HudIntroPhase = 'pre' | 'run' | 'late' | 'done' | 'off';

/** 'boot' = page load / bridge recovery (orb after the boxes). 'wake' = Jarvis
 *  called after fading into the wallpaper: same per-box choreography and
 *  timings, started with a tighter stagger, the orb wakes first. */
export type IntroMode = 'boot' | 'wake';

/** Presence mode asks for a replay with this window event (detail: IntroMode). */
export const INTRO_REPLAY_EVENT = 'hud-intro-replay';

const useIsoLayoutEffect = typeof window !== 'undefined' ? useLayoutEffect : useEffect;

type Session = { cancel: () => void };

type Item = {
  el: HTMLElement;
  w: number;
  h: number;
  cx: number;
  cy: number;
  group: number;
  idx: number;
  ghost: HTMLElement | null;
  scan: HTMLElement | null;
};

const EASE_DROP = 'cubic-bezier(0.3, 0.05, 0.2, 1)';
const EASE_SLIDE = 'cubic-bezier(0.55, 0, 0.2, 1)';
const EASE_EXPAND = 'cubic-bezier(0.2, 0.9, 0.3, 1.25)';
const EASE_DOT = 'cubic-bezier(0.22, 0.55, 0.28, 1)';
const EASE_WAVE = 'cubic-bezier(0.1, 0.7, 0.3, 1)';
const SCRAMBLE = '█▓▒░<>/\\|#@%$&01ABCDEF';

/**
 * Whole-HUD load-in. Plays on mount (first load, window reload, school-mode
 * return) and again whenever the bridge comes back after being unreachable.
 * Purely visual + sound: it never gates data polling or voice input.
 */
export function useHudIntro(rootRef: RefObject<HTMLElement | null>): HudIntroPhase {
  const [phase, setPhase] = useState<HudIntroPhase>('pre');
  const lastPlay = useRef(0);

  useIsoLayoutEffect(() => {
    const root = rootRef.current;
    if (!root) {
      setPhase('done');
      return;
    }
    let session: Session | null = null;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
    const stop = () => {
      session?.cancel();
      session = null;
    };
    const play = (force: boolean, mode: IntroMode = 'boot') => {
      if (RESPECT_REDUCED_MOTION && reduce.matches) {
        stop();
        setPhase('off');
        return;
      }
      const now = Date.now();
      if (!force && (session || now - lastPlay.current < REPLAY_COOLDOWN_MS)) return;
      lastPlay.current = now;
      stop();
      try {
        // pre-set so a replay never paints one frame of the finished HUD
        root.setAttribute('data-intro', 'run');
        const s = runIntro(
          root,
          {
            onLate: () => setPhase('late'),
            onDone: () => {
              session = null;
              setPhase('done');
            },
          },
          mode
        );
        if (s) {
          session = s;
          setPhase('run');
        } else {
          root.setAttribute('data-intro', 'done');
          setPhase('done');
        }
      } catch {
        stop();
        root.setAttribute('data-intro', 'done');
        setPhase('done');
      }
    };
    play(true);
    const off = onBridgeRecover(() => play(false));
    const onReplay = (e: Event) => {
      const d = (e as CustomEvent<IntroMode | undefined>).detail;
      play(true, d === 'wake' ? 'wake' : 'boot');
    };
    window.addEventListener(INTRO_REPLAY_EVENT, onReplay);
    return () => {
      window.removeEventListener(INTRO_REPLAY_EVENT, onReplay);
      off();
      stop();
    };
  }, [rootRef]);

  return phase;
}

// ---------------------------------------------------------------------------

function collectItems(root: HTMLElement): Item[] {
  const items: Item[] = [];
  root.querySelectorAll<HTMLElement>('[data-ix-zone]').forEach((zone) => {
    const group = zone.tagName === 'HEADER' ? 0 : zone.tagName === 'FOOTER' ? 2 : 1;
    Array.from(zone.children).forEach((c, idx) => {
      if (!(c instanceof HTMLElement)) return;
      if (c.hasAttribute('data-ix-zone') || c.hasAttribute('data-ix-orb')) return;
      items.push({ el: c, w: 0, h: 0, cx: 0, cy: 0, group, idx, ghost: null, scan: null });
    });
  });
  return items;
}

/** Settle finite CSS entrance animations (e.g. the panels' `rise`) so the
 *  measured rect is the final one. */
function settle(el: HTMLElement): void {
  try {
    el.getAnimations().forEach((a) => {
      const t = a.effect?.getComputedTiming();
      if (t && Number.isFinite(t.endTime as number)) a.finish();
    });
  } catch {
    /* ignore */
  }
}

function runIntro(
  root: HTMLElement,
  cb: { onLate: () => void; onDone: () => void },
  mode: IntroMode = 'boot'
): Session | null {
  if (typeof root.animate !== 'function') return null;
  const vw = window.innerWidth;

  // 1. gather + measure (reads only, one layout).
  const all = collectItems(root);
  all.forEach((it) => settle(it.el));
  const items = all.filter((it) => {
    const r = it.el.getBoundingClientRect();
    it.w = r.width;
    it.h = r.height;
    it.cx = r.left + r.width / 2;
    it.cy = r.top + r.height / 2;
    return r.width >= 6 && r.height >= 6;
  });
  const orbBody = root.querySelector<HTMLElement>(`.${styles.orbBody}`);
  if (items.length === 0 && !orbBody) return null;
  const vh = window.innerHeight;
  const orbRect = orbBody?.getBoundingClientRect();
  const cx = orbRect ? orbRect.left + orbRect.width / 2 : vw / 2;
  const cy = orbRect ? orbRect.top + orbRect.height / 2 : vh / 2;
  const grid = root.querySelector<HTMLElement>('[data-ix-bg="grid"]');
  const field = root.querySelector<HTMLCanvasElement>('canvas[data-ambient-particles="hud"]');
  const fieldRect = field?.getBoundingClientRect();

  items.sort((a, b) => a.group - b.group || a.idx - b.idx || a.cx - b.cx);
  const stagger =
    mode === 'wake' ? WAKE_STAGGER : Math.max(50, Math.min(110, 1500 / Math.max(1, items.length)));

  const anims: Animation[] = [];
  const timers: ReturnType<typeof setTimeout>[] = [];
  const intervals: ReturnType<typeof setInterval>[] = [];
  const restores: (() => void)[] = [];
  let ended = false;

  const cleanup = () => {
    ended = true;
    timers.forEach(clearTimeout);
    intervals.forEach(clearInterval);
    anims.forEach((a) => {
      try {
        a.cancel();
      } catch {
        /* ignore */
      }
    });
    restores.forEach((r) => {
      try {
        r();
      } catch {
        /* ignore */
      }
    });
    timers.length = intervals.length = anims.length = restores.length = 0;
  };
  const track = (a: Animation | null): Animation | null => {
    if (a) anims.push(a);
    return a;
  };

  let stopSfx: () => void = () => undefined;
  try {
    // 2. prepare writes: tag, ghost, positioning.
    items.forEach((it) => {
      const el = it.el;
      el.setAttribute('data-ix', 'empty');
      if (getComputedStyle(el).position === 'static') {
        el.style.position = 'relative';
        restores.push(() => (el.style.position = ''));
      }
      const ghost = document.createElement('i');
      ghost.className = styles.ghost;
      ghost.setAttribute('aria-hidden', 'true');
      const scan = document.createElement('i');
      scan.className = styles.ghostScan;
      ghost.appendChild(scan);
      el.appendChild(ghost);
      it.ghost = ghost;
      it.scan = scan;
      restores.push(() => {
        ghost.remove();
        el.removeAttribute('data-ix');
      });
    });

    // 3. per-box timeline.
    const T = D_DESCEND + D_SLIDE + D_EXPAND;
    const o1 = D_DESCEND / T;
    const o2 = (D_DESCEND + D_SLIDE) / T;
    const sfxBoxes: IntroSfxBox[] = [];
    let boxesEnd = 0;

    items.forEach((it, i) => {
      const delay = i * stagger + (mode === 'wake' ? WAKE_BOX_AT : 0);
      const s0 = START_SCALE;
      const dx0 = vw / 2 - it.cx;
      const y0 = -(it.h * s0) / 2 - 28;
      const dy0 = y0 - it.cy;
      const A = `translate(${dx0}px, ${dy0}px) scale(${s0})`;
      const B = `translate(${dx0}px, 0px) scale(${s0})`;
      const C = `translate(0px, 0px) scale(${s0})`;
      const D = 'translate(0px, 0px) scale(1)';
      const box = track(
        it.el.animate(
          [
            { offset: 0, opacity: 0, transform: A, easing: 'linear' },
            { offset: 0.02, opacity: 1, transform: A, easing: EASE_DROP },
            { offset: o1, opacity: 1, transform: B, easing: EASE_SLIDE },
            { offset: o2, opacity: 1, transform: C, easing: EASE_EXPAND },
            { offset: 1, opacity: 1, transform: D },
          ],
          { duration: T, delay, fill: 'both' }
        )
      );
      track(
        it.ghost?.animate(
          [
            { offset: 0, borderWidth: '3px' },
            { offset: o2, borderWidth: '3px' },
            { offset: 1, borderWidth: '1px' },
          ],
          { duration: T, delay, fill: 'both' }
        ) ?? null
      );
      const end = delay + T + D_GLITCH;
      boxesEnd = Math.max(boxesEnd, end);
      sfxBoxes.push({
        at: delay,
        d1: D_DESCEND,
        d2: D_SLIDE,
        d3: D_EXPAND,
        glitch: D_GLITCH,
        dir: Math.abs(dx0) < 24 ? 0 : dx0 > 0 ? -1 : 1,
      });
      if (box) box.onfinish = () => glitchIn(it);
    });

    // 4. text-scramble + content glitch for one box.
    const glitchIn = (it: Item) => {
      if (ended) return;
      const el = it.el;
      const kids = Array.from(el.children).filter(
        (c): c is HTMLElement => c instanceof HTMLElement && c !== it.ghost
      );
      const targets = kids.length > 0 ? kids : [el];
      targets.forEach((t) => {
        const r = () => Math.random();
        const a = Math.round(r() * 40);
        const b = Math.round(r() * 45);
        const dx = 3 + r() * 5;
        const split = (n: number) =>
          `blur(${(Math.abs(n) * 0.45).toFixed(2)}px) drop-shadow(${n}px 0 0 rgba(255, 40, 90, 0.8)) drop-shadow(${-n}px 0 0 rgba(0, 235, 255, 0.8))`;
        track(
          t.animate(
            [
              {
                offset: 0,
                opacity: 0,
                clipPath: 'inset(0 0 100% 0)',
                translate: `${-dx}px 0`,
                filter: split(4),
              },
              {
                offset: 0.1,
                opacity: 1,
                clipPath: `inset(${a}% 0 ${60 - a / 2}% 0)`,
                translate: `${dx}px 0`,
                filter: split(3),
              },
              {
                offset: 0.22,
                opacity: 0.15,
                clipPath: `inset(${b}% 0 ${10}% 0)`,
                translate: `${-dx * 0.8}px 0`,
                filter: split(-4),
              },
              {
                offset: 0.34,
                opacity: 1,
                clipPath: `inset(0 0 ${a}% 0)`,
                translate: `${dx * 0.6}px 0`,
                filter: split(3),
              },
              {
                offset: 0.46,
                opacity: 0.35,
                clipPath: `inset(${30 - a / 3}% 0 ${28}% 0)`,
                translate: `${-dx}px 0`,
                filter: split(-3),
              },
              {
                offset: 0.6,
                opacity: 1,
                clipPath: 'inset(0 0 0 0)',
                translate: `${dx * 0.4}px 0`,
                filter: split(2),
              },
              {
                offset: 0.72,
                opacity: 0.6,
                clipPath: `inset(${b / 3}% 0 0 0)`,
                translate: `${-dx * 0.3}px 0`,
                filter: split(-2),
              },
              {
                offset: 0.84,
                opacity: 1,
                clipPath: 'inset(0 0 0 0)',
                translate: '0 0',
                filter: split(1),
              },
              {
                offset: 1,
                opacity: 1,
                clipPath: 'inset(0 0 0 0)',
                translate: '0 0',
                filter: 'none',
              },
            ],
            { duration: D_GLITCH, fill: 'backwards', easing: 'linear' }
          )
        );
      });
      track(
        it.ghost?.animate([{ opacity: 1 }, { opacity: 1, offset: 0.25 }, { opacity: 0 }], {
          duration: D_GLITCH * 0.9,
          fill: 'forwards',
        }) ?? null
      );
      track(
        it.scan?.animate(
          [
            { opacity: 1, transform: 'translateY(0px)' },
            { opacity: 1, transform: `translateY(${it.h}px)`, offset: 0.9 },
            { opacity: 0, transform: `translateY(${it.h}px)` },
          ],
          { duration: D_GLITCH * 0.85, easing: 'steps(14)', fill: 'forwards' }
        ) ?? null
      );
      el.removeAttribute('data-ix');
      scramble(el, D_GLITCH * 0.7, intervals, restores);
    };

    // 5. the orb (last on boot, first on a wake): the wallpaper's static ASCII
    //    emblem (the replica canvas, pixel-identical) comes alive in place. One
    //    scalar (orb-morph.ts) drives it: the emblem glyphs spin up from the
    //    static pose and hand over, core first, to the live layers that
    //    cross-fade in over it with generous overlap. Nothing moves or pops.
    const orbAt = mode === 'wake' ? WAKE_ORB_AT : boxesEnd + ORB_PAUSE;
    const land = orbAt + DOT_MS; // the morph starts when the spark has landed
    const layerStart = land;
    const flareAt = land + MORPH_MS * FLARE_AT;
    const layerPlan: { at: number; idx: number }[] = [];
    const mk = (cls: string, parent: HTMLElement = root) => {
      const e = document.createElement('i');
      e.className = cls;
      e.setAttribute('aria-hidden', 'true');
      parent.appendChild(e);
      restores.push(() => e.remove());
      return e;
    };
    /** [{opacity: 0}, {opacity: <natural>}]: a lone keyframe would animate
     *  natural -> 0 (it is the END keyframe), the opposite of a reveal. */
    const reveal = (el: Element): Keyframe[] => [
      { opacity: 0 },
      { opacity: getComputedStyle(el).opacity },
    ];
    orbMorph.setScope(root);
    orbMorph.reset();
    restores.push(() => orbMorph.setScope(root));
    // the replica canvas fades in over the wallpaper (identical pixels: invisible)
    root.querySelectorAll(`[data-ix-replica]`).forEach((el) => {
      track(
        (el as HTMLElement).animate(reveal(el), {
          duration: 900,
          easing: 'ease-in-out',
          fill: 'backwards',
        })
      );
    });
    if (orbBody) {
      // 5a. the spark: a tiny point of light at the emblem's nucleus.
      const at = `translate3d(${cx}px, ${cy}px, 0)`;
      const dot = mk(styles.dot);
      const dglow = mk(styles.dotGlow, dot);
      mk(styles.dotCore, dot);
      track(
        dot.animate(
          [
            { offset: 0, opacity: 0, transform: at },
            { offset: 0.6, opacity: 1, transform: at },
            { offset: 1, opacity: 1, transform: at },
          ],
          { duration: DOT_MS, delay: orbAt, easing: EASE_DOT, fill: 'both' }
        )
      );
      track(
        dglow.animate(
          [
            { opacity: 0.2, transform: 'scale(0.4)' },
            { opacity: 1, transform: 'scale(1.1)' },
          ],
          { duration: DOT_MS, delay: orbAt, easing: 'ease-in', fill: 'both' }
        )
      );
      track(
        dglow.animate(
          [
            { opacity: 1, transform: 'scale(1.1)' },
            { opacity: 0.8, transform: 'scale(2.4)', offset: 0.3 },
            { opacity: 0, transform: 'scale(3.2)' },
          ],
          { duration: 700, delay: land, easing: 'ease-out', fill: 'forwards' }
        )
      );
      track(
        dot.animate([{ opacity: 1 }, { opacity: 0 }], {
          duration: 700,
          delay: land,
          easing: 'ease-in-out',
          fill: 'forwards',
        })
      );

      // The morph itself: m 0 -> 1, energy and glyph hand-over derive from it.
      const stopMorph = orbMorph.to(1, MORPH_MS, land);
      restores.push(stopMorph);
      orbMorph.ignite(land + MORPH_MS * IGNITE_AT);

      // 5b. the live layers cross-fade in over the emblem.
      const byLayer = new Map<number, Element[]>();
      root.querySelectorAll('[data-ix-layer]').forEach((el) => {
        const n = Number(el.getAttribute('data-ix-layer'));
        if (!Number.isFinite(n)) return;
        const list = byLayer.get(n) ?? [];
        list.push(el);
        byLayer.set(n, list);
      });
      byLayer.forEach((els, idx) => {
        const [wa, wb] = layerWindow(idx);
        const base = land + wa * MORPH_MS;
        const span = (wb - wa) * MORPH_MS;
        layerPlan.push({ at: base, idx });
        const full = L_SCALE[idx] ?? 0.6;
        const s0 = 1 + (full - 1) * (idx >= 6 ? MORPH_SCALE_OUTER : MORPH_SCALE);
        const rot = (L_ROT[idx] ?? 0) * MORPH_SPIN;
        els.forEach((el, j) => {
          const delay = base + j * (L_SUB[idx] ?? 0);
          const spin = el.hasAttribute('data-ix-spin');
          const dur = spin ? span * 1.5 : span;
          // cross-fade from the emblem (no pop: same place, scale, colour)
          track(
            el.animate(reveal(el), {
              duration: span,
              delay,
              easing: 'ease-in-out',
              fill: 'backwards',
            })
          );
          track(
            el.animate(
              [
                { scale: String(s0), rotate: `${rot}deg` },
                { scale: '1', rotate: '0deg' },
              ],
              { duration: dur, delay, easing: EASE_SPIN, fill: 'backwards' }
            )
          );
          el.querySelectorAll('[data-ix-draw]').forEach((ring) =>
            track(
              ring.animate([{ strokeDasharray: '0 1' }, { strokeDasharray: '1 1' }], {
                duration: span * 1.1,
                delay,
                easing: 'cubic-bezier(0.3, 0.6, 0.2, 1)',
                fill: 'backwards',
              })
            )
          );
        });
      });
      if (root.querySelector('[data-ascii-reactor]')) {
        timers.push(
          setTimeout(() => {
            if (!ended) window.dispatchEvent(new Event('hud-reactor-rebuild'));
          }, land)
        );
      }

      // 5c. glow: a soft bloom pulse as the nucleus ignites, then the big
      //     settle flare when the live orb has taken over.
      const glow = root.querySelector<HTMLElement>(`.${styles.orbGlow}`);
      if (glow) {
        track(
          glow.animate(
            [
              { opacity: 0, transform: 'scale(0.15)' },
              { opacity: 0.5, transform: 'scale(0.5)', offset: 0.4 },
              { opacity: 0, transform: 'scale(0.9)' },
            ],
            { duration: 1500, delay: land + MORPH_MS * IGNITE_AT, easing: 'ease-in-out' }
          )
        );
        track(
          glow.animate(
            [
              { opacity: 0, transform: 'scale(0.8)' },
              { opacity: 0.8, transform: 'scale(1.12)', offset: 0.3 },
              { opacity: 0, transform: 'scale(1)' },
            ],
            { duration: 1500, delay: flareAt, easing: 'ease-in-out' }
          )
        );
      }
      const waves = Array.from(root.querySelectorAll<HTMLElement>(`.${styles.orbWave}`));
      waves.forEach((w, k) =>
        track(
          w.animate(
            [
              { opacity: 0.85, transform: 'translate(-50%, -50%) scale(0.5)' },
              { opacity: 0, transform: 'translate(-50%, -50%) scale(3.4)' },
            ],
            { duration: WAVE_MS, delay: flareAt + k * 200, easing: EASE_WAVE, fill: 'forwards' }
          )
        )
      );
    }
    const end = Math.max(flareAt + 200 + WAVE_MS + 300, land + MORPH_MS + 400);

    // 5d. background: the wallpaper, then every background layer cross-fades in
    //     with a long eased opacity ramp (no clip edges, no pops): hairline
    //     grid / vignette first, the glyph field when the orb ignites and
    //     again, deeper, when it settles. A soft scan line sweeps once.
    let bgPlan: { scanAt: number; scanMs: number; ripples: number[] } | null = null;
    if (grid || field) {
      bgPlan = { scanAt: GRID_AT, scanMs: GRID_MS, ripples: orbBody ? [land, flareAt] : [] };
    }
    const EASE_BG = 'cubic-bezier(0.45, 0.05, 0.3, 1)';
    if (grid) {
      track(
        grid.animate(reveal(grid), {
          duration: GRID_MS,
          delay: GRID_AT,
          easing: EASE_BG,
          fill: 'backwards',
        })
      );
      const scan = mk(styles.scanLine);
      track(
        scan.animate(
          [
            { opacity: 0, transform: 'translate3d(0, -8px, 0)' },
            { opacity: 0.7, transform: `translate3d(0, ${vh * 0.12}px, 0)`, offset: 0.15 },
            { opacity: 0.7, transform: `translate3d(0, ${vh * 0.88}px, 0)`, offset: 0.85 },
            { opacity: 0, transform: `translate3d(0, ${vh - 8}px, 0)` },
          ],
          { duration: GRID_MS, delay: GRID_AT, easing: EASE_BG, fill: 'both' }
        )
      );
    }
    if (field && fieldRect && orbBody) {
      // the glyph field fades up in two long ramps (ignite, then settle)
      track(
        field.animate([{ opacity: 0 }, { opacity: 0.4, offset: 0.55 }, { opacity: 0.55 }], {
          duration: RIPPLE1_MS * 2,
          delay: land,
          easing: 'ease-in-out',
          fill: 'both',
        })
      );
      track(
        field.animate([{ opacity: 0.55 }, { opacity: 1 }], {
          duration: RIPPLE2_MS * 1.6,
          delay: flareAt - 400,
          easing: 'ease-in-out',
          fill: 'forwards',
        })
      );
      // expanding dotted rings (opacity based) ride out from the nucleus
      (
        [
          [land, RIPPLE1_MS * 1.6, Math.hypot(fieldRect.width, fieldRect.height) * 0.3],
          [flareAt, RIPPLE2_MS * 1.6, Math.hypot(fieldRect.width, fieldRect.height) * 0.55],
        ] as const
      ).forEach(([when, ms, r]) => {
        const ring = mk(styles.ripple);
        const box = (d: number) => ({
          width: `${d}px`,
          height: `${d}px`,
          margin: `${-d / 2}px 0 0 ${-d / 2}px`,
        });
        track(
          ring.animate(
            [
              { ...box(0), opacity: 0, transform: `translate3d(${cx}px, ${cy}px, 0)` },
              {
                ...box(r),
                opacity: 0.55,
                transform: `translate3d(${cx}px, ${cy}px, 0)`,
                offset: 0.25,
              },
              { ...box(r * 2), opacity: 0, transform: `translate3d(${cx}px, ${cy}px, 0)` },
            ],
            { duration: ms, delay: when, easing: EASE_WAVE, fill: 'forwards' }
          )
        );
      });
    }
    // the rest of the root (chat dock, overlays) arrives with the boxes
    Array.from(root.children).forEach((c) => {
      if (!(c instanceof HTMLElement)) return;
      if (
        c.hasAttribute('data-ix-bg') ||
        c.hasAttribute('data-ix-zone') ||
        c.hasAttribute('data-ix-replica') ||
        c.hasAttribute('data-ix-badge') ||
        c.hasAttribute('data-ambient-particles') ||
        c.tagName === 'CANVAS' ||
        c.classList.contains(styles.dot) ||
        c.classList.contains(styles.ping) ||
        c.classList.contains(styles.scanLine) ||
        c.classList.contains(styles.ripple)
      )
        return;
      track(
        c.animate(reveal(c), {
          duration: 1400,
          delay: Math.max(0, boxesEnd * 0.5),
          easing: 'ease-in-out',
          fill: 'backwards',
        })
      );
    });

    // 6. sound, from the same plan.
    stopSfx = playIntroSfx({
      boxes: sfxBoxes,
      boxesEnd,
      orbAt,
      dotMs: DOT_MS,
      layers: layerPlan.sort((x, y) => x.idx - y.idx),
      flareAt,
      endAt: end,
      bg: bgPlan,
      mode,
    });

    // 7. phase callbacks + failsafe teardown.
    timers.push(
      setTimeout(() => {
        if (!ended) cb.onLate();
      }, boxesEnd)
    );
    timers.push(
      setTimeout(() => {
        if (ended) return;
        cleanup();
        cb.onDone();
      }, end)
    );
  } catch (err) {
    stopSfx();
    cleanup();
    throw err;
  }

  return {
    cancel: () => {
      stopSfx();
      cleanup();
    },
  };
}

/** Brief text scramble: letters/digits flip to glyphs and resolve to the
 *  real text. A node React updated meanwhile is left alone. */
function scramble(
  el: HTMLElement,
  ms: number,
  intervals: ReturnType<typeof setInterval>[],
  restores: (() => void)[]
): void {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const nodes: { node: Text; orig: string; last: string }[] = [];
  for (let n = walker.nextNode(); n && nodes.length < 60; n = walker.nextNode()) {
    const t = n as Text;
    if (t.data.length > 0 && t.data.length <= 400 && /[A-Za-z0-9]/.test(t.data)) {
      nodes.push({ node: t, orig: t.data, last: t.data });
    }
  }
  if (nodes.length === 0) return;
  restores.push(() => {
    for (const s of nodes) if (s.last !== '\u0000' && s.node.data === s.last) s.node.data = s.orig;
  });
  const ticks = Math.max(3, Math.round(ms / 50));
  let tick = 0;
  const id = setInterval(() => {
    tick++;
    const p = tick / ticks;
    for (const s of nodes) {
      if (s.last === '\u0000') continue;
      if (s.node.data !== s.last) {
        s.last = '\u0000'; // React rewrote it: hands off
        continue;
      }
      const next =
        p >= 1
          ? s.orig
          : s.orig.replace(/[A-Za-z0-9]/g, (ch) =>
              Math.random() < (1 - p) * 0.9
                ? SCRAMBLE[Math.floor(Math.random() * SCRAMBLE.length)]
                : ch
            );
      s.node.data = next;
      s.last = next;
    }
    if (p >= 1) clearInterval(id);
  }, 50);
  intervals.push(id);
}
