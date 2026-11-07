/** Phone reactor: precomputed polar geometry, holographic gyroscope, orbit
 * trails and travelling glyph filaments. Drawn in the worker with a sprite
 * atlas; no text shaping, DOM reads or per-frame geometry construction. */
import { RAMP, noiseIndex, type GlyphAtlas, type ReactorFrame } from '@hud/ascii';

const TAU = Math.PI * 2;
const clamp = (x: number) => Math.max(0, Math.min(1, x));
const fract = (x: number) => x - Math.floor(x);
const band = (x: number, width: number) => Math.exp(-((x / width) ** 2));
export type FieldState = { speed: number; boost: number; live: boolean; accent: string; reduced: boolean; agent?: string };
export type PhoneFrame = ReactorFrame & { listen: number; think: number; speak: number };
export const SPEED: Record<string, number> = { idle: 0.65, listening: 0.95, thinking: 1.8, speaking: 1.15 };

/** Integrate velocity rather than multiplying absolute time: a call-state
 * change never teleports an orbit. Level attack is faster than its release. */
export class FieldDynamics {
  frame: PhoneFrame = { build: 3, t: 0, boost: 0, speed: 1, shock: null, listen: 0, think: 0, speak: 0 };
  private velocity = SPEED.idle;
  private age = 3;
  private pulseAge = 2;
  private strength = 0;
  pulse(strength = 0.8) { this.pulseAge = 0; this.strength = strength; }
  ignite() { this.age = 0; this.pulse(1); }
  step(dt: number, st: FieldState): PhoneFrame {
    const f = this.frame;
    if (st.reduced) {
      f.build = 3; f.t = 3; f.boost = 0; f.shock = null;
      f.listen = f.think = f.speak = 0;
      return f;
    }
    dt = Math.max(0, Math.min(dt, 0.064));
    const mix = 1 - Math.exp(-dt * 5);
    this.velocity += (st.speed - this.velocity) * mix;
    f.t += dt * this.velocity;
    this.age += dt;
    this.pulseAge += dt;
    f.build = this.age;
    f.boost += (Math.min(1.5, st.boost) - f.boost) * (1 - Math.exp(-dt * (st.boost > f.boost ? 18 : 4)));
    f.listen += ((st.live && st.agent === 'listening' ? 1 : 0) - f.listen) * mix;
    f.think += ((st.live && st.agent === 'thinking' ? 1 : 0) - f.think) * mix;
    f.speak += ((st.live && st.agent === 'speaking' ? 1 : 0) - f.speak) * mix;
    f.shock = this.pulseAge < 1.4 ? [this.pulseAge * 0.85, this.strength * (1 - this.pulseAge / 1.4)] : null;
    return f;
  }
}

export type Cells = {
  w: number; h: number; cw: number; ch: number; aa: number;
  px: Float32Array; py: Float32Array; nx: Float32Array; ny: Float32Array;
  radius: Float32Array; angle: Float32Array; seed: Float32Array;
};
export function cellsFor(w: number, h: number, cw: number, ch: number): Cells {
  const R = Math.min(w, h) * 0.46;
  const px: number[] = [], py: number[] = [], nx: number[] = [], ny: number[] = [];
  const radius: number[] = [], angle: number[] = [], seed: number[] = [];
  for (let y = 0; y < Math.ceil(h / ch); y++) {
    const yy = ((y + 0.5) * ch - h / 2) / R;
    for (let x = 0; x < Math.ceil(w / cw); x++) {
      const xx = ((x + 0.5) * cw - w / 2) / R;
      const r = Math.hypot(xx, yy);
      if (r > 1.09) continue;
      px.push(x * cw); py.push(y * ch); nx.push(xx); ny.push(yy);
      radius.push(r); angle.push(Math.atan2(yy, xx));
      seed.push(fract(Math.sin(x * 127.1 + y * 311.7) * 43758.5453));
    }
  }
  return { w, h, cw, ch, aa: 0.5 * ch / R, px: new Float32Array(px), py: new Float32Array(py),
    nx: new Float32Array(nx), ny: new Float32Array(ny), radius: new Float32Array(radius),
    angle: new Float32Array(angle), seed: new Float32Array(seed) };
}

/** Deterministic ornamental energy; never presented as measured telemetry. */
export function fieldSample(r: number, a: number, seed: number, f: PhoneFrame, aa: number): [number, number] {
  const t = f.t;
  const u = a / TAU;
  const assembly = clamp(f.build / 1.6);
  const fine = Math.max(0.012, aa * 0.55);
  const spin = t * (0.045 + f.think * 0.035);
  // Five bright orbit heads pull fading tails through the outer encoder ring.
  const trail = Math.exp(-fract(u * 5 - spin * 5) * 10);
  const ticks = fract(u * 80 + 0.1) < 0.3 ? 0.28 : 0;
  let energy = band(r - 0.965, fine) * (0.23 + trail * 0.9 + ticks);
  energy += band(r - 0.85, fine * 0.7) * (fract(u * 48 + t * 0.12) < 0.58 ? 0.6 : 0.05);
  energy += band(r - 0.765, fine * 0.7) * (0.13 + Math.exp(-fract(-u * 3 - t * 0.18) * 9) * 0.6);
  // Counter-rotating helical filaments: thin bright ridges with soft trails.
  const envelope = clamp((r - 0.22) / 0.13) * clamp((0.74 - r) / 0.08);
  const helix = Math.sin(a * 3 + r * 19 - t * 1.3);
  const reverse = Math.sin(a * 5 - r * 14 + t * 0.9);
  energy += envelope * (Math.pow(Math.max(0, helix), 18) * 0.57 + Math.pow(Math.max(0, reverse), 30) * 0.23);
  // Curved latitude lattice rotates through the depth of a holographic sphere.
  if (r < 0.69) {
    const z = Math.sqrt(Math.max(0, 0.69 * 0.69 - r * r));
    const x = r * Math.cos(a), y = r * Math.sin(a);
    const longitude = Math.atan2(x * Math.cos(t * 0.21) + z * Math.sin(t * 0.21), z * Math.cos(t * 0.21) - x * Math.sin(t * 0.21));
    const latitude = Math.asin(clamp(Math.abs(y) / 0.69)) * Math.sign(y);
    const grid = Math.pow(Math.max(0, Math.cos(longitude * 8)), 24) + Math.pow(Math.max(0, Math.cos(latitude * 9)), 26);
    energy += envelope * grid * (0.1 + z * 0.22);
  }
  // Listening focuses inward; speech sends level-driven ripples outward.
  const waveR = 0.43 + Math.sin(a * 12 + t * 2) * (0.006 + f.boost * 0.026);
  energy += band(r - waveR, fine) * (0.2 + f.listen * 0.25 + f.boost * 0.5);
  const ripple = fract(r * 2.1 - t * 0.48);
  energy += envelope * band(ripple - 0.5, 0.035) * f.speak * (0.1 + f.boost * 0.55);
  // Sparse glyph packets travel along radial lanes; no random frame flicker.
  const lane = fract(u * 24) < 0.15;
  if (lane && r > 0.3 && r < 0.9) energy += band(fract(r * 1.7 + t * 0.35 + seed * 0.2) - 0.5, 0.07) * 0.6;
  energy *= assembly * (0.92 + Math.sin(t * 1.5) * 0.08);
  let hot = trail * band(r - 0.965, fine) * 0.7;
  if (f.shock) { const pulse = band(r - f.shock[0], 0.045) * f.shock[1]; energy += pulse; hot += pulse * 0.7; }
  // Dark lens keeps the central mark legible.
  energy *= clamp((r - 0.19) / 0.08);
  return [Math.min(1.5, energy), Math.min(1, hot)];
}

export function paint(ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D, atlas: GlyphAtlas, c: Cells, frame: PhoneFrame) {
  ctx.clearRect(0, 0, c.w, c.h);
  for (let i = 0; i < c.px.length; i++) {
    const [intensity, hot] = fieldSample(c.radius[i], c.angle[i], c.seed[i], frame, c.aa);
    if (intensity < 0.075) continue;
    const bright = Math.min(1, intensity);
    const packet = intensity > 0.5 && c.seed[i] > 0.8;
    const glyph = packet ? noiseIndex(fract(c.seed[i] + Math.floor(frame.t * 5) * 0.13))
      : hot > 0.6 ? RAMP.length - 2 : 1 + Math.floor(bright * (RAMP.length - 3.01));
    atlas.draw(ctx as CanvasRenderingContext2D, glyph, Math.min(1, bright * 0.8 + hot * 0.45), c.px[i], c.py[i]);
  }
}
