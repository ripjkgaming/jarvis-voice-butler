/** The animated mark (PLAN.md §4.4). One model drives every <Mark> instance:
 * 16 core vertices (x/y springs), 4 satellites (x, y, scale, rotate, opacity),
 * ring colour (linear RGB) + dash + spin rate, and core fill (linear RGB), all
 * on the critically damped `mark` preset. A state change only retargets, so a
 * glyph morphs from wherever it is with its velocity intact. Live modulation
 * (mic/playback level, flicker, spin) is additive and never moves a target. */
import data from './mark-glyphs.json';
import { PRESETS, Spring } from './spring';
import type { Glyph } from '../store';

export type GlyphName = 'star' | 'orbit' | 'flame' | 'play';
type Sat = { x: number; y: number; scale: number; rotate: number; opacity: number };
type Shape = { ring: { color: string; dash: number | number[] }; core: { fill: string; points: number[][] }; sats: Sat[] };

export const MARK = data as unknown as {
  ring_radius: number;
  glyphs: Record<GlyphName, Shape>;
  modifiers: { locked: { ring: Shape['ring']; core: Shape['core'] }; guest: { ring: Shape['ring'] } };
};
export const GLYPHS: GlyphName[] = ['star', 'orbit', 'flame', 'play'];
export const IGNITION: GlyphName[] = ['orbit', 'flame', 'play', 'star'];
export const IGNITION_HOLD_MS = 600;

const lin = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const srgb = (c: number) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);
export function hexToLinear(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [lin(((n >> 16) & 255) / 255), lin(((n >> 8) & 255) / 255), lin((n & 255) / 255)];
}
export function linearToCss(rgb: number[]): string {
  const b = (v: number) => Math.round(Math.min(1, Math.max(0, srgb(Math.min(1, Math.max(0, v))))) * 255);
  return `rgb(${b(rgb[0])},${b(rgb[1])},${b(rgb[2])})`;
}

export type MarkTarget = {
  points: number[][];
  fill: [number, number, number];
  sats: Sat[];
  ring: [number, number, number];
  dash: number;
  spin: number; // rev/s of the satellite group
};

const ZERO_SAT: Sat = { x: 0, y: 0, scale: 0, rotate: 0, opacity: 0 };

export function targetFor(glyph: Glyph, guest: boolean, reduced = false): MarkTarget {
  const base = MARK.glyphs[glyph === 'locked' ? 'star' : glyph];
  const locked = glyph === 'locked';
  const ring = locked ? MARK.modifiers.locked.ring : base.ring;
  const core = locked ? MARK.modifiers.locked.core : base.core;
  const sats = locked ? [ZERO_SAT, ZERO_SAT, ZERO_SAT, ZERO_SAT] : base.sats;
  const spin = reduced ? 0 : glyph === 'star' ? 1 / 20 : glyph === 'orbit' ? 0.5 : 0;
  return {
    points: core.points,
    fill: hexToLinear(core.fill),
    sats,
    ring: hexToLinear(guest ? MARK.modifiers.guest.ring.color : ring.color),
    dash: guest ? 1 : 0,
    spin,
  };
}

export type Modulation = { agent: string; micRms: number; playRms: number };

/** Writes for one SVG instance (see ui/Mark.tsx). */
export type MarkView = {
  core: SVGPathElement;
  coreGroup: SVGGElement;
  satGroup: SVGGElement;
  sats: SVGGElement[];
  rings: SVGCircleElement[];
  /** The page holding this instance; hidden pages are not written. */
  page?: Element | null;
};

const f = (v: number) => Math.round(v * 100) / 100;

// Attribute writes invalidate SVG paint even if their value did not change.
const attributes = new WeakMap<Element, Map<string, string>>();
function attr(el: Element, name: string, value: string) {
  let cache = attributes.get(el);
  if (!cache) attributes.set(el, (cache = new Map()));
  if (cache.get(name) === value) return;
  cache.set(name, value);
  el.setAttribute(name, value);
}

export class MarkModel {
  pts: Spring[] = [];
  sat: Spring[][] = [];
  ring: Spring[];
  fill: Spring[];
  dash: Spring;
  spin: Spring;
  wListen: Spring;
  wSpeak: Spring;
  wThink: Spring;
  angle = 0;
  t = 0;
  views = new Set<MarkView>();
  glyph: Glyph = 'star';
  guest = false;
  reduced: boolean;

  constructor(reduced = false, initial: Glyph = 'star') {
    this.reduced = reduced;
    const tgt = targetFor(initial, false, reduced);
    const s = (x: number) => new Spring(x, PRESETS.mark, 0.0005);
    for (const [x, y] of tgt.points) this.pts.push(s(x), s(y));
    for (const sat of tgt.sats) this.sat.push([s(sat.x), s(sat.y), s(sat.scale), s(sat.rotate), s(sat.opacity)]);
    this.ring = tgt.ring.map(s);
    this.fill = tgt.fill.map(s);
    this.dash = s(tgt.dash);
    this.spin = s(tgt.spin);
    this.wListen = s(0);
    this.wSpeak = s(0);
    this.wThink = s(0);
    this.glyph = initial;
  }

  private springCache: Spring[] | null = null;
  private all(): Spring[] {
    return this.springCache ??= [...this.pts, ...this.sat.flat(), ...this.ring, ...this.fill, this.dash, this.spin, this.wListen, this.wSpeak, this.wThink];
  }

  get settled() {
    return this.all().every((s) => s.settled);
  }

  /** Retarget every part (never resets position or velocity). */
  setGlyph(glyph: Glyph, guest = this.guest, snap = this.reduced) {
    this.glyph = glyph;
    this.guest = guest;
    const t = targetFor(glyph, guest, this.reduced);
    const set = (sp: Spring, v: number) => (snap ? sp.snap(v) : sp.setTarget(v));
    t.points.forEach(([x, y], i) => {
      set(this.pts[2 * i], x);
      set(this.pts[2 * i + 1], y);
    });
    t.sats.forEach((sat, i) => {
      const [x, y, sc, r, o] = this.sat[i];
      set(x, sat.x);
      set(y, sat.y);
      set(sc, sat.scale);
      // shortest rotation path
      const cur = r.x;
      set(r, cur + ((((sat.rotate - cur) % 360) + 540) % 360) - 180);
      set(o, sat.opacity);
    });
    t.ring.forEach((v, i) => set(this.ring[i], v));
    t.fill.forEach((v, i) => set(this.fill[i], v));
    set(this.dash, t.dash);
    set(this.spin, t.spin);
    if (!this.reduced) {
      this.wListen.setTarget(glyph === 'orbit' ? 1 : 0);
      this.wSpeak.setTarget(glyph === 'play' ? 1 : 0);
      this.wThink.setTarget(glyph === 'flame' ? 1 : 0);
    }
  }

  /** Current vertex i in mark units (tests, e2e probes). */
  vertex(i: number): [number, number] {
    return [this.pts[2 * i].x, this.pts[2 * i + 1].x];
  }

  coreD(): string {
    let d = '';
    for (let i = 0; i < 16; i++) d += (i ? 'L' : 'M') + f(this.pts[2 * i].x) + ' ' + f(this.pts[2 * i + 1].x);
    return d + 'Z';
  }

  /** Advance springs and live modulation; returns true while anything moves. */
  step(dt: number, mod: Modulation): boolean {
    let moving = false;
    for (const sp of this.all()) if (sp.advance(dt)) moving = true;
    this.t += dt;
    if (!this.reduced) this.angle = (this.angle + this.spin.x * 360 * dt) % 360;
    const live = !this.reduced && (this.spin.x > 0.0001 || this.wThink.x > 0.001 || this.wSpeak.x > 0.001 || this.wListen.x > 0.001);
    this.write(mod);
    return moving || live || mod.micRms > 0 || mod.playRms > 0;
  }

  write(mod: Modulation) {
    if (!this.views.size) return;
    const d = this.coreD();
    const fill = linearToCss(this.fill.map((s) => s.x));
    const ring = linearToCss(this.ring.map((s) => s.x));
    const g = Math.max(0, this.dash.x);
    const dash = g < 0.01 ? 'none' : `3 ${f(2.2 * g)}`;
    const radius = 1 + ((6 * mod.micRms) / 14) * this.wListen.x;
    const speak = 1 + 0.12 * mod.playRms * this.wSpeak.x;
    const flick = 1 + (0.05 * Math.sin(this.t * 13) + 0.03 * Math.sin(this.t * 29)) * this.wThink.x;
    const coreT = `scale(${f(speak)},${f(speak * flick)})`;
    const satT = `rotate(${f(this.angle)})`;
    const sats = this.sat.map(
      ([x, y, sc, r, o]) =>
        [`translate(${f(x.x * radius)},${f(y.x * radius)}) rotate(${f(r.x)}) scale(${f(Math.max(0, sc.x))})`, f(Math.min(1, Math.max(0, o.x)))] as const,
    );
    for (const v of this.views) {
      if (v.page?.hasAttribute('data-hidden')) continue;
      attr(v.core, 'd', d);
      attr(v.core, 'fill', fill);
      attr(v.core, 'stroke', fill);
      attr(v.coreGroup, 'transform', coreT);
      attr(v.satGroup, 'transform', satT);
      sats.forEach(([tr, op], i) => {
        attr(v.sats[i], 'transform', tr);
        attr(v.sats[i], 'opacity', String(op));
      });
      for (const c of v.rings) {
        attr(c, 'stroke', ring);
        attr(c, 'stroke-dasharray', dash);
      }
    }
  }
}

/** Ignition cycle runner: orbit → flame → play → star, 600 ms each. */
export class Ignition {
  private i = 0;
  private cancel: (() => void) | null = null;
  constructor(
    private model: MarkModel,
    private after: (ms: number, fn: () => void) => () => void,
    private kick: () => void,
  ) {}

  get running() {
    return this.cancel !== null;
  }

  start(loop: boolean, done?: () => void) {
    this.stop();
    this.i = 0;
    const next = () => {
      this.model.setGlyph(IGNITION[this.i % IGNITION.length], this.model.guest, false);
      this.kick();
      this.i++;
      if (!loop && this.i >= IGNITION.length) {
        this.cancel = null;
        done?.();
        return;
      }
      this.cancel = this.after(IGNITION_HOLD_MS, next);
    };
    next();
  }

  stop() {
    this.cancel?.();
    this.cancel = null;
  }
}
