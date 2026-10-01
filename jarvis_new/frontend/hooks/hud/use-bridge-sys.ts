'use client';

import { type BridgeSys } from '@/lib/bridge';
import { useSharedPoll } from '@/lib/shared-poll';

/**
 * Shared bridge /sys snapshot: one 2 s poll (lib/shared-poll) no matter how
 * many HUD components subscribe (ticker, dials, gauges, SysCore). Paused
 * while hidden; offline keeps the last snapshot; null until data arrives.
 */
const POLL_MS = 2000;

export function useBridgeSysSnapshot(): BridgeSys | null {
  return useSharedPoll<BridgeSys>('/sys', POLL_MS);
}
