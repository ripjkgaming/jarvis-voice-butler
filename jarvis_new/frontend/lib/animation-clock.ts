/** One animation clock per transition. All tweens update before its canvas
 * paints; no competing RAF loops and no 16 ms polling between stages. */
const MAX_FRAME_MS = 34;
const easeInOut = (u: number) => 0.5 - 0.5 * Math.cos(Math.PI * u);

export function frameClock() {
  let t = 0;
  let last = -1;
  let disposed = false;
  const listeners = new Set<() => void>();
  const pending = new Set<() => void>();
  const clock = {
    get now() {
      return t;
    },
    tick(raf: number) {
      if (disposed) return t;
      if (last >= 0) t += Math.max(0, Math.min(raf - last, MAX_FRAME_MS));
      last = raf;
      listeners.forEach((fn) => fn());
      return t;
    },
    /** Stops pending work immediately on reversal or unmount. */
    dispose() {
      disposed = true;
      pending.forEach((finish) => finish());
      listeners.clear();
    },
    wait(check: () => boolean, capMs: number): Promise<boolean> {
      if (disposed) return Promise.resolve(false);
      return new Promise((resolve) => {
        const finish = (ok = false) => {
          clearTimeout(timer);
          listeners.delete(step);
          pending.delete(cancel);
          resolve(ok);
        };
        const cancel = () => finish(false);
        const step = () => {
          if (check()) finish(true);
        };
        const timer = setTimeout(cancel, capMs);
        pending.add(cancel);
        listeners.add(step);
        step();
      });
    },
    frames(n: number, capMs = 700) {
      // wait() checks once immediately, then once per drawn frame.
      return clock.wait(() => n-- <= 0, capMs);
    },
  };
  return clock;
}

export type FrameClock = ReturnType<typeof frameClock>;

export function runTimers(clock: FrameClock, alive: () => boolean) {
  const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
  // Window changes use real time; animation stages use drawn-frame time.
  const until = async (cond: () => boolean, timeout: number) => {
    const t0 = performance.now();
    while (alive() && !cond()) {
      if (performance.now() - t0 > timeout) return false;
      await sleep(30);
    }
    return alive();
  };
  const untilT = async (cond: () => boolean, ms: number) => {
    const t0 = clock.now;
    await clock.wait(() => !alive() || cond() || clock.now - t0 >= ms, ms * 3 + 3000);
    return alive() && cond();
  };
  const waitT = (ms: number) => {
    const end = clock.now + ms;
    return untilT(() => clock.now >= end, ms + 50);
  };
  const tween = async (ms: number, fn: (e: number) => void, ease = easeInOut) => {
    const t0 = clock.now;
    const completed = await clock.wait(
      () => {
        if (!alive()) return true;
        const u = ms <= 0 ? 1 : Math.max(0, Math.min(1, (clock.now - t0) / ms));
        fn(ease(u));
        return u === 1;
      },
      ms * 3 + 3000
    );
    // A real timeout also resolves when the webview has stopped painting.
    if (!completed && alive()) fn(ease(1));
  };
  return { sleep, until, untilT, waitT, tween, settle: () => clock.frames(3) };
}
