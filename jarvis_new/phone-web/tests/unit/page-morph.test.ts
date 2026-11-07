import { describe, expect, it } from 'vitest';
import { delta, IDENTITY, invert, lerpT, shortest, type Pose } from '../../src/motion/flip';
import { CORE_DECK, GATE_CORE, MAPS, PageMorph, pairFrame, SEGMENTS, type MorphEnv, type PageId } from '../../src/motion/page-morph';

const P = (cx: number, cy: number, w: number, h: number, rot = 0): Pose => ({ cx, cy, w, h, rot });

describe('FLIP math', () => {
  it('delta maps the to-rect onto the from-rect', () => {
    const d = delta(P(100, 100, 50, 10, 324), P(200, 300, 180, 92, 0));
    expect(d.tx).toBe(-100);
    expect(d.ty).toBe(-200);
    expect(d.sx).toBeCloseTo(50 / 180);
    expect(d.sy).toBeCloseTo(10 / 92);
    expect(d.r).toBe(-36); // 324° is −36° the short way
  });

  it('rotation takes the shortest path', () => {
    expect(shortest(350, 10)).toBe(20);
    expect(shortest(10, 350)).toBe(-20);
    expect(shortest(0, 180)).toBe(180);
    expect(shortest(0, -180)).toBe(180);
  });

  it('lerpT endpoints', () => {
    const d = delta(P(0, 0, 10, 10, 90), P(50, 50, 20, 40));
    expect(lerpT(d, IDENTITY, 0)).toEqual(d);
    expect(lerpT(d, IDENTITY, 1)).toEqual(IDENTITY);
    expect(lerpT(IDENTITY, invert(d), 1)).toEqual(invert(d));
  });

  it('incoming and outgoing elements share one path', () => {
    const from = P(100, 100, 40, 12, 36);
    const to = P(300, 500, 180, 92);
    for (const p of [0, 0.3, 0.5, 0.8, 1]) {
      const f = pairFrame(from, to, p);
      const inW = to.w * f.inT.sx;
      const outW = from.w * f.outT.sx;
      expect(inW).toBeCloseTo(outW, 6);
      expect(to.cx + f.inT.tx).toBeCloseTo(from.cx + f.outT.tx, 6);
      expect(to.rot + f.inT.r).toBeCloseTo(from.rot + f.outT.r, 6);
    }
    const a = pairFrame(from, to, 0);
    expect([a.inO, a.outO, a.outLabel]).toEqual([0, 1, 1]);
    const b = pairFrame(from, to, 1);
    expect([b.inO, b.outO, b.inLabel]).toEqual([1, 0, 1]);
  });

  it('maps follow the plan', () => {
    expect(SEGMENTS.map((s) => s.angle)).toEqual([324, 36, 108, 180, 252]);
    const segPairs = CORE_DECK.pairs.filter((p) => p.from.startsWith('core.seg'));
    expect(segPairs.map((p) => [p.to, p.delay])).toEqual([
      ['deck.tile.GUEST', 0],
      ['deck.tile.LOCK', 18],
      ['deck.tile.SCREENS', 36],
      ['deck.tile.POWER', 54],
      ['deck.tile.UNLOCK', 72],
    ]);
    expect(CORE_DECK.pairs.find((p) => p.from === 'core.cmd')).toMatchObject({ to: 'deck.tile.MIC', delay: 40 });
    expect(GATE_CORE.pairs.map((p) => [p.from, p.to, p.delay])).toEqual([
      ['gate.mark', 'core.mark', 0],
      ['gate.decode', 'core.cmd', 30],
      ['gate.glyphrow', 'strip', 60],
    ]);
  });
});

// ------------------------------------------------------------- controller

function fakeEl(key: string) {
  const attrs = new Set<string>();
  return {
    dataset: { morph: key } as Record<string, string>,
    style: {} as Record<string, string>,
    inert: false,
    querySelectorAll: () => [],
    setAttribute: (a: string) => attrs.add(a),
    removeAttribute: (a: string) => attrs.delete(a),
    hidden: () => attrs.has('data-hidden'),
  };
}

function harness(start: PageId = 'core') {
  const els = new Map<string, ReturnType<typeof fakeEl>>();
  const pages = new Map<PageId, ReturnType<typeof fakeEl>>();
  const el = (k: string) => {
    if (!els.has(k)) els.set(k, fakeEl(k));
    return els.get(k)!;
  };
  for (const id of ['gate', 'core', 'deck'] as PageId[]) pages.set(id, fakeEl(id));
  let now = 0;
  let timers: { at: number; fn: () => void }[] = [];
  let task: ((dt: number) => boolean) | null = null;
  const settled: PageId[] = [];
  const env: MorphEnv = {
    el: (k) => el(k) as unknown as HTMLElement,
    page: (id) => pages.get(id) as unknown as HTMLElement,
    after: (ms, fn) => {
      const t = { at: now + ms, fn };
      timers.push(t);
      return () => (timers = timers.filter((x) => x !== t));
    },
    run: (fn) => (task = fn),
    now: () => now,
    measure: (e) => {
      const k = (e as unknown as { dataset: { morph: string } }).dataset.morph;
      let h = 0;
      for (const c of k) h = (h * 31 + c.charCodeAt(0)) % 997;
      return P(h, h * 2, 20 + (h % 50), 10 + (h % 30), k.startsWith('core.seg.') ? Number(k.split('.')[2]) : 0);
    },
    onSettle: (p) => settled.push(p),
  };
  const m = new PageMorph(env, start);
  m.measureAll();
  m.snap(start);
  settled.length = 0;
  const step = (ms = 1000 / 60) => {
    now += ms;
    const due = timers.filter((t) => t.at <= now);
    timers = timers.filter((t) => t.at > now);
    due.forEach((t) => t.fn());
    if (task && !task(ms / 1000)) task = null;
  };
  const runToRest = (limit = 600) => {
    let n = 0;
    while (task && n++ < limit) step();
  };
  return { m, el, pages, step, runToRest, settled, timers: () => timers };
}

describe('PageMorph', () => {
  it('core to deck settles on deck and hides other pages', () => {
    const h = harness('core');
    h.m.go('deck');
    expect(h.pages.get('core')!.hidden()).toBe(false);
    expect(h.pages.get('deck')!.hidden()).toBe(false);
    h.runToRest();
    expect(h.m.current).toBe('deck');
    expect(h.settled).toEqual(['deck']);
    expect(h.pages.get('core')!.hidden()).toBe(true);
    expect(h.el('deck.tile.GUEST').style.opacity).toBe('1');
    expect(h.el('core.seg.324').style.opacity).toBe('0');
  });

  it('stagger applies only when starting from rest', () => {
    const h = harness('core');
    h.m.go('deck');
    const idx = CORE_DECK.pairs.findIndex((p) => p.to === 'deck.tile.UNLOCK');
    expect(h.m.springs[idx].target).toBe(0); // delayed 72 ms
    expect(h.m.springs[0].target).toBe(1);
    h.step(80);
    expect(h.m.springs[idx].target).toBe(1);
    // Interrupt mid-flight: every pair retargets immediately, no delay.
    h.step(16);
    h.m.go('core');
    expect(h.m.springs.every((s) => s.target === 0)).toBe(true);
    expect(h.timers()).toHaveLength(0);
  });

  it('interrupting keeps every spring continuous', () => {
    const h = harness('core');
    h.m.go('deck');
    for (let i = 0; i < 7; i++) h.step();
    const before = h.m.springs.map((s) => [s.x, s.v]);
    h.m.go('core');
    expect(h.m.springs.map((s) => [s.x, s.v])).toEqual(before);
    h.runToRest();
    expect(h.m.current).toBe('core');
  });

  it('a third-page trigger first returns to CORE, then starts the new map', () => {
    const h = harness('core');
    h.m.go('deck');
    for (let i = 0; i < 10; i++) h.step();
    h.m.go('gate');
    expect(h.m.queued).toBe('gate');
    expect(h.m.map).toBe(CORE_DECK);
    expect(h.m.springs.every((s) => s.target === 0)).toBe(true);
    let switched = false;
    for (let i = 0; i < 600 && h.m.moving; i++) {
      h.step();
      if (h.m.map === GATE_CORE && !switched) {
        switched = true;
        // the new map starts from the CORE side
        expect(h.m.springs.every((s) => s.x === 1 || s.target === 0)).toBe(true);
      }
    }
    expect(switched).toBe(true);
    expect(h.m.current).toBe('gate');
    expect(h.settled).toEqual(['gate']);
  });

  it('gate to deck goes through core without jumping', () => {
    const h = harness('gate');
    h.m.go('deck');
    h.runToRest(1200);
    expect(h.m.current).toBe('deck');
    expect(MAPS.indexOf(h.m.map)).toBe(1);
  });

  it('only the target page is interactive during a morph', () => {
    const h = harness('core');
    h.m.go('deck');
    expect(h.pages.get('deck')!.inert).toBe(false);
    expect(h.pages.get('core')!.inert).toBe(true);
    expect(h.pages.get('gate')!.inert).toBe(true);
  });
});
