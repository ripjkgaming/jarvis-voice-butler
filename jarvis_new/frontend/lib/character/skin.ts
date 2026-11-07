import g from '@/components/ascii/glyphs.module.css';
import s from '@/components/stark/stark.module.css';
import { type RGBA, css, lerpColor, parseColor } from './color';
import { setParticleSkin } from './particle-skin';
import type { CharacterPublic, LayoutSpec } from './types';

/** Design-token key -> CSS custom property on the HUD root. */
const COLOR_VARS: Record<string, string> = {
  void: '--void',
  ink: '--ink',
  deep: '--deep',
  raise: '--raise',
  line: '--line',
  line_strong: '--line-strong',
  accent: '--cyan',
  accent_hot: '--cyan-hot',
  accent_dim: '--cyan-dim',
  text: '--text',
  text_dim: '--text-dim',
  text_faint: '--text-faint',
  warn: '--amber',
  danger: '--red',
  ok: '--green',
};
export const COLOR_KEYS = Object.keys(COLOR_VARS);

/** Tokens the stark CSS reads as "r, g, b" so alpha variants follow the skin. */
const RGB_VARS: Record<string, string> = {
  accent: '--acc',
  deep: '--deep-rgb',
  ink: '--ink-rgb',
  warn: '--warn-rgb',
};

export type ColorSet = Record<string, RGBA>;
export type Scalars = {
  glow: number;
  scan: number;
  edge: number;
  radius: number;
  cut: number;
  pcount: number;
  psize: number;
  pdrift: number;
};

const SANS = `'Inter', 'Segoe UI', 'Noto Sans', system-ui, sans-serif`;

export function colorsOf(c: CharacterPublic): ColorSet {
  const out: ColorSet = {};
  for (const k of COLOR_KEYS) out[k] = parseColor(String(c.theme_tokens[k] ?? '#000'));
  return out;
}

export function scalarsOf(c: CharacterPublic): Scalars {
  const t = c.theme_tokens;
  return {
    glow: Number(t.glow ?? 1),
    scan: Number(t.scan ?? 0),
    edge: Number(t.edge_refract ?? 0),
    radius: Number(t.radius_panel ?? 10),
    cut: Number(t.cut_angle ?? 0),
    pcount: Number(t.particle_count ?? 1),
    psize: Number(t.particle_size ?? 1),
    pdrift: Number(t.particle_drift ?? 1),
  };
}

export function mixColors(a: ColorSet, b: ColorSet, t: number): ColorSet {
  const out: ColorSet = {};
  for (const k of COLOR_KEYS) out[k] = lerpColor(a[k], b[k], t);
  return out;
}

export function mixScalars(a: Scalars, b: Scalars, t: number): Scalars {
  const m = (x: number, y: number) => x + (y - x) * t;
  return {
    glow: m(a.glow, b.glow),
    scan: m(a.scan, b.scan),
    edge: m(a.edge, b.edge),
    radius: m(a.radius, b.radius),
    cut: m(a.cut, b.cut),
    pcount: m(a.pcount, b.pcount),
    psize: m(a.psize, b.psize),
    pdrift: m(a.pdrift, b.pdrift),
  };
}

/** Writes one frame of palette to the HUD root (custom properties only). */
export function applyPalette(root: HTMLElement, col: ColorSet, sc: Scalars) {
  const st = root.style;
  for (const k of COLOR_KEYS) st.setProperty(COLOR_VARS[k], css(col[k]));
  for (const [k, v] of Object.entries(RGB_VARS)) {
    const c = col[k];
    st.setProperty(v, `${Math.round(c[0])}, ${Math.round(c[1])}, ${Math.round(c[2])}`);
  }
  const a = col.accent;
  st.setProperty('--glow', `0 0 10px ${css(a, 0.45 * sc.glow)}`);
  st.setProperty('--cx-scan', String(+sc.scan.toFixed(3)));
  st.setProperty('--cx-edge', String(+sc.edge.toFixed(3)));
  st.setProperty('--cx-radius', `${sc.radius.toFixed(1)}px`);
  st.setProperty('--cx-cut', `${sc.cut.toFixed(1)}px`);
  st.setProperty('--cx-pcount', String(+sc.pcount.toFixed(3)));
  st.setProperty('--cx-psize', String(+sc.psize.toFixed(3)));
  st.setProperty('--cx-pdrift', String(+sc.pdrift.toFixed(3)));
  syncParticles(col, sc);
}

/** Pushes the skin's particle look to the shared particle state. */
export function syncParticles(col: ColorSet, sc: Scalars) {
  setParticleSkin(col.accent, col.accent_hot, col.ink, {
    count: sc.pcount,
    size: sc.psize,
    drift: sc.pdrift,
    glow: sc.glow,
    edge: sc.edge,
  });
}

const VARS = [
  ...Object.values(COLOR_VARS),
  ...Object.values(RGB_VARS),
  '--glow',
  '--cx-scan',
  '--cx-edge',
  '--cx-radius',
  '--cx-cut',
  '--cx-pcount',
  '--cx-psize',
  '--cx-pdrift',
  '--f-label',
  '--f-brand',
];

/** Back to the stock stylesheet: Jarvis wears no overrides at rest. */
export function clearPalette(root: HTMLElement) {
  for (const v of VARS) root.style.removeProperty(v);
}

export function applyTypography(root: HTMLElement, c: CharacterPublic) {
  const t = c.theme_tokens;
  const font = (v: unknown) => (v === 'sans' ? SANS : null);
  const label = font(t.font_label);
  const brand = font(t.font_brand);
  if (label) root.style.setProperty('--f-label', label);
  else root.style.removeProperty('--f-label');
  if (brand) root.style.setProperty('--f-brand', brand);
  else root.style.removeProperty('--f-brand');
}

/** Layout and surface rules that tokens alone cannot express. Built from the
 *  real (hashed) module class names; every rule needs a data-skin, so Jarvis
 *  matches none of them. */
export function skinCss(layouts?: Record<string, LayoutSpec>): string {
  const r = `.${s.root}:global(.hud)`;
  const root = (id: string) => `.${s.root}[data-skin='${id}']`;
  const U = root('ultron');
  const F = root('friday');
  void r;
  return `
${U} .${s.frame}, ${U} .${g.box} {
  clip-path: polygon(var(--cx-cut) 0, 100% 0, 100% calc(100% - var(--cx-cut)),
    calc(100% - var(--cx-cut)) 100%, 0 100%, 0 var(--cx-cut));
}
${U} .${s.top} {
  background: none;
  isolation: isolate;
}
${U} .${s.top}::before {
  content: ''; position: absolute; inset: 0; z-index: -1; pointer-events: none;
  background: linear-gradient(180deg, rgba(var(--deep-rgb), 0.96), rgba(var(--ink-rgb), 0.7));
  clip-path: polygon(0 0, 100% 0, 100% calc(100% - 10px), calc(100% - 28px) 100%, 28px 100%, 0 calc(100% - 10px));
}
${U} .${s.backdrop} {
  background:
    radial-gradient(ellipse 105% 100% at 50% 48%, transparent 58%, rgba(0,0,0,0.94) 100%),
    radial-gradient(ellipse 52% 62% at 50% 47%, rgba(var(--acc), 0.16), transparent 72%),
    repeating-linear-gradient(0deg, rgba(var(--acc), 0.05) 0 1px, transparent 1px 36px),
    repeating-linear-gradient(90deg, rgba(var(--acc), 0.05) 0 1px, transparent 1px 36px),
    var(--ink);
}
${U}::after {
  content: ''; position: absolute; inset: 0; z-index: 40; pointer-events: none;
  background: repeating-linear-gradient(0deg, rgba(0,0,0,0.5) 0 1px, transparent 1px 3px);
  opacity: calc(var(--cx-scan) * 0.5); mix-blend-mode: multiply;
}
${U} .${s.chip} { text-transform: uppercase; }
${U} .${s.wordmark}, ${U} .${s.readoutWord}, ${U} .${s.frameLabel}, ${U} .${g.title} {
  text-shadow: 1.5px 0 rgba(255,0,40,0.75), -1.5px 0 rgba(0,225,255,0.45), 0 0 14px rgba(var(--acc), 0.7);
}
${U} .${s.rxEnergy} {
  filter: drop-shadow(2px 0 0 rgba(255,0,40,0.5)) drop-shadow(-2px 0 0 rgba(0,225,255,0.28)) drop-shadow(0 0 18px rgba(var(--acc), 0.55));
}
${U} .${s.backdrop}::after {
  content: ''; position: absolute; inset: 0; pointer-events: none;
  background: radial-gradient(ellipse at 50% 50%, transparent 40%, rgba(var(--acc), 0.1) 100%),
    repeating-linear-gradient(0deg, transparent 0 2px, rgba(var(--acc), 0.06) 2px 3px);
  mix-blend-mode: screen;
}
@media (prefers-reduced-motion: no-preference) {
  ${U}::before {
    content: ''; position: absolute; left: 0; right: 0; top: -20%; height: 18%; z-index: 41;
    pointer-events: none; background: linear-gradient(180deg, transparent, rgba(var(--acc), 0.1), transparent);
    animation: cx-roll 6.5s linear infinite;
  }
  ${U} .${s.grid} { animation: cx-flicker 4.3s steps(1) infinite; }
  ${U} .${s.frame}, ${U} .${g.box} { animation: cx-glitch 7.1s steps(1) infinite; }
  ${U} .${s.right} > .${s.frame}:nth-child(2n), ${U} .${s.left} > .${s.frame}:nth-child(2n) { animation-delay: -2.4s; }
  ${U} .${s.right} > .${s.frame}:nth-child(3n) { animation-delay: -4.9s; }
  ${U} .${s.wordmark} { animation: cx-tear 5.3s steps(1) infinite; }
  @keyframes cx-roll { to { top: 105%; } }
  @keyframes cx-flicker {
    0%, 100% { opacity: 1; } 91% { opacity: 0.86; } 92% { opacity: 1; } 96% { opacity: 0.92; } 97% { opacity: 1; }
  }
  @keyframes cx-glitch {
    0%, 93%, 100% { transform: none; filter: none; }
    94% { transform: translate(-3px, 0); filter: drop-shadow(3px 0 0 rgba(255,0,40,0.55)) drop-shadow(-3px 0 0 rgba(0,225,255,0.35)); }
    96% { transform: translate(4px, 1px); filter: drop-shadow(-3px 0 0 rgba(255,0,40,0.55)); }
    98% { transform: translate(-2px, -1px); filter: none; }
  }
  @keyframes cx-tear {
    0%, 90%, 100% { transform: none; clip-path: none; }
    92% { transform: translateX(-5px) skewX(-6deg); }
    94% { transform: translateX(5px); clip-path: inset(40% 0 20% 0); }
    96% { transform: translateX(-2px); }
  }
}

${F} .${s.frame} {
  border-radius: var(--cx-radius);
  background: linear-gradient(160deg, rgba(255,255,255,0.78), rgba(255,255,255,0.46));
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.95), inset 0 0 0 1px rgba(var(--acc), 0.18),
    0 12px 34px rgba(var(--acc), 0.12);
  backdrop-filter: blur(14px) saturate(1.25);
}
${F} .${s.rxEnergy} { filter: saturate(1.9) brightness(0.62) contrast(1.35); }
${F} .${s.frame} > .${s.outline}, ${F} .${s.top}::after { display: none; }
${F} .${g.edge}, ${F} .${g.foot}, ${F} .${g.fill}, ${F} .${g.fillShort} { opacity: 0; }
${F} .${s.top} {
  background: linear-gradient(180deg, rgba(255,255,255,0.86), rgba(255,255,255,0.5));
  box-shadow: 0 1px 0 rgba(var(--acc), 0.2), 0 8px 24px rgba(var(--acc), 0.08);
  backdrop-filter: blur(12px);
}
${F} .${s.backdrop} {
  background:
    radial-gradient(ellipse 60% 70% at 50% 40%, rgba(var(--acc), 0.1), transparent 70%),
    linear-gradient(180deg, var(--void), var(--ink));
}
${F} .${s.chip} { border-radius: 999px; padding: 0 10px; background: rgba(var(--acc), 0.1); }
${F} .${s.chip}::before, ${F} .${s.chip}::after { content: none; }
${layoutCss(layouts)}
`;
}

/** Grid-template / ordering presets per layout (characters/_motion.yaml
 *  `layouts`). The same panels sit in new grid slots; no component forks.
 *  Jarvis (hud_round) is the stock stylesheet and gets no rules. */
export function layoutCss(layouts?: Record<string, LayoutSpec>): string {
  if (!layouts) return '';
  const R = (id: string) => `.${s.root}[data-skin='${id}']`;
  const out: string[] = [];
  const ang = layouts.hud_angular;
  if (ang?.columns) {
    const [l, c, r] = ang.columns;
    const U = R('ultron');
    // Mirrored: operations slabs left, wide reactor centre, vitals tight right.
    out.push(`
${U} { --top-h: ${ang.top_h}px; --rx-scale: ${ang.reactor_scale}; --slab: 20px; }
${U} .${s.grid} {
  grid-template-columns: minmax(250px, ${r}fr) minmax(0, ${c}fr) minmax(190px, ${l}fr);
  grid-template-rows: minmax(0, 1fr);
  gap: ${ang.gap}px; padding: ${ang.gap}px ${ang.gap + 4}px;
}
${U} .${s.right} { grid-column: 1; grid-row: 1; gap: ${Math.round(ang.gap / 2)}px; }
${U} .${s.center} { grid-column: 2; grid-row: 1; }
${U} .${s.left} { grid-column: 3; grid-row: 1; gap: ${Math.round(ang.gap / 2)}px; }
${U} .${s.left} > * { padding-block: 0; }
${U} .${s.right} > .${s.frame}:nth-child(odd) {
  clip-path: polygon(0 0, calc(100% - var(--slab)) 0, 100% var(--slab), 100% 100%, var(--slab) 100%, 0 calc(100% - var(--slab)));
}
${U} .${s.right} > .${s.frame}:nth-child(even) {
  margin-inline-start: var(--slab);
  clip-path: polygon(var(--slab) 0, 100% 0, 100% calc(100% - var(--slab)), calc(100% - var(--slab)) 100%, 0 100%, 0 var(--slab));
}
${U} .${s.reactor} { width: calc(min(100cqw, 146.6cqh) * var(--rx-scale)); max-width: 100cqw; }
`);
  }
  const gl = layouts.hud_glass;
  if (gl?.cards) {
    const F = R('friday');
    // Summary strip on top, small raised reactor, panels as a card grid.
    out.push(`
${F} { --top-h: ${gl.top_h}px; --rx-scale: ${gl.reactor_scale}; }
${F} .${s.grid} {
  grid-template-columns: repeat(${gl.cards}, minmax(0, 1fr));
  grid-template-rows: auto minmax(0, 1.3fr) repeat(2, minmax(0, 1fr));
  gap: ${gl.gap}px; padding: ${gl.gap}px ${gl.gap + 6}px;
}
${F} .${s.summary} {
  display: grid; grid-column: 1 / -1; grid-row: 1;
  grid-template-columns: repeat(${gl.cards}, minmax(0, 1fr)); gap: ${gl.gap}px;
}
${F} .${s.summaryCard} {
  border-radius: var(--cx-radius);
  background: linear-gradient(160deg, rgba(255,255,255,0.8), rgba(255,255,255,0.5));
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.95), inset 0 0 0 1px rgba(var(--acc), 0.18);
}
${F} .${s.left}, ${F} .${s.right} { display: contents; }
${F} .${s.center} { grid-column: 1 / -1; grid-row: 2; }
${F} .${s.reactor} { width: calc(min(100cqw, 146.6cqh) * var(--rx-scale)); }
${F} .${s.left} > *, ${F} .${s.right} > * { min-width: 0; min-height: 0; overflow: hidden; }
${F} .${s.right} > .${s.panels} { display: contents; }
${F} .${s.right} > :nth-child(3) { grid-column: span 2; }
`);
  }
  return out.join('');
}
