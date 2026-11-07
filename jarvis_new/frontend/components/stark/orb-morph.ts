/**
 * Static -> live orb morph.
 *
 * One scalar `m` (0..1) is the whole story: 0 = the wallpaper's static ASCII
 * emblem (the replica canvas, pixel-identical to the PNG), 1 = the live orb.
 * Everything else is derived from it, so the HUD intro (a timed tween), the
 * presence fade / wake (m follows the presence progress) and any interruption
 * all share one continuous path and nothing can snap:
 *
 *   energy(m)   motion dial: ASCII field clock, CSS ring / plasma playback rate
 *   reveal(m)   emblem glyphs hand over to the live layers core-first
 *   layerWindow live layer i cross-fades in during [a, b] of m (generous overlap)
 *
 * The field clock `t` starts exactly at the wallpaper's static pose and only
 * ever advances by energy * dt, so there is no phase reset anywhere.
 */
import { setOrbEnergy } from '@/lib/orb-motion';
import { PHASE_PERIODS, type ReplicaFrame } from '@/lib/wall-replica';

/** Length of the static -> live morph when it is time based (intro, wake). */
export const MORPH_MS = 4200;

const clamp01 = (x: number): number => (x < 0 ? 0 : x > 1 ? 1 : x);
export const smoothstep = (a: number, b: number, x: number): number => {
  const t = clamp01((x - a) / (b - a));
  return t * t * (3 - 2 * t);
};

/** Motion dial for a morph progress: eased, zero until the morph starts. */
export function energyOf(m: number): number {
  return smoothstep(0, 0.6, m);
}

/** Live layer `idx` (0 = nucleus .. 7 = callouts) cross-fades during [a, b] of m. */
export function layerWindow(idx: number): [number, number] {
  const a = 0.05 + 0.05 * idx;
  return [a, a + 0.28];
}

/** Below this morph progress, a falling morph settles onto the static pose. */
const SETTLE_BELOW = 0.5;

/** The replica canvas fades out over the last stretch of the wind-down. */
export const REPLICA_FADE: [number, number] = [0, 0.12];

export type ReplicaPainter = {
  drawStatic: () => void;
  drawFrame: (f: ReplicaFrame) => void;
  staticT: number;
};

type Tween = { from: number; to: number; start: number; ms: number; ease: (x: number) => number };

class OrbMorph {
  m = 0;
  /** field phases [rim, dash, coil, beam, triangle, pulse] (see PHASE_PERIODS) */
  private ph: number[] = [0, 0, 0, 0, 0, 0];
  private mode: 'free' | 'settle' = 'free';
  private settleStart: number[] = [];
  private settleGoal: number[] = [];
  private settleM0 = 1;
  private prevM = 0;
  private painter: ReplicaPainter | null = null;
  private scope: Element | null = null;
  private raf = 0;
  private last = 0;
  private lastDraw = 0;
  private tween: Tween | null = null;
  private ignitedAt = -1e9;
  private dirty = true;

  /** The HUD root (scope for the CSS motion dial); call before the painter arrives. */
  setScope(scope: Element | null): void {
    this.scope = scope;
  }

  attach(scope: Element, painter: ReplicaPainter | null): void {
    this.scope = scope;
    this.painter = painter;
    if (painter) {
      this.ph.fill(painter.staticT);
      this.dirty = true;
      this.kick();
    }
  }

  detach(): void {
    cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.painter = null;
    this.tween = null;
  }

  /** Back to the exact static pose (used while the replica is invisible). */
  reset(): void {
    this.tween = null;
    this.m = 0;
    if (this.painter) this.ph.fill(this.painter.staticT);
    this.mode = 'free';
    this.prevM = 0;
    this.ignitedAt = -1e9;
    this.dirty = true;
    setOrbEnergy(this.scope, 0.03, true);
    this.painter?.drawStatic();
  }

  /** Follow an external progress (presence fade / wake). */
  set(m: number): void {
    this.tween = null;
    const next = clamp01(m);
    if (next !== this.m) this.dirty = true;
    this.m = next;
    this.kick();
  }

  /** Time based morph to `to`. Returns a cancel function. */
  to(to: number, ms: number, delay = 0, ease: (x: number) => number = (x) => x): () => void {
    const start = performance.now() + delay;
    this.tween = { from: this.m, to, start, ms: Math.max(1, ms), ease };
    this.kick();
    const mine = this.tween;
    return () => {
      if (this.tween === mine) this.tween = null;
    };
  }

  /** Core ignition: a brightness pulse and a shock ring from the nucleus. */
  ignite(delay = 0): void {
    this.ignitedAt = performance.now() + delay;
    this.dirty = true;
    this.kick();
  }

  private frame(now: number): ReplicaFrame {
    const since = (now - this.ignitedAt) / 1000;
    const boost = since >= 0 && since < 2.2 ? 1.1 * Math.exp(-since * 2.4) : 0;
    const shock: [number, number] | null =
      since >= 0 && since < 1.5 ? [0.12 + since * 0.8, (1 - since / 1.5) ** 2 * 0.8] : null;
    return {
      t: this.ph[0],
      tt: this.ph,
      boost,
      shock,
      reveal: this.m >= 1 ? 1 : this.m,
    };
  }

  /** Free run while the morph rises; on the way down (below SETTLE_BELOW) every
   *  component glides onto its next pose-equivalent time, so the orb settles
   *  (triangle swings upright, coils / beam come to rest) and lands exactly on
   *  the wallpaper's static pose as m reaches 0. A wake mid-settle resumes free
   *  running from the current pose: no jump. */
  private advance(dt: number, e: number): void {
    const base = this.painter?.staticT ?? 0;
    const falling = this.m < this.prevM - 1e-5;
    const rising = this.m > this.prevM + 1e-5;
    this.prevM = this.m;
    if (this.mode === 'free' && falling && this.m < SETTLE_BELOW) {
      this.mode = 'settle';
      this.settleM0 = Math.max(1e-3, this.m);
      this.settleStart = this.ph.slice();
      this.settleGoal = this.ph.map(
        (v, i) => base + Math.ceil((v - base) / PHASE_PERIODS[i] - 1e-9) * PHASE_PERIODS[i]
      );
    } else if (this.mode === 'settle' && rising) {
      this.mode = 'free';
    }
    if (this.mode === 'settle') {
      const s = smoothstep(0, 1, 1 - this.m / this.settleM0);
      for (let i = 0; i < 6; i++) {
        this.ph[i] = this.settleStart[i] + (this.settleGoal[i] - this.settleStart[i]) * s;
      }
    } else {
      for (let i = 0; i < 6; i++) this.ph[i] += dt * e;
    }
  }

  private kick(): void {
    if (!this.raf && this.scope) {
      this.last = performance.now();
      this.raf = requestAnimationFrame(this.tick);
    }
  }

  private tick = (now: number): void => {
    this.raf = 0;
    const p = this.painter;
    const dt = Math.min(0.1, (now - this.last) / 1000);
    this.last = now;
    if (this.tween) {
      const k = clamp01((now - this.tween.start) / this.tween.ms);
      this.m = this.tween.from + (this.tween.to - this.tween.from) * this.tween.ease(k);
      if (k >= 1) this.tween = null;
      this.dirty = true;
    }
    const e = energyOf(this.m);
    setOrbEnergy(this.scope, e);
    const animating = this.m > 0 && this.m < 1;
    const sinceIgnite = now - this.ignitedAt;
    const ignited = sinceIgnite >= 0 && sinceIgnite < 2300;
    if (animating || ignited) {
      this.advance(dt, e);
      // ~30 fps for the CPU raster; the CSS layers run at display rate
      if (p && now - this.lastDraw >= 30) {
        this.lastDraw = now;
        p.drawFrame(this.frame(now));
      }
      this.raf = requestAnimationFrame(this.tick);
      return;
    }
    if (this.m <= 0 && this.mode === 'settle' && p) {
      // landed: every component is pose-equivalent to the static pose
      this.ph.fill(p.staticT);
      this.mode = 'free';
      this.dirty = true;
    }
    if (this.dirty && p) {
      this.dirty = false;
      if (this.m <= 0 && this.ph.every((v) => Math.abs(v - p.staticT) < 1e-9)) p.drawStatic();
      else p.drawFrame(this.frame(now));
    }
    if (this.tween) this.raf = requestAnimationFrame(this.tick);
  };
}

export const orbMorph = new OrbMorph();
