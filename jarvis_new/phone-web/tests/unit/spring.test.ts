import { describe, expect, it } from 'vitest';
import { DT, PRESETS, Spring } from '../../src/motion/spring';

function run(s: Spring, seconds: number, frame = 1 / 60) {
  const xs: number[] = [];
  for (let t = 0; t < seconds; t += frame) {
    s.advance(frame);
    xs.push(s.x);
  }
  return xs;
}

describe('Spring', () => {
  it('critical page preset settles in ~350 ms without overshoot', () => {
    const s = new Spring(0, PRESETS.page, 0.0005);
    s.setTarget(1);
    let settledAt = -1;
    let t = 0;
    let max = 0;
    while (t < 2) {
      s.advance(1 / 240);
      t += 1 / 240;
      max = Math.max(max, s.x);
      if (s.settled && settledAt < 0) settledAt = t;
    }
    expect(max).toBeLessThanOrEqual(1);
    expect(settledAt).toBeGreaterThan(0.25);
    expect(settledAt).toBeLessThan(0.65);
    expect(s.x).toBe(1);
  });

  it('mark preset settles in ~450 ms without overshoot', () => {
    const s = new Spring(0, PRESETS.mark, 0.0005);
    s.setTarget(1);
    const xs = run(s, 1.2);
    expect(Math.max(...xs)).toBeLessThanOrEqual(1);
    const idx = xs.findIndex((x) => Math.abs(1 - x) < 0.0005);
    expect(idx / 60).toBeLessThan(0.75);
    expect(s.settled).toBe(true);
  });

  it('shake preset overshoots (underdamped) and still settles', () => {
    const s = new Spring(0, PRESETS.shake, 0.05);
    s.v = 400;
    const xs = run(s, 2);
    expect(Math.min(...xs)).toBeLessThan(0);
    expect(s.settled).toBe(true);
    expect(s.x).toBe(0);
  });

  it('setTarget mid-flight keeps position and velocity continuous', () => {
    const s = new Spring(0, PRESETS.page, 0.0005);
    s.setTarget(1);
    run(s, 0.12);
    const x0 = s.x;
    const v0 = s.v;
    expect(v0).toBeGreaterThan(0);
    s.setTarget(0);
    expect(s.x).toBe(x0);
    expect(s.v).toBe(v0);
    let prev = s.x;
    let maxStep = 0;
    let vmax = Math.abs(v0);
    for (let i = 0; i < 200; i++) {
      s.advance(DT);
      maxStep = Math.max(maxStep, Math.abs(s.x - prev));
      vmax = Math.max(vmax, Math.abs(s.v));
      prev = s.x;
    }
    // No step larger than the velocity bound allows (plus one substep of accel slack).
    expect(maxStep).toBeLessThanOrEqual(vmax * DT * 1.01 + 1e-9);
  });

  it('is deterministic across frame splits (fixed substeps)', () => {
    const a = new Spring(0, PRESETS.page, 0.0005);
    const b = new Spring(0, PRESETS.page, 0.0005);
    a.setTarget(1);
    b.setTarget(1);
    for (let i = 0; i < 24; i++) a.advance(1 / 60);
    for (let i = 0; i < 48; i++) b.advance(1 / 120);
    expect(a.x).toBeCloseTo(b.x, 12);
    expect(a.v).toBeCloseTo(b.v, 9);
  });

  it('clamps huge frame deltas to 64 ms', () => {
    const a = new Spring(0, PRESETS.page, 0.0005);
    const b = new Spring(0, PRESETS.page, 0.0005);
    a.setTarget(1);
    b.setTarget(1);
    a.advance(5);
    b.advance(0.064);
    expect(a.x).toBe(b.x);
  });

  it('snaps and reports settled at rest', () => {
    const s = new Spring(3, PRESETS.micro, 0.05);
    expect(s.settled).toBe(true);
    s.setTarget(3);
    expect(s.settled).toBe(true);
    s.setTarget(10);
    expect(s.settled).toBe(false);
  });

  it('presets match the plan', () => {
    expect(PRESETS.page).toEqual({ k: 380, c: 39 });
    expect(PRESETS.mark).toEqual({ k: 260, c: 32 });
    expect(PRESETS.micro).toEqual({ k: 700, c: 42 });
    expect(PRESETS.shake).toEqual({ k: 900, c: 18 });
  });
});
