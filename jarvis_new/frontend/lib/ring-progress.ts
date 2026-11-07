/**
 * Pure model + motion math for the reactor task rings (components/stark/
 * reactor-progress.tsx). No DOM, no React, no allocation in the per-frame
 * path: the component only reads `RingBank.slots` and writes SVG attributes.
 *
 * Motion model
 *  - every value that moves is time-based (dt in ms), so 30 / 60 / 144 Hz
 *    reach the same state at the same wall time;
 *  - progress follows a critically damped spring evaluated in CLOSED FORM
 *    (exact for any dt, so stepping 2 x dt/2 equals 1 x dt);
 *  - the spring never overshoots its target and drops momentum when the
 *    target reverses (progress jumping backwards eases back, no bounce);
 *  - unknown progress is a smooth sweeping arc that cross-fades into a real
 *    value (and back) through a damped `indet` mix.
 */

export const MAX_RINGS = 6;

/** Fill spans this many degrees; the rest is the 12 o'clock label gap. */
export const SPAN_DEG = 348;
export const START_DEG = 6;

const SETTLE_EPS = 0.0004;
const HOLD_INDET_MS = 1000;
const FLASH_HOLD_MS = 1100;
const FOCUS_CYCLE_MS = 5000;
const MAX_DT_MS = 100;
/** Spring natural frequency (rad/s): ~0.65 s to settle. */
export const OMEGA = 8;

export type Tier = 'cyan' | 'ice' | 'amber';
export type TaskState = 'running' | 'done' | 'failed' | 'cancelled';

export type RingTask = {
  id: string;
  tag: string;
  title: string;
  stage: string;
  /** 0..1, null = unknown (indeterminate). */
  pct: number | null;
  state: TaskState;
  tier: Tier;
};

/* ------------------------------------------------------------ easing */

const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);

/** Frame-rate independent exponential approach (tau in ms). */
export function dampExp(cur: number, target: number, dtMs: number, tauMs: number): number {
  return target + (cur - target) * Math.exp(-dtMs / tauMs);
}

export type Spring = { x: number; v: number };

/**
 * Advance a critically damped spring toward `target` by dtMs, in place.
 * Closed form: x(t) = T + (A + B t) e^(-w t), A = x0 - T, B = v0 + w A.
 * Returns true once settled (and snaps x to target).
 */
export function stepSpring(s: Spring, target: number, dtMs: number, omega = OMEGA): boolean {
  // Target reversed against the motion: drop momentum (no overshoot / bounce).
  if (s.v * (target - s.x) < 0) s.v = 0;
  const t = dtMs / 1000;
  const a = s.x - target;
  const b = s.v + omega * a;
  const e = Math.exp(-omega * t);
  const nx = target + (a + b * t) * e;
  const nv = (b - omega * (a + b * t)) * e;
  if (a * (nx - target) < 0) {
    // crossed the target inside this step: land on it
    s.x = target;
    s.v = 0;
    return true;
  }
  s.x = nx;
  s.v = nv;
  if (Math.abs(nx - target) < SETTLE_EPS && Math.abs(nv) < SETTLE_EPS * 4) {
    s.x = target;
    s.v = 0;
    return true;
  }
  return false;
}

/** Indeterminate sweep: head circles once per 2.0 s while the arc length
 *  breathes 20..100 degrees. Continuous in t. Writes into `out`. */
export function sweepArc(tMs: number, out: { head: number; len: number }): void {
  out.head = ((tMs / 2000) % 1) * 360;
  out.len = 60 + 40 * Math.sin((tMs / 3100) * Math.PI * 2);
}

/* ------------------------------------------------------------ layout */

const BAND_CENTER = 239;
const BAND = 28;

/** Radius / stroke of ring `i` of `n` (0 = outermost). Rings share one band
 *  inside the reactor rim; one ring is fat, six are fine hairlines. */
export function ringLayout(n: number, i: number, out: { r: number; w: number }): void {
  const count = Math.max(1, Math.min(MAX_RINGS, n));
  const pitch = Math.min(9, BAND / count);
  out.r = BAND_CENTER + ((count - 1) / 2 - i) * pitch;
  out.w = Math.min(6, pitch * 0.62);
}

/* -------------------------------------------------------- task input */

type ResearchRow = { id: string; title: string; stage?: string | null; progress?: number | null };
type ActivityRow = {
  id: string;
  kind: string;
  title: string;
  detail?: string;
  progress: number | null;
  status: string;
};

const KIND_TAG: Record<string, string> = {
  download: 'DL',
  update: 'UPD',
  coding: 'CODE',
  research: 'RSCH',
  build: 'BUILD',
  task: 'TASK',
};

const KIND_TIER: Record<string, Tier> = {
  research: 'cyan',
  download: 'ice',
  update: 'ice',
  coding: 'amber',
  build: 'amber',
  task: 'amber',
};

const toPct = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? clamp01(v / 100) : null;

const toState = (s: string): TaskState =>
  s === 'done' || s === 'failed' || s === 'cancelled' ? s : 'running';

/** Merge bridge /sys research jobs and /activity items into ring tasks.
 *  Backend progress is 0..100; tasks carry 0..1. Order is stable (research
 *  first, then activity order), which fixes ring slots for simultaneous
 *  starts. A research job listed in both feeds appears once. */
export function collectTasks(
  research: readonly ResearchRow[] | null | undefined,
  items: readonly ActivityRow[] | null | undefined
): RingTask[] {
  const out: RingTask[] = [];
  const rs = research ?? [];
  for (const r of rs) {
    out.push({
      id: `r:${r.id}`,
      tag: KIND_TAG.research,
      title: r.title,
      stage: r.stage ?? '',
      pct: toPct(Number(r.progress) || 0),
      state: 'running',
      tier: 'cyan',
    });
  }
  for (const a of items ?? []) {
    if (a.kind === 'research' && rs.some((r) => r.id === a.id || r.title === a.title)) continue;
    out.push({
      id: `a:${a.id}`,
      tag: KIND_TAG[a.kind] ?? 'TASK',
      title: a.title,
      stage: a.detail ?? '',
      pct: toPct(a.progress),
      state: toState(a.status),
      tier: KIND_TIER[a.kind] ?? 'amber',
    });
  }
  return out;
}

export const MORE_ID = '+more';

/** More than `max` running tasks: the first max-1 keep their own ring and the
 *  rest collapse into one aggregate "+N" ring (mean of known progress, sweeping
 *  when none is known). Terminal-state tasks pass through untouched (the bank
 *  only animates ones that already own a ring). */
export function capTasks(tasks: readonly RingTask[], max = MAX_RINGS): RingTask[] {
  const running = tasks.filter((t) => t.state === 'running');
  const rest = tasks.filter((t) => t.state !== 'running');
  if (running.length <= max) return [...running, ...rest];
  const keep = running.slice(0, max - 1);
  const over = running.slice(max - 1);
  let sum = 0;
  let known = 0;
  for (const t of over) {
    if (t.pct !== null) {
      sum += t.pct;
      known++;
    }
  }
  keep.push({
    id: MORE_ID,
    tag: `+${over.length}`,
    title: `${over.length} MORE TASKS`,
    stage: '',
    pct: known ? sum / known : null,
    state: 'running',
    tier: 'ice',
  });
  return [...keep, ...rest];
}

/* -------------------------------------------------------------- bank */

export type SlotPhase = 'free' | 'running' | 'completing' | 'flash' | 'leaving';

export type Slot = {
  idx: number;
  phase: SlotPhase;
  id: string;
  tag: string;
  title: string;
  stage: string;
  tier: Tier;
  /** Target progress 0..1, null = indeterminate. */
  pct: number | null;
  /** Largest progress seen (decides if a vanished job counts as finished). */
  lastPct: number;
  /** Displayed progress (spring), 0..1. */
  fill: Spring;
  /** Ignition sweep 0..1 (spring). */
  rev: Spring;
  /** 0..1 cross-fade between the real value and the indeterminate arc. */
  indet: number;
  /** Group opacity 0..1 (damped). */
  op: number;
  /** Completion flash 1 -> 0. */
  flash: number;
  /** Current / target ring radius + stroke (damped on re-layout). */
  r: number;
  w: number;
  tr: number;
  tw: number;
  /** Not yet shown (indeterminate jobs wait a beat so blips never flicker). */
  holdUntil: number;
  phaseMs: number;
  /** Counted in the layout (visible and not free). */
  shown: boolean;
};

const blankSlot = (idx: number): Slot => ({
  idx,
  phase: 'free',
  id: '',
  tag: '',
  title: '',
  stage: '',
  tier: 'cyan',
  pct: 0,
  lastPct: 0,
  fill: { x: 0, v: 0 },
  rev: { x: 0, v: 0 },
  indet: 0,
  op: 0,
  flash: 0,
  r: 0,
  w: 0,
  tr: 0,
  tw: 0,
  holdUntil: 0,
  phaseMs: 0,
  shown: false,
});

export class RingBank {
  readonly slots: Slot[] = Array.from({ length: MAX_RINGS }, (_, i) => blankSlot(i));
  /** Slot index shown in the centre readout, -1 = none. */
  focus = -1;
  /** Number of rings currently in the layout. */
  count = 0;
  /** Bumped whenever a slot's text / identity changes (cheap React sync). */
  rev = 0;
  /** A ring just completed (consumer fires its burst). */
  burst = 0;
  private lay = { r: 0, w: 0 };
  private clock = 0;

  /** Feed the latest task list. Safe to call at poll rate, idempotent. */
  sync(tasks: readonly RingTask[], now: number): void {
    const slots = this.slots;
    const byId = new Map<string, RingTask>();
    for (const t of tasks) byId.set(t.id, t);

    for (const s of slots) {
      if (s.phase === 'free') continue;
      const t = byId.get(s.id);
      if (!t) {
        // vanished: finished if it got near the end (research has no status)
        if (s.phase === 'running') this.finish(s, s.lastPct >= 0.85 && s.pct !== null);
        continue;
      }
      if (t.state === 'running') {
        if (s.phase !== 'running') {
          s.phase = 'running'; // same id restarted while fading out
          s.phaseMs = 0;
          s.flash = 0;
        }
        this.assign(s, t);
      } else if (s.phase === 'running') {
        this.finish(s, t.state === 'done');
      }
      byId.delete(s.id);
    }

    for (const t of byId.values()) {
      if (t.state !== 'running') continue; // never saw it run: don't animate
      const s = slots.find((x) => x.phase === 'free');
      if (!s) continue;
      this.open(s, t, now);
    }
  }

  private assign(s: Slot, t: RingTask): void {
    if (s.title !== t.title || s.stage !== t.stage || s.tag !== t.tag || s.tier !== t.tier) {
      s.title = t.title;
      s.stage = t.stage;
      s.tag = t.tag;
      s.tier = t.tier;
      this.rev++;
    }
    s.pct = t.pct;
    if (t.pct !== null && t.pct > s.lastPct) s.lastPct = t.pct;
  }

  private open(s: Slot, t: RingTask, now: number): void {
    s.phase = 'running';
    s.id = t.id;
    s.fill.x = 0;
    s.fill.v = 0;
    s.rev.x = 0;
    s.rev.v = 0;
    s.indet = t.pct === null ? 1 : 0;
    s.op = 0;
    s.flash = 0;
    s.phaseMs = 0;
    s.lastPct = t.pct ?? 0;
    s.holdUntil = t.pct === null ? now + HOLD_INDET_MS : 0;
    s.shown = false;
    s.r = 0;
    s.w = 0;
    s.title = '';
    this.assign(s, t);
    this.rev++;
  }

  private finish(s: Slot, ok: boolean): void {
    if (ok && s.holdUntil <= this.clock) {
      s.phase = 'completing';
      s.pct = 1;
    } else {
      s.phase = 'leaving';
    }
    s.phaseMs = 0;
  }

  /**
   * Advance every ring by `dtMs` (clamped) at wall time `now`.
   * `reduced` snaps values instead of easing. Returns true while anything
   * is still on screen (the caller keeps its rAF alive).
   */
  step(now: number, dtMs: number, reduced = false): boolean {
    const dt = dtMs < 0 ? 0 : dtMs > MAX_DT_MS ? MAX_DT_MS : dtMs;
    this.clock = now;
    const slots = this.slots;

    // visibility + layout membership
    let n = 0;
    for (const s of slots) {
      if (s.phase === 'free') {
        s.shown = false;
        continue;
      }
      s.shown = now >= s.holdUntil;
      if (!s.shown && s.phase === 'leaving') {
        this.release(s); // blip ended before it was ever shown
        continue;
      }
      if (s.shown) n++;
    }
    this.count = n;

    let i = 0;
    let focusable = 0;
    for (const s of slots) {
      if (!s.shown) continue;
      ringLayout(n, i, this.lay);
      s.tr = this.lay.r;
      s.tw = this.lay.w;
      if (s.r === 0 || reduced) {
        s.r = s.tr;
        s.w = s.tw;
      } else {
        s.r = dampExp(s.r, s.tr, dt, 200);
        s.w = dampExp(s.w, s.tw, dt, 200);
      }
      i++;
      if (s.phase === 'running') focusable++;
    }

    // focus: a completing ring grabs the readout, else cycle the live ones
    let focus = -1;
    for (const s of slots) {
      if (s.shown && (s.phase === 'completing' || s.phase === 'flash')) {
        focus = s.idx;
        break;
      }
    }
    if (focus < 0 && focusable > 0) {
      let k = Math.floor(now / FOCUS_CYCLE_MS) % focusable;
      for (const s of slots) {
        if (s.shown && s.phase === 'running' && k-- === 0) {
          focus = s.idx;
          break;
        }
      }
    }
    this.focus = focus;

    let alive = false;
    for (const s of slots) {
      if (s.phase === 'free') continue;
      alive = true;
      if (!s.shown) continue;
      s.phaseMs += dt;

      // appear / ignition
      if (reduced) {
        s.rev.x = 1;
        s.rev.v = 0;
      } else if (s.rev.x !== 1) stepSpring(s.rev, 1, dt, 6);

      // value
      const known = s.pct !== null;
      const goal = known ? (s.pct as number) : s.fill.x;
      if (reduced) {
        s.fill.x = goal;
        s.fill.v = 0;
      } else {
        stepSpring(s.fill, goal, dt);
      }
      const indetGoal = known ? 0 : 1;
      s.indet = reduced ? indetGoal : dampExp(s.indet, indetGoal, dt, 180);
      if (Math.abs(s.indet - indetGoal) < 0.002) s.indet = indetGoal;

      // lifecycle
      let opGoal = n > 1 && s.idx !== this.focus && s.phase === 'running' ? 0.6 : 1;
      if (s.phase === 'completing') {
        if (Math.abs(s.fill.x - 1) < 0.002) {
          s.fill.x = 1;
          s.fill.v = 0;
          s.phase = 'flash';
          s.phaseMs = 0;
          s.flash = 1;
          this.burst++;
        }
      } else if (s.phase === 'flash') {
        s.flash = reduced ? 0 : dampExp(s.flash, 0, dt, 260);
        if (s.phaseMs >= (reduced ? 500 : FLASH_HOLD_MS)) {
          s.phase = 'leaving';
          s.phaseMs = 0;
        }
      }
      if (s.phase === 'leaving') opGoal = 0;
      s.op = reduced ? opGoal : dampExp(s.op, opGoal, dt, s.phase === 'leaving' ? 240 : 200);
      if (Math.abs(s.op - opGoal) < 0.004) s.op = opGoal;
      if (s.phase === 'leaving' && s.op === 0) this.release(s);
    }
    return alive;
  }

  private release(s: Slot): void {
    s.phase = 'free';
    s.id = '';
    s.shown = false;
    s.op = 0;
    this.rev++;
  }
}
