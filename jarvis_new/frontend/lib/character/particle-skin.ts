import type { RGBA } from './color';
import type { Motion } from './types';

/** Live particle look. The engine writes it from the same design tokens it
 *  writes to the HUD root (--cyan, --acc, --cx-p*), so ambient particles,
 *  glyph wipes and the ASCII field follow the skin and cross-fade with it.
 *  One shared mutable object: readers poll `version`, nothing allocates. */
export type ParticleSkin = {
  rgb: [number, number, number];
  hot: [number, number, number];
  ink: [number, number, number];
  /** Draw alpha: light skins get softer particles. */
  alpha: number;
  count: number;
  size: number;
  drift: number;
  /** Glow layer inputs, from the same skin tokens (glow, edge_refract). */
  glow: number;
  edge: number;
  version: number;
};

/** Jarvis values before the character payload arrives (match jarvis.yaml). */
export const particleSkin: ParticleSkin = {
  rgb: [168, 235, 255],
  hot: [244, 253, 255],
  ink: [3, 16, 26],
  alpha: 1,
  count: 1,
  size: 1,
  drift: 1,
  glow: 1,
  edge: 0,
  version: 0,
};

export type ParticleParams = {
  count: number;
  size: number;
  drift: number;
  glow: number;
  edge: number;
};

const LIGHT_ALPHA = 0.5;

export function setParticleSkin(accent: RGBA, hot: RGBA, ink: RGBA, p: ParticleParams) {
  const k = particleSkin;
  k.rgb[0] = accent[0];
  k.rgb[1] = accent[1];
  k.rgb[2] = accent[2];
  k.hot[0] = hot[0];
  k.hot[1] = hot[1];
  k.hot[2] = hot[2];
  k.ink[0] = ink[0];
  k.ink[1] = ink[1];
  k.ink[2] = ink[2];
  k.alpha = 0.299 * ink[0] + 0.587 * ink[1] + 0.114 * ink[2] > 128 ? LIGHT_ALPHA : 1;
  k.count = p.count;
  k.size = p.size;
  k.drift = p.drift;
  k.glow = p.glow;
  k.edge = p.edge;
  k.version++;
}

const hex = (c: number) =>
  Math.max(0, Math.min(255, Math.round(c)))
    .toString(16)
    .padStart(2, '0');

/** `#rrggbb` of the accent (or the hot accent). Allocates: call on re-bake only. */
export function particleHex(hot = false): string {
  const c = hot ? particleSkin.hot : particleSkin.rgb;
  return `#${hex(c[0])}${hex(c[1])}${hex(c[2])}`;
}

/** Count multiplier ceiling for this device class (_motion.yaml particles). */
export function particleCap(motion: Motion | null): number {
  const p = motion?.particles;
  if (!p) return 1;
  const cores = typeof navigator === 'undefined' ? 8 : navigator.hardwareConcurrency || 8;
  return cores <= p.low_cores ? p.cap_low : p.cap_desktop;
}

/** Size multiplier snapped to `steps` levels so a cross-fade re-bakes rarely. */
export function quantSize(size: number, steps: number): number {
  const n = Math.max(1, steps);
  return Math.round(size * n) / n;
}
