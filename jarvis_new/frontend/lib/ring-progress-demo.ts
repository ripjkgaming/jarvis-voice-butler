import type { RingTask, Tier } from './ring-progress';

/**
 * DEV PREVIEW ONLY (reactor-progress.tsx loads it behind
 * `process.env.NODE_ENV !== 'production'` + `?ringdemo=N`). Deterministic
 * synthetic tasks in t (ms): staggered starts, a determinate climb with a
 * backwards jump, an indeterminate one that turns determinate, and
 * completions. Never used with real data.
 */
const TIERS: Tier[] = ['cyan', 'ice', 'amber'];
const TAGS = ['RSCH', 'DL', 'CODE', 'UPD', 'BUILD', 'TASK', 'RSCH', 'DL'];

export function demoTasks(tMs: number, n: number): RingTask[] {
  const out: RingTask[] = [];
  const cycle = 24000;
  const c = tMs % cycle;
  for (let i = 0; i < Math.min(n, 9); i++) {
    const start = i * 1800;
    const dur = 11000 + i * 1700;
    const age = c - start;
    if (age < 0 || age > dur + 1500) continue;
    const frac = Math.min(1, age / dur);
    const indet = i % 3 === 1 && frac < 0.45;
    let pct: number | null = indet ? null : frac;
    if (pct !== null && i % 2 === 0 && frac > 0.55 && frac < 0.7) pct = 0.3; // jump back
    out.push({
      id: `demo:${i}`,
      tag: TAGS[i % TAGS.length],
      title: `Demo task ${i + 1}`,
      stage: indet ? 'Working' : `Step ${Math.min(9, 1 + Math.floor(frac * 9))}/9`,
      pct,
      state: age >= dur ? 'done' : 'running',
      tier: TIERS[i % TIERS.length],
    });
  }
  return out;
}
