'use client';

import { useEffect, useState } from 'react';
import { type BridgeSys, bridgeSys } from '@/lib/bridge';

/**
 * Shared bridge /sys snapshot. One 2s poll no matter how many HUD
 * components subscribe (hud-shell ticker, SysCore gauges, …).
 * Fail-soft: offline keeps the last snapshot; null until data arrives.
 */
const POLL_MS = 2000;

let cached: BridgeSys | null = null;
let timer: ReturnType<typeof setInterval> | null = null;
const subs = new Set<(s: BridgeSys | null) => void>();

async function pollOnce(): Promise<void> {
  if (typeof document !== 'undefined' && document.hidden) return;
  try {
    const j = await bridgeSys();
    if (j) {
      cached = j;
      subs.forEach((fn) => fn(j));
    }
  } catch {
    /* offline — keep last */
  }
}

function ensurePolling(): void {
  if (timer) return;
  void pollOnce();
  timer = setInterval(() => {
    void pollOnce();
  }, POLL_MS);
}

function maybeStop(): void {
  if (subs.size === 0 && timer) {
    clearInterval(timer);
    timer = null;
  }
}

export function useBridgeSysSnapshot(): BridgeSys | null {
  const [sys, setSys] = useState<BridgeSys | null>(cached);

  useEffect(() => {
    subs.add(setSys);
    setSys(cached);
    ensurePolling();
    return () => {
      subs.delete(setSys);
      maybeStop();
    };
  }, []);

  return sys;
}
