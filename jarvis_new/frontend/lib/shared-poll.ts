'use client';

import { useEffect, useState } from 'react';
import { bridgeGet } from '@/lib/bridge';

/** One bridge poll per path, however many components want it.
 *
 *  Before this, every hook polled on its own timer (the call state alone
 *  was fetched by ~7 copies of useJarvisState/useBootLog, 20 requests a
 *  second) and most kept going while the overlay was hidden. Here:
 *  - one chained-timeout loop per path, never two requests in flight;
 *  - it runs at the fastest interval any current subscriber asked for;
 *  - it stops while the document is hidden and polls at once when shown;
 *  - a failed poll (bridge down) backs off, doubling up to 15 s;
 *  - subscribers only hear about data that actually changed.
 */

type Sub = { ms: number; cb: (value: unknown) => void };
type Poller = {
  path: string;
  subs: Set<Sub>;
  value: unknown;
  sig: string;
  timer: ReturnType<typeof setTimeout> | null;
  inFlight: boolean;
  failures: number;
};

const MAX_BACKOFF_MS = 15000;
const pollers = new Map<string, Poller>();
let visibilityHooked = false;

function isHidden(): boolean {
  return typeof document !== 'undefined' && document.hidden;
}

function intervalFor(p: Poller): number {
  let ms = Infinity;
  p.subs.forEach((s) => {
    ms = Math.min(ms, s.ms);
  });
  if (!Number.isFinite(ms)) return MAX_BACKOFF_MS;
  return p.failures > 0 ? Math.min(MAX_BACKOFF_MS, ms * 2 ** Math.min(p.failures, 6)) : ms;
}

function schedule(p: Poller, delay: number): void {
  if (p.timer) clearTimeout(p.timer);
  p.timer = p.subs.size > 0 && !isHidden() ? setTimeout(() => void run(p), delay) : null;
}

async function run(p: Poller): Promise<void> {
  p.timer = null;
  if (p.subs.size === 0 || p.inFlight) return;
  if (isHidden()) return; // resumed by the visibilitychange listener
  p.inFlight = true;
  let next: unknown = null;
  try {
    next = await bridgeGet<unknown>(p.path);
  } catch {
    next = null;
  }
  p.inFlight = false;
  if (next === null) {
    p.failures += 1; // keep the last good value
  } else {
    p.failures = 0;
    const sig = JSON.stringify(next);
    if (sig !== p.sig) {
      p.sig = sig;
      p.value = next;
      p.subs.forEach((s) => s.cb(next));
    }
  }
  schedule(p, intervalFor(p));
}

function hookVisibility(): void {
  if (visibilityHooked || typeof document === 'undefined') return;
  visibilityHooked = true;
  document.addEventListener('visibilitychange', () => {
    pollers.forEach((p) => {
      if (isHidden()) {
        if (p.timer) clearTimeout(p.timer);
        p.timer = null;
      } else if (p.subs.size > 0 && !p.inFlight) schedule(p, 0);
    });
  });
}

/** Subscribe to a bridge path; returns the unsubscribe function. */
export function subscribePoll(path: string, ms: number, cb: (value: unknown) => void): () => void {
  hookVisibility();
  let p = pollers.get(path);
  if (!p) {
    p = { path, subs: new Set(), value: null, sig: '', timer: null, inFlight: false, failures: 0 };
    pollers.set(path, p);
  }
  const poller = p;
  const sub: Sub = { ms, cb };
  const before = poller.subs.size === 0 ? Infinity : intervalFor(poller);
  poller.subs.add(sub);
  if (poller.value !== null) cb(poller.value);
  // First subscriber, or a faster one: poll now and settle into the new rate.
  if (ms < before && !poller.inFlight) schedule(poller, 0);
  return () => {
    poller.subs.delete(sub);
    if (poller.subs.size === 0 && poller.timer) {
      clearTimeout(poller.timer);
      poller.timer = null;
    }
  };
}

/** The latest JSON from a bridge path, shared across the HUD. null until
 *  the first answer; `enabled=false` unsubscribes (e.g. no call is up). */
export function useSharedPoll<T>(path: string, ms: number, enabled = true): T | null {
  const [snapshot, setSnapshot] = useState<{ path: string; value: T | null }>(() => {
    const p = pollers.get(path);
    return { path, value: enabled && p ? (p.value as T | null) : null };
  });
  useEffect(() => {
    if (!enabled) return;
    return subscribePoll(path, ms, (v) => setSnapshot({ path, value: v as T }));
  }, [path, ms, enabled]);
  if (!enabled) return null;
  // Never render the previous endpoint's data during the effect handoff.
  return snapshot.path === path ? snapshot.value : ((pollers.get(path)?.value as T) ?? null);
}
