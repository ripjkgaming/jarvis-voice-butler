import { describe, expect, it } from 'vitest';
import { Loop } from '../../src/motion/loop';

function fakeRaf() {
  let queue: ((t: number) => void)[] = [];
  let t = 0;
  return {
    raf: (cb: (t: number) => void) => queue.push(cb),
    now: () => t,
    step(ms = 16) {
      t += ms;
      const q = queue;
      queue = [];
      q.forEach((cb) => cb(t));
      return q.length;
    },
    get pending() {
      return queue.length;
    },
  };
}

describe('Loop', () => {
  it('stops when every task is done', () => {
    const r = fakeRaf();
    const loop = new Loop(r.raf, r.now);
    let n = 0;
    loop.add(() => ++n < 3);
    while (r.step()) {
      /* drain */
    }
    expect(n).toBe(3);
    expect(loop.active).toBe(false);
    expect(r.pending).toBe(0);
  });

  it('timers fire on the loop clock', () => {
    const r = fakeRaf();
    const loop = new Loop(r.raf, r.now);
    const fired: number[] = [];
    loop.after(30, () => fired.push(loop.now()));
    r.step(16);
    expect(fired).toEqual([]);
    r.step(16);
    expect(fired).toEqual([32]);
    expect(r.step()).toBe(0);
  });

  it('cancelled timers never fire', () => {
    const r = fakeRaf();
    const loop = new Loop(r.raf, r.now);
    let fired = false;
    const cancel = loop.after(10, () => (fired = true));
    cancel();
    loop.add(() => false);
    r.step(20);
    expect(fired).toBe(false);
  });

  it('passes seconds since the last frame', () => {
    const r = fakeRaf();
    const loop = new Loop(r.raf, r.now);
    const dts: number[] = [];
    loop.add((dt) => (dts.push(dt), dts.length < 2));
    r.step(20);
    r.step(10);
    expect(dts[0]).toBeCloseTo(0.02);
    expect(dts[1]).toBeCloseTo(0.01);
  });
});
