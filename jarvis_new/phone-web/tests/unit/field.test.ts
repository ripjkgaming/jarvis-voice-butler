import { describe, expect, it } from 'vitest';
import { cellsFor, FieldDynamics, fieldSample, type FieldState } from '../../src/ui/field-render';

const idle: FieldState = { speed: 0.65, boost: 0, live: false, reduced: false, accent: '#5fe3ff', agent: 'idle' };

describe('reactor dynamics', () => {
  it('changes speed continuously, without jumping orbit phase', () => {
    const d = new FieldDynamics();
    for (let i = 0; i < 600; i++) d.step(1 / 60, idle);
    const before = d.frame.t;
    d.step(1 / 60, { ...idle, speed: 1.8, live: true, agent: 'thinking' });
    expect(d.frame.t - before).toBeGreaterThan(0);
    expect(d.frame.t - before).toBeLessThan(0.02);
    for (let i = 0; i < 120; i++) d.step(1 / 60, { ...idle, speed: 1.8, live: true, agent: 'thinking' });
    expect(d.frame.think).toBeGreaterThan(0.99);
  });

  it('eases actual audio levels and lets activation pulses fade away', () => {
    const d = new FieldDynamics();
    d.pulse();
    d.step(1 / 60, { ...idle, boost: 1 });
    expect(d.frame.boost).toBeGreaterThan(0);
    expect(d.frame.boost).toBeLessThan(1);
    expect(d.frame.shock).not.toBeNull();
    for (let i = 0; i < 180; i++) d.step(1 / 60, idle);
    expect(d.frame.shock).toBeNull();
    expect(d.frame.boost).toBeLessThan(0.001);
  });

  it('reduced motion freezes the field even during speech and ignition', () => {
    const d = new FieldDynamics();
    const state = { ...idle, reduced: true, live: true, agent: 'speaking', boost: 1 };
    d.ignite();
    const before = { ...d.step(1 / 60, state) };
    for (let i = 0; i < 100; i++) d.step(1 / 60, state);
    expect(d.frame).toEqual(before);
    expect(d.frame.shock).toBeNull();
    expect(d.frame.boost).toBe(0);
  });

  it('keeps all visible samples finite and the central glyph lens dark', () => {
    const cells = cellsFor(260, 260, 4, 7);
    const frame = new FieldDynamics().step(1 / 60, idle);
    let lit = 0;
    for (let i = 0; i < cells.px.length; i++) {
      const [energy, hot] = fieldSample(cells.radius[i], cells.angle[i], cells.seed[i], frame, cells.aa);
      expect(Number.isFinite(energy) && Number.isFinite(hot)).toBe(true);
      expect(energy).toBeGreaterThanOrEqual(0);
      expect(energy).toBeLessThanOrEqual(1.5);
      if (energy > 0.075) lit++;
      if (cells.radius[i] < 0.19) expect(energy).toBe(0);
    }
    expect(lit).toBeGreaterThan(300);
  });
});
