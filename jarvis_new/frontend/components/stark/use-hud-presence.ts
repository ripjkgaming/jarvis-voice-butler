'use client';

import { type RefObject, useEffect, useRef, useState } from 'react';
import { isTauri, listenToTalk, setOverlayClickThrough } from '@/lib/tauri';
import type { VoiceLink } from '@/lib/voice-link';
import { playPresenceSfx } from './intro-sfx';
import styles from './intro.module.css';
import { REPLICA_FADE, layerWindow, orbMorph } from './orb-morph';
import { type HudIntroPhase, INTRO_REPLAY_EVENT } from './use-hud-intro';

// Where the wallpaper's static emblem sits (the orb settles back onto it when
// Jarvis fades and wakes out of it when he is called). Tune in wall-emblem.ts.
export { WALL_BG_RGB, WALL_EMBLEM_CENTER, WALL_EMBLEM_RADIUS_FRAC } from './wall-emblem';

// ---- tuning ---------------------------------------------------------------
/** Idle this long (no voice, job, caption, key, click, focus) before he starts to
 *  fade. Until then the full HUD stays drawn. Only real activity resets the
 *  timer; passive polling never does. */
export const IDLE_GRACE_MS = 20 * 60 * 1000;
/** Whole-HUD dissolve length, present -> gone. */
export const FADE_MS = 7_000;
/** While fading, below this presence the window turns click-through. */
const CLICK_THROUGH_BELOW = 0.22;
/** A wake that catches him mid-fade reverses from the current presence over this. */
export const RETURN_MS = 1_200;
/** A wake from fully gone replays the boot choreography at its fixed timing
 *  (intro 'wake' mode); this is the safety net if it never reports done. */
export const WAKE_FAILSAFE_MS = 14_000;
/** The wake intro cannot be "done" before it has had time to start. */
const WAKE_GUARD_MS = 600;

const KEY_ENABLED = 'jarvis.presence.enabled';
/** A requested hide ("Jarvis, hide yourself") ignores voice / link activity
 *  this long, so his spoken goodbye and the call teardown do not wake him
 *  straight back. Real input (key, click, talk hotkey, show) still wakes. */
const HIDE_HOLD_MS = 25_000;

/** Standby badge opacity in the gone state (matches stark.module.css .badge). */
export const BADGE_OPACITY = 0.9;

export type PresenceState = 'present' | 'fading' | 'gone' | 'appearing';

export type PresenceInput = {
  introPhase: HudIntroPhase;
  /** Jarvis voice state: anything but 'idle' keeps him present. */
  active: boolean;
  link: VoiceLink;
  /** Recent typed exchange: keeps him present and wakes him. */
  busy: boolean;
  /** Background job running: keeps the HUD drawn while present, but never wakes
   *  a faded HUD (the standby badge shows the job instead). */
  jobs: boolean;
};

type Track = { anim: Animation; a: number; b: number };

function readEnabled(): boolean {
  try {
    return localStorage.getItem(KEY_ENABLED) !== '0';
  } catch {
    return true;
  }
}

function writeEnabled(on: boolean): void {
  try {
    localStorage.setItem(KEY_ENABLED, on ? '1' : '0');
  } catch {
    /* storage unavailable */
  }
}

const clamp01 = (x: number): number => (x < 0 ? 0 : x > 1 ? 1 : x);
const smooth = (x: number): number => {
  const q = clamp01(x);
  return q * q * (3 - 2 * q);
};

const BOX_FLICKER: Keyframe[] = [
  { offset: 0, opacity: 0 },
  { offset: 0.22, opacity: 0.45 },
  { offset: 0.32, opacity: 0.12 },
  { offset: 0.5, opacity: 0.8 },
  { offset: 0.64, opacity: 0.42 },
  { offset: 0.8, opacity: 1 },
  { offset: 1, opacity: 1 },
];

/** Every element that takes part in the dissolve, with its window [a, b] in
 *  presence space (0 = gone, 1 = present). Windows come from orb-morph.ts for
 *  the orb (live layers fade in generously overlapped while the emblem glyphs
 *  hand over), then backdrop / glyph field, then boxes ordered by distance
 *  from the screen centre (nearest first), the outermost last. Fading runs
 *  the same windows backwards: outer boxes dissolve first, the orb last, and
 *  the replica canvas finally dissolves into the identical wallpaper.
 *
 *  NB each track is explicit [from-hidden, natural]: a lone keyframe is the
 *  END keyframe in WAAPI (it would animate natural -> hidden, inverted). */
function buildTracks(root: HTMLElement): Track[] {
  const out: Track[] = [];
  const add = (el: Element | null, a: number, b: number, frames: Keyframe[]) => {
    if (!el || typeof (el as HTMLElement).animate !== 'function') return;
    try {
      const anim = (el as HTMLElement).animate(frames, {
        duration: 1000,
        fill: 'both',
        easing: 'linear',
      });
      anim.id = 'presence';
      anim.pause();
      out.push({ anim, a, b });
    } catch {
      /* element cannot animate */
    }
  };
  const natural = (el: Element) => getComputedStyle(el).opacity;
  const fade = (el: Element): Keyframe[] => [{ opacity: 0 }, { opacity: natural(el) }];

  // orb: replica canvas (dissolves into the identical wallpaper last), body,
  // live layers (core -> outward)
  root.querySelectorAll('[data-ix-replica]').forEach((el) => {
    add(el, REPLICA_FADE[0], REPLICA_FADE[1], fade(el));
  });
  const orbBody = root.querySelector(`.${styles.orbBody}`);
  if (orbBody) add(orbBody, 0, 0.1, fade(orbBody));
  root.querySelectorAll('[data-ix-layer]').forEach((el) => {
    const idx = Number(el.getAttribute('data-ix-layer'));
    if (!Number.isFinite(idx)) return;
    const [a, b] = layerWindow(idx);
    add(el, a, b, [
      { opacity: 0, scale: idx >= 6 ? '1.03' : '0.97' },
      { opacity: natural(el), scale: '1' },
    ]);
  });

  // standby badge: the mirror image, it fades IN as the HUD fades out
  root.querySelectorAll('[data-ix-badge]').forEach((el) => {
    add(el, 0, 0.45, [{ opacity: BADGE_OPACITY }, { opacity: 0 }]);
  });

  // backdrop + glyph field: long eased ramps
  root.querySelectorAll('[data-ix-bg], canvas[data-ambient-particles]').forEach((el) => {
    add(el, 0.05, 0.85, fade(el));
  });

  // boxes
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const boxes: { el: HTMLElement; d: number; whole: boolean }[] = [];
  const seen = new Set<Element>();
  const push = (el: HTMLElement, whole: boolean) => {
    if (seen.has(el)) return;
    seen.add(el);
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return;
    boxes.push({
      el,
      whole,
      d: Math.hypot(r.left + r.width / 2 - vw / 2, r.top + r.height / 2 - vh / 2),
    });
  };
  root.querySelectorAll<HTMLElement>('[data-ix-zone]').forEach((zone) => {
    const bar = zone.tagName === 'HEADER' || zone.tagName === 'FOOTER';
    if (bar) {
      push(zone, true);
      return;
    }
    Array.from(zone.children).forEach((c) => {
      if (!(c instanceof HTMLElement)) return;
      if (c.hasAttribute('data-ix-zone') || c.hasAttribute('data-ix-orb')) return;
      push(c, false);
    });
  });
  boxes.sort((x, y) => x.d - y.d);
  const n = boxes.length;
  boxes.forEach((b, i) => {
    const a = 0.34 + 0.4 * (n > 1 ? i / (n - 1) : 0);
    add(b.el, a, a + 0.26, b.whole ? fade(b.el) : BOX_FLICKER);
  });

  // everything else the HUD root carries (chat dock, overlays, ...)
  const handled = new Set<Element>(boxes.map((b) => b.el));
  const extras: HTMLElement[] = [];
  Array.from(root.children).forEach((c) => {
    if (!(c instanceof HTMLElement)) return;
    if (
      c.hasAttribute('data-ix-bg') ||
      c.hasAttribute('data-ambient-particles') ||
      c.hasAttribute('data-ix-replica') ||
      c.hasAttribute('data-ix-badge') ||
      c.hasAttribute('data-ix-zone') ||
      handled.has(c)
    )
      return;
    if (getComputedStyle(c).display === 'contents') {
      Array.from(c.children).forEach((g) => g instanceof HTMLElement && extras.push(g));
    } else extras.push(c);
  });
  extras.forEach((el) => add(el, 0.45, 0.85, fade(el)));
  return out;
}

/**
 * Presence mode. When Jarvis is not in use he dissolves into the wallpaper
 * (the live orb winds down onto the wallpaper's static emblem, then the replica
 * dissolves into the identical wallpaper), and when he is called he returns.
 * States: present -> fading -> gone -> appearing -> present.
 *
 * - Idle = Jarvis idle, no link in progress, no running job / recent typed
 *   exchange, no key / click / wheel / pointer activity for IDLE_GRACE_MS, and
 *   the load-in intro is not playing. Then FADE_MS of dissolve (boxes
 *   outermost first, orb last). Gone = window click-through and every
 *   animation / canvas paused (html.hud-hidden), nothing painted.
 * - Waking from gone replays the boot choreography at its FIXED original
 *   timing (use-hud-intro.ts, 'wake' mode: cards descend, slide, expand and
 *   glitch in; the orb wakes from the wallpaper emblem first). Nothing is
 *   stretched to the connection time.
 * - Waking mid-fade reverses from the CURRENT presence over RETURN_MS, never
 *   popping.
 * - Fade / return are opacity (+ tiny scale on orb layers) on paused Web
 *   Animations scrubbed from one rAF loop that runs only while it is needed.
 * - `P` toggles the feature (persisted); `window.__jarvisPresence` for debug.
 *
 * Purely visual: never gates data polling or voice input.
 */
export function useHudPresence(
  rootRef: RefObject<HTMLElement | null>,
  input: PresenceInput
): PresenceState {
  const [state, setState] = useState<PresenceState>('present');
  const inputRef = useRef(input);
  const poke = useRef<(() => void) | null>(null);

  useEffect(() => {
    inputRef.current = input;
    poke.current?.();
  });

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const html = document.documentElement;

    let st: PresenceState = 'present';
    let p = 1;
    let enabled = readEnabled();
    let lastActive = Date.now();
    let raf = 0;
    let lastT = 0;
    let tracks: Track[] = [];
    const badgeAnims: Animation[] = [];
    let returnFrom = 0;
    let returnStart = 0;
    let wakeStart = 0;
    let clickThrough = false;
    let hideHoldUntil = 0;
    let hidSelf = false;
    let disposed = false;
    const timers: ReturnType<typeof setTimeout>[] = [];

    const go = (next: PresenceState) => {
      if (st === next) return;
      st = next;
      root.setAttribute('data-presence', next);
      if (!disposed) setState(next);
    };
    const ct = (ignore: boolean) => {
      if (clickThrough === ignore) return;
      clickThrough = ignore;
      if (isTauri()) void setOverlayClickThrough(ignore);
    };
    const dropTracks = () => {
      tracks.forEach((t) => {
        try {
          t.anim.cancel();
        } catch {
          /* ignore */
        }
      });
      tracks = [];
    };
    const render = (pp: number) => {
      for (const t of tracks) {
        t.anim.currentTime = smooth((pp - t.a) / (t.b - t.a)) * 1000;
      }
      // static -> live morph follows the presence: motion, glyph hand-over
      orbMorph.set(pp);
    };
    const hideSelf = (on: boolean) => {
      hidSelf = on;
      root.style.visibility = on ? 'hidden' : '';
      if (on) {
        html.setAttribute('data-hud-gone', 'true');
        html.classList.add('hud-hidden');
      } else {
        html.removeAttribute('data-hud-gone');
        if (!document.hidden) html.classList.remove('hud-hidden');
      }
    };
    const stopLoop = () => {
      cancelAnimationFrame(raf);
      raf = 0;
    };
    const startLoop = () => {
      if (raf) return;
      lastT = performance.now();
      raf = requestAnimationFrame(tick);
    };

    const forcePresent = (sound: boolean) => {
      stopLoop();
      dropTracks();
      badgeAnims.splice(0).forEach((a) => a.cancel());
      if (hidSelf) hideSelf(false);
      p = 1;
      orbMorph.set(1);
      ct(false);
      if (st === 'appearing' && sound) playPresenceSfx('settle');
      go('present');
      lastActive = Date.now();
    };

    const startFade = () => {
      tracks = buildTracks(root);
      p = 1;
      render(1);
      go('fading');
      playPresenceSfx('fade', FADE_MS);
      startLoop();
    };

    /** Called him while he was only partly faded: reverse from where he is. */
    const startReturn = () => {
      if (tracks.length === 0) tracks = buildTracks(root);
      returnFrom = p;
      returnStart = performance.now();
      ct(false);
      go('appearing');
      playPresenceSfx('appear', RETURN_MS);
      startLoop();
    };

    /** Called him while gone: replay the boot choreography (fixed timing). */
    const startWake = () => {
      stopLoop();
      dropTracks();
      hideSelf(false);
      p = 0;
      orbMorph.reset();
      wakeStart = Date.now();
      ct(false);
      go('appearing');
      // the badge (visible while gone) dissolves as the HUD returns
      root.querySelectorAll<HTMLElement>('[data-ix-badge]').forEach((el) => {
        try {
          const a = el.animate([{ opacity: BADGE_OPACITY }, { opacity: 0 }], {
            duration: 1200,
            easing: 'ease-in-out',
            fill: 'forwards',
          });
          badgeAnims.push(a);
        } catch {
          /* ignore */
        }
      });
      window.dispatchEvent(new CustomEvent(INTRO_REPLAY_EVENT, { detail: 'wake' }));
      timers.push(setTimeout(() => poke.current?.(), WAKE_GUARD_MS + 50));
      timers.push(
        setTimeout(() => {
          if (st === 'appearing' && tracks.length === 0) forcePresent(false);
        }, WAKE_FAILSAFE_MS)
      );
    };

    const wake = (reason: string) => {
      lastActive = Date.now();
      if (reason === 'state' && Date.now() < hideHoldUntil) return;
      hideHoldUntil = 0;
      if (st === 'present' || st === 'appearing') return;
      if (st === 'gone') startWake();
      else startReturn();
    };

    function tick(now: number) {
      raf = 0;
      if (disposed) return;
      const dt = Math.min(64, now - lastT);
      lastT = now;
      if (st === 'fading') {
        p -= dt / FADE_MS;
        if (p <= 0) {
          p = 0;
          render(0);
          dropTracks();
          orbMorph.reset();
          hideSelf(true);
          ct(true);
          go('gone');
          return;
        }
        if (p < CLICK_THROUGH_BELOW) ct(true);
        render(p);
        raf = requestAnimationFrame(tick);
        return;
      }
      if (st === 'appearing' && tracks.length > 0) {
        const f = clamp01((now - returnStart) / RETURN_MS);
        const e = f * f * (3 - 2 * f);
        p = returnFrom + (1 - returnFrom) * e;
        render(p);
        if (f >= 1) {
          forcePresent(true);
          return;
        }
        raf = requestAnimationFrame(tick);
      }
    }

    // ---- inputs -----------------------------------------------------------
    const onInputs = () => {
      const i = inputRef.current;
      const introRunning =
        i.introPhase === 'pre' || i.introPhase === 'run' || i.introPhase === 'late';
      const engaged = i.active || i.link.phase !== 'idle' || i.busy;
      if (st === 'appearing' && tracks.length === 0) {
        // wake intro in flight: present once it reports done
        if (!introRunning && Date.now() - wakeStart > WAKE_GUARD_MS) forcePresent(false);
        lastActive = Date.now();
        return;
      }
      if (introRunning) {
        if ((st === 'fading' || st === 'gone') && Date.now() >= hideHoldUntil) forcePresent(false);
        lastActive = Date.now();
        return;
      }
      if (engaged) {
        if (st === 'fading' || st === 'gone') wake('state');
        lastActive = Date.now();
      } else if (i.jobs && st === 'present') {
        lastActive = Date.now();
      }
    };
    poke.current = onInputs;
    onInputs();

    const idleTimer = setInterval(() => {
      if (st !== 'present' || !enabled || document.hidden) return;
      const i = inputRef.current;
      if (i.introPhase !== 'done' && i.introPhase !== 'off') return;
      if (i.active || i.link.phase !== 'idle' || i.busy || i.jobs) {
        lastActive = Date.now();
        return;
      }
      if (Date.now() - lastActive >= IDLE_GRACE_MS) startFade();
    }, 1000);

    let lastMove = 0;
    const onPointerMove = () => {
      const t = Date.now();
      if (t - lastMove < 400) return;
      lastMove = t;
      if (st === 'present') lastActive = t;
    };
    const onEngage = () => {
      lastActive = Date.now();
      if (st === 'fading' || st === 'gone') wake('input');
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      const typing =
        !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
      if (!typing && !e.repeat && (e.key === 'p' || e.key === 'P')) {
        enabled = !enabled;
        writeEnabled(enabled);
        if (!enabled && st !== 'present') wake('toggle');
        lastActive = Date.now();
        return;
      }
      onEngage();
    };
    const onVisible = () => {
      if (!document.hidden) wake('show');
    };
    // A dissolved HUD still owns a visible native window. Showing it again
    // does not fire visibilitychange, so the shell explicitly wakes it.
    const onShow = () => wake('show');
    let offTalk: () => void = () => undefined;
    void listenToTalk(() => wake('talk')).then((off) => {
      if (disposed) off();
      else offTalk = off;
    });

    window.addEventListener('keydown', onKey);
    window.addEventListener('pointerdown', onEngage, { passive: true });
    window.addEventListener('wheel', onEngage, { passive: true });
    window.addEventListener('touchstart', onEngage, { passive: true });
    window.addEventListener('pointermove', onPointerMove, { passive: true });
    window.addEventListener('jarvis-hud-show', onShow);
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onEngage);

    const dbg = {
      get enabled() {
        return enabled;
      },
      get state() {
        return st;
      },
      get progress() {
        return p;
      },
      toggle() {
        enabled = !enabled;
        writeEnabled(enabled);
        if (!enabled && st !== 'present') wake('toggle');
        return enabled;
      },
      wake: () => wake('debug'),
      /** Requested hide: the same dissolve as the idle timeout, now. */
      fade: () => {
        if (st !== 'present') return;
        hideHoldUntil = Date.now() + HIDE_HOLD_MS;
        startFade();
      },
    };
    (window as unknown as { __jarvisPresence?: unknown }).__jarvisPresence = dbg;

    return () => {
      disposed = true;
      poke.current = null;
      clearInterval(idleTimer);
      timers.forEach(clearTimeout);
      stopLoop();
      offTalk();
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('pointerdown', onEngage);
      window.removeEventListener('wheel', onEngage);
      window.removeEventListener('touchstart', onEngage);
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('jarvis-hud-show', onShow);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onEngage);
      dropTracks();
      badgeAnims.splice(0).forEach((a) => a.cancel());
      if (hidSelf) hideSelf(false);
      if (clickThrough) ct(false);
      root.removeAttribute('data-presence');
      const w = window as unknown as { __jarvisPresence?: unknown };
      if (w.__jarvisPresence === dbg) delete w.__jarvisPresence;
    };
  }, [rootRef]);

  return state;
}
