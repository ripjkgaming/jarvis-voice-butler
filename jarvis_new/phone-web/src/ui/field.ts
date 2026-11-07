/** Phone ASCII reactor, using the shared HUD glyph atlas.
 * Rendered in a worker on an OffscreenCanvas so the page's animation frames
 * stay inside the morph budget; 30 fps idle, 60 fps during a live call,
 * nothing while hidden. Browsers without OffscreenCanvas draw on the page. */
import { glyphAtlas, prefersReducedMotion, type GlyphAtlas } from '@hud/ascii';
import { effect } from '@preact/signals';
import fontUrl from '../fonts/commit-700.woff2?url';
import { loop } from '../motion/loop';
import { call, guestOn } from '../store';
import { cellsFor, FieldDynamics, paint, SPEED, type Cells, type FieldState } from './field-render';
import FieldWorker from './field-worker.ts?worker';

const offscreen = typeof OffscreenCanvas !== 'undefined' && 'transferControlToOffscreen' in HTMLCanvasElement.prototype;
let worker: Worker | null = null;
const ids = new WeakMap<HTMLCanvasElement, string>();
const els = new Map<string, { el: HTMLCanvasElement; cw: number; ch: number }>();
let seq = 0;

function state(): FieldState {
  const c = call.value;
  const live = c.phase === 'live';
  return {
    live,
    agent: live && !c.muted ? c.agent : 'idle',
    reduced: prefersReducedMotion(),
    accent: guestOn.value ? '#ffb347' : '#5fe3ff',
    speed: c.muted ? 0.35 : (SPEED[live ? c.agent : 'idle'] ?? 1),
    boost: live && c.agent === 'speaking' ? 0.25 + c.playRms * 1.6 : live && c.agent === 'listening' ? 0.15 + c.micRms : 0,
  };
}

// ------------------------------------------------ main-thread fallback
type Local = { el: HTMLCanvasElement; ctx: CanvasRenderingContext2D; cw: number; ch: number; cells: Cells | null; atlas: GlyphAtlas | null; accent: string };
const locals = new Map<string, Local>();
const dynamics = new FieldDynamics();
let visible = new Set<string>();
let running = false;
let acc = 0;
function sizeLocal(s: Local) {
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const w = Math.max(1, s.el.offsetWidth);
  const h = Math.max(1, s.el.offsetHeight);
  s.el.width = Math.round(w * dpr);
  s.el.height = Math.round(h * dpr);
  s.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  s.cells = cellsFor(w, h, s.cw, s.ch);
  s.atlas = glyphAtlas(s.cw, s.ch, dpr, s.accent);
}
function tickLocal(dt: number) {
  if (!visible.size || document.hidden) return (running = false);
  const st = state();
  acc += dt;
  if (!st.reduced && acc < 1 / (st.live ? 60 : 30) - 0.002) return true;
  const frame = dynamics.step(acc, st);
  acc = 0;
  for (const [id, s] of locals) {
    if (!visible.has(id) || !s.cells || !s.atlas) continue;
    if (s.accent !== st.accent) {
      s.accent = st.accent;
      s.atlas = glyphAtlas(s.cw, s.ch, Math.min(2, window.devicePixelRatio || 1), s.accent);
    }
    paint(s.ctx, s.atlas, s.cells, frame);
  }
  if (st.reduced) return (running = false);
  return true;
}

function ensureWorker() {
  if (worker) return worker;
  worker = new FieldWorker();
  worker.postMessage({ type: 'font', url: new URL(fontUrl, location.href).href });
  let lastPost = 0;
  let lastState: FieldState | null = null;
  effect(() => {
    const st = state();
    const now = performance.now();
    const semantic = !lastState || st.agent !== lastState.agent || st.live !== lastState.live
      || st.accent !== lastState.accent || st.reduced !== lastState.reduced;
    if (now - lastPost < 40 && st.live && !semantic) return; // throttle levels, never state changes
    lastPost = now;
    lastState = st;
    worker!.postMessage({ type: 'state', state: st });
  });
  return worker;
}

export const field = {
  attach(el: HTMLCanvasElement, cw: number, ch: number) {
    const id = `f${seq++}`;
    ids.set(el, id);
    els.set(id, { el, cw, ch });
    if (offscreen) {
      const w = ensureWorker();
      const canvas = el.transferControlToOffscreen();
      w.postMessage(
        { type: 'attach', id, canvas, cw, ch, w: el.offsetWidth, h: el.offsetHeight, dpr: Math.min(2, window.devicePixelRatio || 1) },
        [canvas],
      );
    } else {
      const ctx = el.getContext('2d');
      if (ctx) {
        const s: Local = { el, ctx, cw, ch, cells: null, atlas: null, accent: state().accent };
        locals.set(id, s);
        sizeLocal(s);
      }
    }
    return () => {
      els.delete(id);
      locals.delete(id);
    };
  },
  show(canvases: HTMLCanvasElement[]) {
    const next = canvases.map((c) => ids.get(c)).filter((x): x is string => !!x);
    if (next.some((id) => !visible.has(id))) dynamics.pulse(0.6);
    visible = new Set(document.hidden ? [] : next);
    if (worker) worker.postMessage({ type: 'show', ids: [...visible] });
    else this.kick();
  },
  resize() {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    for (const [id, { el }] of els) {
      if (worker) worker.postMessage({ type: 'resize', id, w: el.offsetWidth, h: el.offsetHeight, dpr });
      const s = locals.get(id);
      if (s) sizeLocal(s);
    }
  },
  refresh() {
    worker?.postMessage({ type: 'state', state: state() });
    this.kick();
  },
  ignite() {
    dynamics.ignite();
    worker?.postMessage({ type: 'ignite' });
    this.kick();
  },
  kick() {
    if (worker || running || !visible.size) return;
    running = true;
    loop.add(tickLocal);
  },
};
document.addEventListener('visibilitychange', () => {
  const all = [...els.values()].map((x) => x.el).filter((el) => !el.closest('[data-hidden]'));
  field.show(all);
});

// Media preferences are not signals; propagate a runtime change explicitly.
matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', () => field.refresh());
if (!offscreen) effect(() => { state(); field.kick(); });
