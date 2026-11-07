/**
 * Shared "orb energy" dial (0..1). 1 = the live orb at full speed; near 0 it
 * has settled back into the static wallpaper emblem (rings stopped, plasma
 * frozen, glyph field slow). The HUD intro (spin-up) and the presence fade /
 * appear (components/stark/use-hud-presence.ts) drive it; the ASCII reactor
 * reads it every frame, and CSS animations inside the orb body are slowed via
 * playbackRate (WebKit-safe: only CSSAnimation instances are touched, never
 * the Web Animations the intro/presence scrub themselves).
 */

let energy = 1;
let lastApply = 0;
let lastRate = 1;

/** Current dial value; cheap enough to read every frame. */
export function getOrbEnergy(): number {
  return energy;
}

function isCssAnimation(a: Animation): boolean {
  return typeof (a as { animationName?: unknown }).animationName === 'string';
}

/** Set the dial. CSS animation rates are updated at most every ~90 ms (and
 *  always when the value reaches 1 or 0) so a per-frame caller stays cheap. */
export function setOrbEnergy(scope: Element | null, value: number, force = false): void {
  const e = Math.max(0.03, Math.min(1, value));
  energy = e;
  if (!scope) return;
  const now = performance.now();
  const edge = e >= 1 || e <= 0.03;
  if (!force && !edge && now - lastApply < 90) return;
  if (!force && edge && lastRate === e) return;
  lastApply = now;
  lastRate = e;
  try {
    const body = scope.querySelector('[data-ix-orb]') ?? scope;
    body.getAnimations({ subtree: true }).forEach((a) => {
      if (!isCssAnimation(a)) return;
      try {
        a.updatePlaybackRate(e);
      } catch {
        /* ignore */
      }
    });
  } catch {
    /* getAnimations unsupported */
  }
}

/** Tween the dial from -> to; returns cancel. Leaves the dial where it is. */
export function rampOrbEnergy(
  scope: Element | null,
  from: number,
  to: number,
  ms: number,
  delay = 0
): () => void {
  let raf = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  const begin = () => {
    const t0 = performance.now();
    const tick = () => {
      if (stopped) return;
      const k = Math.min(1, (performance.now() - t0) / Math.max(1, ms));
      const eased = 1 - (1 - k) * (1 - k);
      setOrbEnergy(scope, from + (to - from) * eased);
      if (k < 1) raf = requestAnimationFrame(tick);
      else setOrbEnergy(scope, to, true);
    };
    tick();
  };
  if (delay > 0) timer = setTimeout(begin, delay);
  else begin();
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    cancelAnimationFrame(raf);
  };
}
