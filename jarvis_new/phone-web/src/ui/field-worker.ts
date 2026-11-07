/** Off-main-thread ASCII reactor field (OffscreenCanvas). The page only posts
 * state; this worker owns its own animation frames, so the field never costs
 * the morph frame budget. Reuses the shared engine (frontend/lib/ascii.ts):
 * glyphAtlas expects a DOM canvas, so a 3-line shim hands it OffscreenCanvas. */
import { glyphAtlas, type GlyphAtlas } from '@hud/ascii';
import { cellsFor, FieldDynamics, paint, type Cells, type FieldState } from './field-render';

const g = globalThis as unknown as Record<string, unknown> & {
  fonts: FontFaceSet;
  requestAnimationFrame(cb: (t: number) => void): number;
  onmessage: ((e: MessageEvent) => void) | null;
};
g.document = { createElement: () => new OffscreenCanvas(1, 1), documentElement: {} };
g.getComputedStyle = () => ({ getPropertyValue: () => "'Commit Mono'" });

type S = { canvas: OffscreenCanvas; ctx: OffscreenCanvasRenderingContext2D; cw: number; ch: number; dpr: number; cells: Cells | null; atlas: GlyphAtlas | null; accent: string };
const surfaces = new Map<string, S>();
let visible = new Set<string>();
let st: FieldState = { speed: 1, boost: 0, live: false, accent: '#5fe3ff', reduced: false };
const dynamics = new FieldDynamics();
let last = 0;
let acc = 0;
let running = false;
let fontReady: Promise<unknown> = Promise.resolve();

function build(s: S, w: number, h: number) {
  s.canvas.width = Math.max(1, Math.round(w * s.dpr));
  s.canvas.height = Math.max(1, Math.round(h * s.dpr));
  s.ctx.setTransform(s.dpr, 0, 0, s.dpr, 0, 0);
  s.cells = cellsFor(w, h, s.cw, s.ch);
  s.atlas = glyphAtlas(s.cw, s.ch, s.dpr, st.accent);
  s.accent = st.accent;
}

function tick(t: number) {
  if (!visible.size) {
    running = false;
    return;
  }
  const dt = last ? (t - last) / 1000 : 0;
  last = t;
  acc += dt;
  const fps = st.live ? 60 : 30;
  if (st.reduced || acc >= 1 / fps - 0.002) {
    const step = Math.min(acc, 0.25);
    acc = 0;
    const frame = dynamics.step(step, st);
    for (const [id, s] of surfaces) {
      if (!visible.has(id) || !s.cells) continue;
      if (s.accent !== st.accent) {
        s.atlas = glyphAtlas(s.cw, s.ch, s.dpr, st.accent);
        s.accent = st.accent;
      }
      if (s.atlas) paint(s.ctx, s.atlas, s.cells, frame);
    }
  }
  if (st.reduced) { running = false; return; }
  g.requestAnimationFrame(tick);
}

function kick() {
  if (running || !visible.size) return;
  running = true;
  last = 0;
  g.requestAnimationFrame(tick);
}

g.onmessage = (e: MessageEvent) => {
  const m = e.data;
  if (m.type === 'font') {
    const face = new FontFace('Commit Mono', `url(${m.url})`, { weight: '700' });
    g.fonts.add(face);
    fontReady = face.load().catch(() => {});
  } else if (m.type === 'attach') {
    const ctx = (m.canvas as OffscreenCanvas).getContext('2d')!;
    const s: S = { canvas: m.canvas, ctx, cw: m.cw, ch: m.ch, dpr: m.dpr, cells: null, atlas: null, accent: st.accent };
    surfaces.set(m.id, s);
    void fontReady.then(() => { build(s, m.w, m.h); kick(); });
  } else if (m.type === 'resize') {
    const s = surfaces.get(m.id);
    if (s) {
      s.dpr = m.dpr;
      void fontReady.then(() => { build(s, m.w, m.h); kick(); });
    }
  } else if (m.type === 'show') {
    if (m.ids.some((id: string) => !visible.has(id))) dynamics.pulse(0.6);
    visible = new Set(m.ids);
    kick();
  } else if (m.type === 'state') {
    if (st.agent !== m.state.agent || st.live !== m.state.live) dynamics.pulse(0.9);
    st = m.state;
    kick();
  } else if (m.type === 'ignite') {
    dynamics.ignite();
    kick();
  }
};
