'use client';

import s from '@/components/stark/stark.module.css';
import { css } from './color';
import { type Ease, clamp01, easings, smooth } from './ease';
import { type Anchor, type FxCtx, type Rect, SIGNATURES, mulberry32 } from './signatures';
import {
  type ColorSet,
  type Scalars,
  applyPalette,
  applyTypography,
  clearPalette,
  colorsOf,
  mixColors,
  mixScalars,
  scalarsOf,
  skinCss,
  syncParticles,
} from './skin';
import {
  type SwitchEvent,
  commitVisual,
  getCharacterState,
  onSwitch,
  prefersReduced,
  setEngineBusy,
  setVisualId,
} from './store';
import type { CharacterPublic, Motion, Range } from './types';

const MAX_DT = 34;
const DPR_CAP = 1.5;
const OFF_PX = 14; // staggered ease-out/in travel
const GROUPS = ['chrome', 'panels', 'text', 'orb'] as const;
type Group = (typeof GROUPS)[number];

type Seg = { t0: number; t1: number; from: number; to: number; ease: Ease };
type Shape = { sides: number; rings: number };

export type FrameStats = {
  frames: number;
  worst: number;
  over20: number;
  over34: number;
  total: number;
};

const rangeOf = (m: Motion, k: string): Range => m.phases[k] as Range;
const numOf = (m: Motion, k: string): number => m.phases[k] as number;

function shapeOf(c: CharacterPublic): Shape {
  const cut = Number(c.theme_tokens.cut_angle ?? 0);
  const edge = Number(c.theme_tokens.edge_refract ?? 0);
  return { sides: cut > 0 ? 6 : 0, rings: edge > 0 ? 2 : cut > 0 ? 4 : 3 };
}

/** Radius of a regular n-gon at angle th (0 sides = circle). */
function polyR(th: number, sides: number): number {
  if (sides < 3) return 1;
  const seg = (Math.PI * 2) / sides;
  const a = ((th % seg) + seg) % seg;
  return Math.cos(Math.PI / sides) / Math.cos(a - seg / 2);
}

export class CharacterEngine {
  private root: HTMLElement | null = null;
  private canvas: HTMLCanvasElement | null = null;
  private g: CanvasRenderingContext2D | null = null;
  private style: HTMLStyleElement | null = null;
  private raf = 0;
  private running = false;
  private lastNow = 0;
  private t = 0;
  private motion: Motion | null = null;
  private ease: Record<string, Ease> = {};
  private visual = 'jarvis';
  private toId = 'jarvis';
  private turn = 0;
  private cur: ColorSet | null = null;
  private curSc: Scalars | null = null;
  private fromCol: ColorSet | null = null;
  private fromSc: Scalars | null = null;
  private toCol: ColorSet | null = null;
  private toSc: Scalars | null = null;
  private vals: Record<Group, number> = { chrome: 1, panels: 1, text: 1, orb: 1 };
  private segs: Record<Group, Seg[]> = { chrome: [], panels: [], text: [], orb: [] };
  private swapAt = 0;
  private swapped = false;
  private sigStart = 0;
  private sigEnd = 0;
  private endAt = 0;
  private reduced = false;
  private skipSig = false;
  private fromShape: Shape = { sides: 0, rings: 3 };
  private toShape: Shape = { sides: 0, rings: 3 };
  private fx: FxCtx | null = null;
  private flashLog: number[] = [];
  private flip: {
    el: HTMLElement;
    dx: number;
    dy: number;
    sx: number;
    sy: number;
    free: boolean;
  }[] = [];
  private flipFirst = new Map<HTMLElement, DOMRect>();
  private flipPending = 0;
  private lastShake: [number, number] = [0, 0];
  private seed = 1;
  private paletteFrame = 0;
  stats: FrameStats = { frames: 0, worst: 0, over20: 0, over34: 0, total: 0 };
  readonly trace: number[] = [];
  private off: (() => void) | null = null;

  start(): () => void {
    this.off = onSwitch((e) => this.run(e));
    return () => this.stop();
  }

  stop() {
    this.off?.();
    this.off = null;
    cancelAnimationFrame(this.raf);
    this.running = false;
    this.canvas?.remove();
    this.style?.remove();
    this.canvas = null;
    this.style = null;
  }

  private findRoot(): HTMLElement | null {
    const el = document.querySelector<HTMLElement>(`.${s.root}`);
    if (el !== this.root) this.root = el;
    return this.root;
  }

  private ensureStyle() {
    if (this.style) return;
    this.style = document.createElement('style');
    this.style.dataset.cx = 'skins';
    this.style.textContent = skinCss(getCharacterState().motion?.layouts);
    document.head.appendChild(this.style);
  }

  private ensureCanvas() {
    if (!this.canvas) {
      const c = document.createElement('canvas');
      c.setAttribute('aria-hidden', 'true');
      c.style.cssText =
        'position:fixed;inset:0;width:100vw;height:100vh;pointer-events:none;z-index:90;display:none';
      document.body.appendChild(c);
      this.canvas = c;
      this.g = c.getContext('2d', { alpha: true });
    }
    const dpr = Math.min(window.devicePixelRatio || 1, DPR_CAP);
    const w = Math.round(window.innerWidth * dpr);
    const h = Math.round(window.innerHeight * dpr);
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
    this.g?.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  /* ------------------------------------------------------- groups */

  private els(group: Group): HTMLElement[] {
    const r = this.root;
    if (!r) return [];
    const q = (sel: string) => Array.from(r.querySelectorAll<HTMLElement>(sel));
    switch (group) {
      case 'chrome':
        return q(`.${s.top}, .${s.foot}`);
      case 'panels':
        return q(`.${s.left} > *, .${s.right} > *, .${s.soloGrid} > :not(.${s.center})`);
      case 'text':
        return q(`.${s.center} > :not(.${s.reactorWrap})`);
      case 'orb':
        return q(`.${s.reactorWrap}`);
    }
  }

  private rects(group: Group): Rect[] {
    return this.els(group)
      .map((e) => e.getBoundingClientRect())
      .filter((r) => r.width > 8 && r.height > 8)
      .map((r) => ({ x: r.left, y: r.top, w: r.width, h: r.height }));
  }

  private anchor(): Anchor {
    const el = this.root?.querySelector<HTMLElement>(`.${s.reactor}`);
    const r = el?.getBoundingClientRect();
    if (!r || r.width < 40) return { x: innerWidth / 2, y: innerHeight / 2, r: 110 };
    return {
      x: r.left + r.width / 2,
      y: r.top + r.height / 2,
      r: Math.min(r.width, r.height) * 0.36,
    };
  }

  /** FLIP targets outside the fade groups: the reactor glides to its new slot
   *  (uniform scale, so the orb never distorts). */
  private freeEls(): HTMLElement[] {
    const el = this.root?.querySelector<HTMLElement>(`.${s.reactor}`);
    return el ? [el] : [];
  }

  private paint() {
    for (const grp of GROUPS) {
      const v = this.vals[grp];
      const off = (1 - v) * OFF_PX;
      for (const el of this.els(grp)) {
        el.style.opacity = v >= 0.999 ? '' : String(+v.toFixed(3));
        const f = this.flip.find((x) => x.el === el);
        const k = f ? 1 - this.flipProgress() : 0;
        el.style.transformOrigin = '0 0';
        el.style.transform =
          v >= 0.999 && k <= 0.001
            ? ''
            : f && k > 0.001
              ? `translate3d(${f.dx * k}px,${f.dy * k + off}px,0) scale(${1 + (f.sx - 1) * k},${1 + (f.sy - 1) * k})`
              : `translate3d(0,${off}px,0)`;
        el.style.willChange = 'opacity, transform';
      }
    }
    const k = 1 - this.flipProgress();
    for (const f of this.flip) {
      if (!f.free) continue;
      f.el.style.transformOrigin = '0 0';
      f.el.style.willChange = 'transform';
      f.el.style.transform =
        k <= 0.001 ? '' : `translate3d(${f.dx * k}px,${f.dy * k}px,0) scale(${1 + (f.sx - 1) * k})`;
    }
  }

  private flipProgress(): number {
    const m = this.motion;
    if (!m) return 1;
    const [a, b] = rangeOf(m, 'in_panels');
    return this.ease.out_expo(smooth(a, b, this.t));
  }

  private clearInline() {
    for (const grp of GROUPS) {
      for (const el of this.els(grp)) {
        el.style.opacity = '';
        el.style.transform = '';
        el.style.transformOrigin = '';
        el.style.willChange = '';
      }
    }
    for (const el of this.freeEls()) {
      el.style.transform = '';
      el.style.transformOrigin = '';
      el.style.willChange = '';
    }
    if (this.root) this.root.style.transform = '';
  }

  /* ------------------------------------------------------- run */

  private run(ev: SwitchEvent) {
    const st = getCharacterState();
    const m = st.motion;
    const to = st.characters[ev.to];
    if (!m || !to) return;
    const root = this.findRoot();
    if (!root) {
      // No HUD mounted (taskbar orb, school bar): switch the skin silently.
      this.visual = ev.to;
      syncParticles(colorsOf(to), scalarsOf(to));
      setVisualId(ev.to);
      commitVisual(ev.to);
      return;
    }
    this.ensureStyle();
    this.motion = m;
    this.ease = easings(m);
    this.turn = ev.turn;
    const retarget = this.running;
    const from = st.characters[this.visual] ?? st.characters.jarvis;

    this.reduced = prefersReduced() || !st.effects;
    this.fromCol = this.cur ?? colorsOf(st.characters.jarvis);
    this.fromSc = this.curSc ?? scalarsOf(st.characters.jarvis);
    if (!this.cur) {
      this.cur = this.fromCol;
      this.curSc = this.fromSc;
    }
    this.toCol = colorsOf(to);
    this.toSc = scalarsOf(to);
    this.toId = ev.to;
    this.fromShape = shapeOf(from);
    this.toShape = shapeOf(to);
    this.seed = ev.turn * 7919 + 13;
    this.fx = null;
    this.skipSig = retarget && this.t > this.sigEnd - 200;
    this.flip = [];
    this.flipFirst.clear();
    this.flipPending = 0;
    this.swapped = false;

    this.t = 0;
    this.buildTimeline(m, retarget);
    if (this.reduced) this.ensureCanvasHidden();
    else this.ensureCanvas();
    root.setAttribute('data-cx', 'run');
    setEngineBusy(true);
    this.lastNow = performance.now();
    this.stats = { frames: 0, worst: 0, over20: 0, over34: 0, total: 0 };
    this.trace.length = 0;
    if (!this.running) {
      this.running = true;
      this.raf = requestAnimationFrame(this.tick);
    }
  }

  private ensureCanvasHidden() {
    if (this.canvas) this.canvas.style.display = 'none';
  }

  private buildTimeline(m: Motion, retarget: boolean) {
    const E = this.ease;
    const seg = (
      grp: Group,
      t0: number,
      t1: number,
      from: number,
      to: number,
      ease: Ease
    ): Seg => ({
      t0,
      t1,
      from,
      to,
      ease,
    });
    if (this.reduced) {
      const d = m.reduced_ms;
      this.swapAt = 0;
      this.sigStart = this.sigEnd = 0;
      this.endAt = d;
      for (const grp of GROUPS) {
        this.segs[grp] = [seg(grp, 0, d, Math.min(this.vals[grp], 0.4), 1, E.in_out_cubic)];
        this.vals[grp] = Math.min(this.vals[grp], 0.4);
      }
      return;
    }
    // Compress when the previous run was cut short: no dead air on a retarget.
    const k = this.skipSig ? 0.55 : 1;
    const sc = (x: number) => x * k;
    const outKeys: Record<Group, string> = {
      chrome: 'out_chrome',
      panels: 'out_panels',
      text: 'out_text',
      orb: 'out_orb',
    };
    const inKeys: Record<Group, string> = {
      chrome: 'in_chrome',
      panels: 'in_panels',
      text: 'in_text',
      orb: 'in_orb',
    };
    let swap = 0;
    for (const grp of GROUPS) {
      const [o0, o1] = rangeOf(m, outKeys[grp]);
      const [i0, i1] = rangeOf(m, inKeys[grp]);
      swap = Math.max(swap, sc(o1));
      const startV = this.vals[grp];
      const outDur = startV < 0.02 ? 0 : 1;
      this.segs[grp] = [
        seg(grp, sc(o0), sc(o1), startV, 0, E.in_out_cubic),
        seg(grp, sc(i0), sc(i1), 0, 1, E.out_expo),
      ];
      if (!outDur) this.segs[grp][0] = seg(grp, 0, 0.001, 0, 0, E.linear);
    }
    this.swapAt = swap;
    const [s0, s1] = rangeOf(m, 'signature');
    this.sigStart = this.skipSig ? swap : sc(s0);
    this.sigEnd = this.skipSig ? swap + 1 : sc(s1);
    this.endAt = sc(m.total_ms);
    void retarget;
  }

  private evalSegs(grp: Group): number {
    const list = this.segs[grp];
    const t = this.t;
    let v = this.vals[grp];
    for (const sg of list) {
      if (t < sg.t0) break;
      const u = clamp01((t - sg.t0) / Math.max(1, sg.t1 - sg.t0));
      v = sg.from + (sg.to - sg.from) * sg.ease(u);
    }
    return v;
  }

  private doSwap() {
    const root = this.root;
    const st = getCharacterState();
    const to = st.characters[this.toId];
    if (!root || !to) return;
    this.swapped = true;
    // FIRST: capture rects before the skin changes.
    for (const el of [...this.els('panels'), ...this.freeEls()]) {
      this.flipFirst.set(el, el.getBoundingClientRect());
    }
    if (this.toId === 'jarvis') root.removeAttribute('data-skin');
    else root.setAttribute('data-skin', this.toId);
    applyTypography(root, to);
    this.visual = this.toId;
    setVisualId(this.toId);
    commitVisual(this.toId);
    // LAST: two frames later, after React re-rendered brand and layout.
    this.flipPending = 2;
  }

  private measureFlip() {
    this.flip = [];
    for (const [el, first] of this.flipFirst) {
      if (!el.isConnected) continue;
      const last = el.getBoundingClientRect();
      if (last.width < 1 || first.width < 1) continue;
      // Remove our own transform from the measurement: it is identity-free here
      // because panels are at opacity 0 and transform-only offsets are small.
      const free = el.classList.contains(s.reactor);
      let dx = first.left - last.left;
      let dy = first.top - last.top;
      let sx = first.width / last.width;
      let sy = first.height / last.height;
      if (free) {
        // Uniform scale about the centre: translate the centres, scale by width.
        const u = sx;
        dx = first.left + first.width / 2 - (last.left + (last.width * u) / 2);
        dy = first.top + first.height / 2 - (last.top + (last.height * u) / 2);
        sx = sy = u;
      }
      if (Math.abs(dx) + Math.abs(dy) + Math.abs(sx - 1) + Math.abs(sy - 1) < 0.5) continue;
      this.flip.push({ el, dx, dy, sx, sy, free });
    }
    this.flipFirst.clear();
  }

  private flashBudget = (amp: number): number => {
    const now = this.t;
    this.flashLog = this.flashLog.filter((x) => now - x < 1000);
    const max = this.motion?.max_flash_hz ?? 3;
    if (this.flashLog.length >= max) return 0;
    // One flash event spans a few frames; log once per 90 ms window.
    const last = this.flashLog[this.flashLog.length - 1];
    if (last === undefined || now - last > 90) this.flashLog.push(now);
    return amp;
  };

  private buildFx(): FxCtx {
    const m = this.motion as Motion;
    const st = getCharacterState();
    const to = st.characters[this.toId];
    return {
      w: innerWidth,
      h: innerHeight,
      anchor: this.anchor(),
      panels: [...this.rects('panels'), ...this.rects('chrome')],
      colors: this.toCol as ColorSet,
      intensity: to?.intensity ?? 1,
      maxShake: m.max_shake_px,
      maxGlitch: m.max_glitch_alpha,
      rand: mulberry32(this.seed),
      flash: this.flashBudget,
      sec: 0,
      scratch: {},
    };
  }

  private tick = (now: number) => {
    const dt = Math.min(MAX_DT, Math.max(0, now - this.lastNow));
    const raw = now - this.lastNow;
    this.lastNow = now;
    this.t += dt;
    const sp = this.stats;
    sp.frames++;
    sp.total += raw;
    sp.worst = Math.max(sp.worst, raw);
    if (raw > 20) sp.over20++;
    if (raw > 34) sp.over34++;
    if (this.trace.length < 600) this.trace.push(Math.round(raw * 10) / 10);

    const root = this.root;
    const m = this.motion;
    if (
      !root ||
      !root.isConnected ||
      !m ||
      !this.toCol ||
      !this.fromCol ||
      !this.fromSc ||
      !this.toSc
    ) {
      this.finish(true);
      return;
    }
    const t = this.t;

    // palette cross-fade through design tokens (30 Hz: it restyles the HUD)
    const [p0, p1] = this.reduced ? [0, m.reduced_ms] : rangeOf(m, 'palette');
    const pk = this.ease.snap(smooth(p0, p1, t));
    if ((this.paletteFrame++ & 1) === 0 || pk >= 1) {
      this.cur = mixColors(this.fromCol, this.toCol, pk);
      this.curSc = mixScalars(this.fromSc, this.toSc, pk);
      applyPalette(root, this.cur, this.curSc);
    }

    if (!this.swapped && t >= this.swapAt) this.doSwap();
    if (this.flipPending > 0 && --this.flipPending === 0) this.measureFlip();

    for (const grp of GROUPS) this.vals[grp] = this.evalSegs(grp);

    // signature layer
    let shake: [number, number] = [0, 0];
    if (!this.reduced && this.g && this.canvas) {
      const g = this.g;
      const inBeat = t >= this.sigStart && t <= this.sigEnd && !this.skipSig;
      const morphA = rangeOf(m, 'out_orb')[0];
      const morphB = rangeOf(m, 'in_orb')[1];
      const inMorph = t >= morphA && t <= morphB;
      if (inBeat || inMorph) {
        this.canvas.style.display = 'block';
        g.clearRect(0, 0, innerWidth, innerHeight);
        if (!this.fx) this.fx = this.buildFx();
        const fx = this.fx;
        if (inMorph) this.drawMorph(g, fx, smooth(morphA, morphB, t));
        if (inBeat) {
          const to = getCharacterState().characters[this.toId];
          const sig = SIGNATURES[to?.signature ?? ''];
          const u = clamp01((t - this.sigStart) / Math.max(1, this.sigEnd - this.sigStart));
          fx.sec = (t - this.sigStart) / 1000;
          if (sig) {
            sig.draw(g, u, fx);
            shake = sig.shake?.(u, fx) ?? [0, 0];
            if (sig.reveal) {
              for (const grp of GROUPS) {
                const rs = this.rects(grp);
                if (!rs.length) continue;
                const r = rs[0];
                const rv = sig.reveal(r, u, fx);
                if (rv !== null) this.vals[grp] = Math.max(this.vals[grp], rv);
              }
            }
          }
        }
      } else if (this.canvas.style.display !== 'none') {
        g.clearRect(0, 0, innerWidth, innerHeight);
        this.canvas.style.display = 'none';
      }
    }

    this.paint();
    if (shake[0] !== 0 || shake[1] !== 0 || this.lastShake[0] !== 0 || this.lastShake[1] !== 0) {
      root.style.transform =
        shake[0] || shake[1] ? `translate3d(${shake[0]}px,${shake[1]}px,0)` : '';
    }
    this.lastShake = shake;

    if (t >= this.endAt) {
      this.finish(false);
      return;
    }
    this.raf = requestAnimationFrame(this.tick);
  };

  private drawMorph(g: CanvasRenderingContext2D, fx: FxCtx, u: number) {
    const a = fx.anchor;
    const k = this.ease.in_out_cubic(smooth(0.2, 0.62, u));
    const sides = k < 0.5 ? this.fromShape.sides : this.toShape.sides;
    const rings = Math.round(
      this.fromShape.rings + (this.toShape.rings - this.fromShape.rings) * k
    );
    const blend = (th: number) => {
      const r0 = polyR(th, this.fromShape.sides);
      const r1 = polyR(th, this.toShape.sides);
      return r0 + (r1 - r0) * k;
    };
    void sides;
    const col = this.fromCol && this.toCol ? mixColors(this.fromCol, this.toCol, k) : fx.colors;
    const al = Math.sin(clamp01(u) * Math.PI) * 0.85;
    const pulse = 1 - 0.08 * Math.sin(clamp01(u) * Math.PI);
    g.save();
    g.lineWidth = 1.6;
    for (let i = 0; i < rings; i++) {
      const base = a.r * (0.62 + i * 0.22) * pulse;
      g.strokeStyle = css(col.accent, al * (0.95 - i * 0.18));
      g.beginPath();
      const N = 96;
      const rot = u * (i % 2 ? -1 : 1) * 0.9;
      for (let n = 0; n <= N; n++) {
        const th = (n / N) * Math.PI * 2;
        const r = base * blend(th + rot * 0);
        const x = a.x + Math.cos(th + rot) * r;
        const y = a.y + Math.sin(th + rot) * r;
        if (n === 0) g.moveTo(x, y);
        else g.lineTo(x, y);
      }
      g.closePath();
      g.stroke();
    }
    g.restore();
  }

  private finish(aborted: boolean) {
    this.running = false;
    setEngineBusy(false);
    cancelAnimationFrame(this.raf);
    const root = this.root;
    const m = this.motion;
    const st = getCharacterState();
    const to = st.characters[this.toId];
    if (this.canvas) {
      this.g?.clearRect(0, 0, innerWidth, innerHeight);
      this.canvas.style.display = 'none';
    }
    for (const grp of GROUPS) this.vals[grp] = 1;
    this.flip = [];
    if (root && root.isConnected && !aborted) {
      this.clearInline();
      if (!this.swapped) this.doSwap();
      if (this.toId === 'jarvis') {
        clearPalette(root);
        if (this.toCol && this.toSc) syncParticles(this.toCol, this.toSc);
      } else if (this.toCol && this.toSc) applyPalette(root, this.toCol, this.toSc);
      root.removeAttribute('data-cx');
      this.cur = this.toCol;
      this.curSc = this.toSc;
    } else if (root) {
      this.clearInline();
      root.removeAttribute('data-cx');
    }
    if (to && !aborted) {
      this.visual = this.toId;
      setVisualId(this.toId);
      commitVisual(this.toId);
    }
    void m;
  }
}

/** Test and recording hook: window.__cx.stats / .trace after a switch. */
export function exposeEngine(e: CharacterEngine) {
  try {
    (window as unknown as { __cx?: CharacterEngine }).__cx = e;
  } catch {
    /* non-browser */
  }
}
