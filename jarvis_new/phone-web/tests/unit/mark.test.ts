import { describe, expect, it } from 'vitest';
import { GLYPHS, IGNITION, Ignition, MARK, MarkModel, hexToLinear, targetFor } from '../../src/motion/mark';
import { glyphFor } from '../../src/store';

const MOD = { agent: 'idle', micRms: 0, playRms: 0 };

describe('mark data', () => {
  it('every glyph has 16 core points and 4 satellites', () => {
    for (const g of GLYPHS) {
      expect(MARK.glyphs[g].core.points).toHaveLength(16);
      expect(MARK.glyphs[g].sats).toHaveLength(4);
      for (const p of MARK.glyphs[g].core.points) expect(p).toHaveLength(2);
    }
    expect(MARK.modifiers.locked.core.points).toHaveLength(16);
  });

  it('ignition order is orbit, flame, play, star', () => {
    expect(IGNITION).toEqual(['orbit', 'flame', 'play', 'star']);
  });
});

describe('targets', () => {
  it('locked overrides: sealed dot, ice ring, no satellites', () => {
    const t = targetFor('locked', false);
    expect(t.points).toEqual(MARK.modifiers.locked.core.points);
    expect(t.sats.every((s) => s.opacity === 0 && s.scale === 0)).toBe(true);
    expect(t.ring).toEqual(hexToLinear('#e8fbff'));
  });

  it('guest modifier: amber dashed ring over any glyph, core unchanged', () => {
    for (const g of ['star', 'orbit', 'flame', 'play', 'locked'] as const) {
      const t = targetFor(g, true);
      expect(t.ring).toEqual(hexToLinear('#ffb347'));
      expect(t.dash).toBe(1);
      expect(t.points).toEqual(targetFor(g, false).points);
    }
    expect(targetFor('star', false).dash).toBe(0);
  });

  it('state mapping feeds the mark', () => {
    expect(glyphFor(true, 'listening')).toBe('locked');
    expect(glyphFor(false, 'listening')).toBe('orbit');
  });

  it('idle spins slowly, reduced motion never spins', () => {
    expect(targetFor('star', false).spin).toBeCloseTo(0.05);
    expect(targetFor('star', false, true).spin).toBe(0);
  });
});

describe('MarkModel', () => {
  it('morphs continuously and lands exactly on the target glyph', () => {
    const m = new MarkModel(false, 'star');
    m.setGlyph('play');
    let prev = m.vertex(0);
    let maxStep = 0;
    for (let i = 0; i < 120; i++) {
      m.step(1 / 60, MOD);
      const v = m.vertex(0);
      maxStep = Math.max(maxStep, Math.hypot(v[0] - prev[0], v[1] - prev[1]));
      prev = v;
    }
    const want = MARK.glyphs.play.core.points[0];
    expect(m.vertex(0)[0]).toBeCloseTo(want[0], 3);
    expect(m.vertex(0)[1]).toBeCloseTo(want[1], 3);
    expect(maxStep).toBeLessThan(2.5); // mark units per 16 ms frame
  });

  it('a retarget mid-morph keeps vertex position and velocity', () => {
    const m = new MarkModel(false, 'orbit');
    m.setGlyph('flame');
    for (let i = 0; i < 8; i++) m.step(1 / 60, MOD);
    const x = m.pts[0].x;
    const v = m.pts[0].v;
    m.setGlyph('play');
    expect(m.pts[0].x).toBe(x);
    expect(m.pts[0].v).toBe(v);
    let prev = m.vertex(0);
    for (let i = 0; i < 60; i++) {
      m.step(1 / 60, MOD);
      const cur = m.vertex(0);
      expect(Math.hypot(cur[0] - prev[0], cur[1] - prev[1])).toBeLessThan(2.5);
      prev = cur;
    }
  });

  it('modulation is additive and never changes targets', () => {
    const m = new MarkModel(false, 'orbit');
    m.setGlyph('orbit');
    const before = m.sat[0].map((s) => s.target);
    m.step(1 / 60, { agent: 'listening', micRms: 1, playRms: 1 });
    expect(m.sat[0].map((s) => s.target)).toEqual(before);
  });

  it('reduced motion snaps', () => {
    const m = new MarkModel(true, 'star');
    m.setGlyph('flame');
    expect(m.vertex(0)).toEqual(MARK.glyphs.flame.core.points[0] as [number, number]);
    expect(m.settled).toBe(true);
  });

  it('ignition cycles and can loop', () => {
    const m = new MarkModel(false, 'star');
    const timers: (() => void)[] = [];
    const seen: string[] = [];
    const ign = new Ignition(m, (_ms, fn) => (timers.push(fn), () => {}), () => seen.push(m.glyph));
    ign.start(false);
    while (timers.length) timers.shift()!();
    expect(seen).toEqual(['orbit', 'flame', 'play', 'star']);
    const loopSeen: string[] = [];
    const ign2 = new Ignition(m, (_ms, fn) => (timers.push(fn), () => {}), () => loopSeen.push(m.glyph));
    ign2.start(true);
    for (let i = 0; i < 6; i++) timers.shift()!();
    expect(loopSeen.slice(0, 6)).toEqual(['orbit', 'flame', 'play', 'star', 'orbit', 'flame']);
  });
});
