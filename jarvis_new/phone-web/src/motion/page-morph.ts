/** Page element morph (PLAN.md §4.3). Pages never slide: mapped elements of
 * the outgoing page travel onto their partner's rect while the partner
 * travels in from the outgoing rect, crossfading, so each pair reads as one
 * object changing shape. Each pair has one normalised spring p ∈ [0, 1]
 * (0 = map side a, 1 = side b); unpaired elements share one page spring.
 * A trigger mid-morph only retargets the springs (no restarts, no jumps). */
import { clamp01, css, delta, IDENTITY, invert, lerpT, measure, sstep, type Pose, type T } from './flip';
import { PRESETS, Spring } from './spring';

export type PageId = 'gate' | 'core' | 'deck';
type PairKind = 'pose' | 'fadeTo';
type Unpaired = 'fade' | 'growY' | 'orbitIn';
export type PairSpec = { from: string; to: string; delay: number; kind?: PairKind };
export type MapSpec = {
  a: PageId;
  b: PageId;
  pairs: PairSpec[];
  inB: { key: string; kind: Unpaired }[];
  outA: { key: string; kind: Unpaired }[];
  centre?: string; // orbitIn pivot (a rest pose key)
};

export const SEGMENTS = [
  { angle: 324, tile: 'GUEST' },
  { angle: 36, tile: 'LOCK' },
  { angle: 108, tile: 'SCREENS' },
  { angle: 180, tile: 'POWER' },
  { angle: 252, tile: 'UNLOCK' },
] as const;
export const STAGGER_MS = 18;

export const GATE_CORE: MapSpec = {
  a: 'gate',
  b: 'core',
  pairs: [
    { from: 'gate.mark', to: 'core.mark', delay: 0 },
    { from: 'gate.decode', to: 'core.cmd', delay: 30 },
    { from: 'gate.glyphrow', to: 'strip', delay: 60, kind: 'fadeTo' },
  ],
  inB: [
    { key: 'core.ring', kind: 'fade' },
    { key: 'core.captions', kind: 'growY' },
    { key: 'core.nav', kind: 'fade' },
    ...SEGMENTS.map((s) => ({ key: `core.seg.${s.angle}`, kind: 'orbitIn' as const })),
  ],
  outA: [
    { key: 'gate.err', kind: 'fade' },
    { key: 'gate.fp', kind: 'fade' },
    { key: 'gate.pairbox', kind: 'fade' },
  ],
  centre: 'core.ring',
};

export const CORE_DECK: MapSpec = {
  a: 'core',
  b: 'deck',
  pairs: [
    { from: 'core.ring', to: 'deck.headring', delay: 0 },
    { from: 'core.mark', to: 'deck.mark', delay: 0 },
    { from: 'core.nav', to: 'deck.title', delay: 0 },
    // Segments detach clockwise from 324°, 18 ms apart.
    ...SEGMENTS.map((s, rank) => ({ from: `core.seg.${s.angle}`, to: `deck.tile.${s.tile}`, delay: STAGGER_MS * rank })),
    { from: 'core.captions', to: 'deck.ticker', delay: 20 },
    { from: 'core.cmd', to: 'deck.tile.MIC', delay: 40 },
  ],
  inB: [{ key: 'deck.stat', kind: 'fade' }],
  outA: [],
};

export const MAPS = [GATE_CORE, CORE_DECK];
export const SETTLE_SWITCH = 0.02;

export function sideValue(map: MapSpec, page: PageId): 0 | 1 | null {
  return page === map.a ? 0 : page === map.b ? 1 : null;
}

/** Transforms for one pose pair at p (pure; the unit-tested core). */
export function pairFrame(from: Pose, to: Pose, p: number) {
  const d: T = delta(from, to);
  return {
    inT: lerpT(d, IDENTITY, p),
    inO: clamp01((p - 0.15) / 0.35),
    outT: lerpT(IDENTITY, invert(d), p),
    outO: 1 - clamp01((p - 0.25) / 0.35),
    inLabel: sstep(0.55, 0.85, p),
    outLabel: 1 - sstep(0.1, 0.4, p),
  };
}

type El = HTMLElement;
export interface MorphEnv {
  el(key: string): El | null;
  page(id: PageId): El | null;
  after(ms: number, fn: () => void): () => void;
  run(task: (dt: number) => boolean): void;
  now(): number;
  measure?(el: El): Pose;
  decode?(el: El): void;
  focusFirst?(page: El): void;
  onSettle?(page: PageId): void;
}

export class PageMorph {
  active = 0;
  springs: Spring[];
  pageSpring: Spring;
  current: PageId;
  target: PageId;
  queued: PageId | null = null;
  rests = new Map<string, Pose>();
  private pending: (() => void)[] = [];
  private running = false;
  private lastP: number[] = [];
  private focused = false;
  reduced = false;
  private elCache = new Map<string, El | null>();
  private kids = new WeakMap<El, { labels: HTMLElement[]; fades: HTMLElement[] }>();

  /** Cached lookups: the DOM is stable between measures (no querying per frame). */
  private el(key: string): El | null {
    if (!this.elCache.has(key)) this.elCache.set(key, this.env.el(key));
    return this.elCache.get(key)!;
  }
  private children(el: El) {
    let c = this.kids.get(el);
    if (!c) {
      c = {
        labels: [...el.querySelectorAll<HTMLElement>('[data-label]')],
        fades: [...el.querySelectorAll<HTMLElement>('[data-stripfade]')],
      };
      this.kids.set(el, c);
    }
    return c;
  }
  /** Forget cached nodes (after a re-render that may add/remove children). */
  invalidate() {
    this.elCache.clear();
    this.kids = new WeakMap();
  }

  constructor(
    private env: MorphEnv,
    start: PageId = 'gate',
  ) {
    this.current = this.target = start;
    this.active = start === 'deck' ? 1 : 0;
    const v = sideValue(MAPS[this.active], start) ?? 1;
    this.springs = MAPS[this.active].pairs.map(() => new Spring(v, PRESETS.page));
    this.pageSpring = new Spring(v, PRESETS.page);
    this.lastP = this.springs.map(() => v);
  }

  get map() {
    return MAPS[this.active];
  }

  get moving() {
    return this.running;
  }

  private all() {
    return [...this.springs, this.pageSpring];
  }

  /** Re-read every rest pose. Only at rest; a morph defers it to settle. */
  measureAll() {
    if (this.running) return false;
    this.invalidate();
    const m = this.env.measure ?? measure;
    for (const map of MAPS) {
      const keys = [...map.pairs.flatMap((p) => [p.from, p.to]), ...map.inB.map((u) => u.key), ...map.outA.map((u) => u.key)];
      for (const k of keys) {
        const el = this.el(k);
        if (el) this.rests.set(k, m(el));
      }
    }
    return true;
  }

  /** Place everything for `page` without motion (boot, reduced motion). */
  snap(page: PageId) {
    this.cancelPending();
    this.queued = null;
    this.active = page === 'deck' ? 1 : page === 'gate' ? 0 : this.active;
    const map = this.map;
    const v = sideValue(map, page) ?? 1;
    this.springs = map.pairs.map(() => new Spring(v, PRESETS.page));
    this.pageSpring = new Spring(v, PRESETS.page);
    this.lastP = this.springs.map(() => v);
    // Write both maps at their resting value for this page (active map last,
    // since both maps share CORE's elements) so nothing keeps a stale pose.
    const order = this.active ? [0, 1] : [1, 0];
    for (const i of order) {
      const sv = sideValue(MAPS[i], page);
      const value = sv ?? (MAPS[i].a === 'gate' ? 1 : 0); // core side of the other map
      this.origins(MAPS[i]);
      this.write(MAPS[i], MAPS[i].pairs.map(() => value), value, true);
    }
    this.current = this.target = page;
    this.visibility(page, null);
    this.env.onSettle?.(page);
  }

  go(page: PageId) {
    if (!this.running) this.invalidate();
    if (page === this.target && !this.queued) {
      if (!this.running) return;
    }
    if (!this.running && sideValue(this.map, page) === null) {
      // At rest on CORE: switch to the map that holds both pages (no motion yet).
      const idx = MAPS.findIndex((m) => sideValue(m, page) !== null && sideValue(m, this.current) !== null);
      if (idx >= 0) {
        this.active = idx;
        const v0 = sideValue(this.map, this.current)!;
        this.springs = this.map.pairs.map(() => new Spring(v0, PRESETS.page));
        this.pageSpring = new Spring(v0, PRESETS.page);
        this.lastP = this.springs.map(() => v0);
      }
    }
    this.target = page;
    const v = sideValue(this.map, page);
    if (v !== null) {
      this.queued = null;
      this.retarget(v);
    } else {
      // Third page: run the active map back to CORE first, then switch maps.
      this.queued = page;
      this.retarget(sideValue(this.map, 'core')!);
    }
    this.focused = false;
    this.visibility(this.map.a, this.map.b);
    this.start();
  }

  private atRest() {
    return !this.pending.length && this.all().every((s) => s.settled);
  }

  private cancelPending() {
    for (const c of this.pending) c();
    this.pending = [];
  }

  private retarget(v: number) {
    const fromRest = this.atRest();
    this.cancelPending();
    this.map.pairs.forEach((pair, i) => {
      const s = this.springs[i];
      if (fromRest && pair.delay > 0) {
        const cancel = this.env.after(pair.delay, () => {
          this.pending = this.pending.filter((c) => c !== cancel);
          s.setTarget(v);
        });
        this.pending.push(cancel);
      } else s.setTarget(v);
    });
    this.pageSpring.setTarget(v);
    this.origins(this.map);
    for (const el of this.mapEls(this.map)) el.style.willChange = 'transform, opacity';
  }

  /** Pose pairs pivot on the centre; growY unfolds from the top edge. */
  private origins(map: MapSpec) {
    for (const el of this.mapEls(map)) el.style.transformOrigin = '50% 50%';
    for (const u of [...map.inB, ...map.outA]) {
      const el = this.el(u.key);
      if (el && u.kind === 'growY') el.style.transformOrigin = '50% 0';
    }
  }

  private start() {
    if (this.running) return;
    this.running = true;
    this.env.run(this.frame);
  }

  private mapEls(map: MapSpec) {
    const keys = [...map.pairs.flatMap((p) => [p.from, p.to]), ...map.inB.map((u) => u.key), ...map.outA.map((u) => u.key)];
    return keys.map((key) => this.el(key)).filter((e): e is El => !!e && e.dataset.morph !== 'strip');
  }

  frame = (dt: number): boolean => {
    for (const s of this.all()) s.advance(dt);
    const ps = this.springs.map((s) => s.x);
    this.write(this.map, ps, this.pageSpring.x, false);
    // focus moves to the target page once p passes the midpoint
    const tv = sideValue(this.map, this.queued ? 'core' : this.target);
    if (!this.focused && tv !== null && Math.abs(this.pageSpring.x - tv) < 0.5) {
      this.focused = true;
      const pg = this.env.page(this.queued ? 'core' : this.target);
      if (pg) this.env.focusFirst?.(pg);
    }
    if (this.queued) {
      const cv = sideValue(this.map, 'core')!;
      if (!this.pending.length && this.all().every((s) => Math.abs(s.x - cv) < SETTLE_SWITCH)) {
        this.write(this.map, this.springs.map(() => cv), cv, true);
        const next = this.queued;
        this.active = this.active ? 0 : 1;
        const start = sideValue(this.map, 'core')!;
        this.springs = this.map.pairs.map(() => new Spring(start, PRESETS.page));
        this.pageSpring = new Spring(start, PRESETS.page);
        this.lastP = this.springs.map(() => start);
        this.queued = null;
        this.retarget(sideValue(this.map, next)!);
        this.visibility(this.map.a, this.map.b);
        this.focused = false;
        return true;
      }
      return true;
    }
    if (this.atRest()) {
      this.running = false;
      for (const el of this.mapEls(this.map)) el.style.willChange = '';
      this.current = this.target;
      this.visibility(this.current, null);
      this.env.onSettle?.(this.current);
      return false;
    }
    return true;
  };

  private visibility(a: PageId, b: PageId | null) {
    for (const id of ['gate', 'core', 'deck'] as PageId[]) {
      const pg = this.env.page(id);
      if (!pg) continue;
      const shown = id === a || id === b;
      if (shown) pg.removeAttribute('data-hidden');
      else pg.setAttribute('data-hidden', '');
      const interactive = b === null ? shown : id === (this.queued ? 'core' : this.target);
      pg.inert = !interactive;
    }
  }

  private write(map: MapSpec, ps: number[], u: number, snap: boolean) {
    const rest = (k: string) => this.rests.get(k);
    map.pairs.forEach((pair, i) => {
      const p = ps[i];
      // Delayed and settled pairs need no writes while the other springs move.
      if (!snap && p === this.lastP[i]) return;
      const from = this.el(pair.from);
      const to = this.el(pair.to);
      const pf = rest(pair.from);
      const pt = rest(pair.to);
      if (!from || !to || !pf || !pt) return;
      const f = pairFrame(pf, pt, p);
      // Invisible partners keep their last transform until they reappear.
      // A reversal updates it before opacity becomes nonzero, so it stays continuous.
      if (snap || f.outO > 0) from.style.transform = css(f.outT, pf.rot);
      from.style.opacity = String(f.outO);
      if (pair.kind === 'fadeTo') {
        const o = String(sstep(0.5, 1, p));
        for (const x of this.children(to).fades) x.style.opacity = o;
      } else {
        if (snap || f.inO > 0) to.style.transform = css(f.inT, pt.rot);
        to.style.opacity = String(f.inO);
        const li = String(f.inLabel);
        const lo = String(f.outLabel);
        for (const x of this.children(to).labels) x.style.opacity = li;
        for (const x of this.children(from).labels) x.style.opacity = lo;
        if (!snap && this.env.decode) {
          const last = this.lastP[i] ?? p;
          if (last <= 0.55 && p > 0.55) this.env.decode(to);
          if (last >= 0.45 && p < 0.45) this.env.decode(from);
        }
      }
      this.lastP[i] = p;
    });
    const centre = map.centre ? rest(map.centre) : undefined;
    for (const { key, kind } of map.inB) this.unpaired(key, kind, u, centre);
    for (const { key, kind } of map.outA) this.unpaired(key, kind, 1 - u, centre);
  }

  private unpaired(key: string, kind: Unpaired, q: number, centre?: Pose) {
    const el = this.el(key);
    const r = this.rests.get(key);
    if (!el) return;
    const base = r?.rot ?? 0;
    if (kind === 'growY') {
      el.style.transform = `scaleY(${Math.round(q * 1000) / 1000})`;
      el.style.opacity = String(sstep(0.2, 0.8, q));
      return;
    }
    if (kind === 'orbitIn' && r && centre) {
      const th = (-30 * (1 - q) * Math.PI) / 180;
      const vx = r.cx - centre.cx;
      const vy = r.cy - centre.cy;
      const rx = vx * Math.cos(th) - vy * Math.sin(th);
      const ry = vx * Math.sin(th) + vy * Math.cos(th);
      el.style.transform = css({ tx: rx - vx, ty: ry - vy, sx: 1, sy: 1, r: (th * 180) / Math.PI }, base);
      el.style.opacity = String(sstep(0.4, 1, q));
      return;
    }
    const s = 0.9 + 0.1 * q;
    el.style.transform = css({ tx: 0, ty: 0, sx: s, sy: s, r: 0 }, base);
    el.style.opacity = String(sstep(0.4, 1, q));
  }
}
