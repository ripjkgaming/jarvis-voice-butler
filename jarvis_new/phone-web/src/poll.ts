/** /phone/v1/state poller (PLAN.md §3, §9.1): 2 s while visible, stopped while
 * hidden, backoff 2/4/8/15 s while offline, immediate refresh after a control.
 * Sole writer of the laptop, voice and link slices. */
import { api, ApiError } from './api';
import { auth, laptop, laptopFromControl, laptopFromState, link, voice, voiceFromState } from './store';

export const INTERVAL_MS = 2000;
export const BACKOFF_MS = [2000, 4000, 8000, 15000];

export type PollDeps = {
  get: () => Promise<Record<string, unknown>>;
  now: () => number;
  perf: () => number;
  setTimer: (fn: () => void, ms: number) => unknown;
  clearTimer: (t: unknown) => void;
  hidden: () => boolean;
  onGateLost: (kind: 'auth' | 'gate') => void;
};

export class Poller {
  private timer: unknown = null;
  private fails = 0;
  private inflight = false;
  private running = false;

  constructor(private d: PollDeps) {}

  get active() {
    return this.running;
  }

  start() {
    this.running = true;
    this.schedule(0);
  }

  stop() {
    this.running = false;
    if (this.timer !== null) this.d.clearTimer(this.timer);
    this.timer = null;
  }

  /** Merge a /control response's laptop readback (this module owns the slice). */
  applyControl(state: Record<string, unknown> | undefined) {
    if (state) laptop.value = laptopFromControl(laptop.value, state as never, this.d.now());
  }

  /** Poll right now (after a control, on visibility/online). */
  refreshNow() {
    if (!this.running) return;
    this.schedule(0);
  }

  nextDelay() {
    return this.fails === 0 ? INTERVAL_MS : BACKOFF_MS[Math.min(this.fails - 1, BACKOFF_MS.length - 1)];
  }

  private schedule(ms: number) {
    if (this.timer !== null) this.d.clearTimer(this.timer);
    this.timer = this.d.setTimer(() => {
      this.timer = null;
      void this.tick();
    }, ms);
  }

  async tick() {
    if (!this.running || this.inflight) return;
    if (this.d.hidden()) return; // resumed by visibilitychange
    this.inflight = true;
    const t0 = this.d.perf();
    try {
      const json = await this.d.get();
      const now = this.d.now();
      this.fails = 0;
      link.value = { phase: 'online', rttMs: this.d.perf() - t0, lastOk: now };
      laptop.value = laptopFromState(json, now);
      voice.value = voiceFromState(json);
      const g = (json.gate ?? {}) as { state?: string; expires_in_s?: number | null; soft_in_s?: number | null };
      auth.value = {
        ...auth.value,
        paired: true,
        gate: 'open',
        gateExpiresAt: typeof g.expires_in_s === 'number' ? now + g.expires_in_s * 1000 : null,
      };
    } catch (e) {
      const err = e as ApiError;
      if (err.kind === 'auth' || err.kind === 'gate') {
        this.inflight = false;
        this.stop();
        this.d.onGateLost(err.kind);
        return;
      }
      this.fails++;
      link.value = { ...link.value, phase: 'offline', rttMs: null };
    }
    this.inflight = false;
    if (this.running) this.schedule(this.nextDelay());
  }
}

export function browserPoller(onGateLost: PollDeps['onGateLost']) {
  const p = new Poller({
    get: () => api('GET', '/phone/v1/state'),
    now: () => Date.now(),
    perf: () => performance.now(),
    setTimer: (fn, ms) => setTimeout(fn, ms),
    clearTimer: (t) => clearTimeout(t as number),
    hidden: () => document.visibilityState === 'hidden',
    onGateLost,
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') p.refreshNow();
  });
  addEventListener('online', () => p.refreshNow());
  return p;
}
