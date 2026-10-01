'use client';

import { useMemo, useRef } from 'react';
import {
  type BridgeCaptions,
  type BridgeLiveCaption,
  type BridgeRoomState,
  type CallState,
  callStateFrom,
  liveCaptionFrom,
  roomFrom,
} from '@/lib/bridge';
import { useSharedPoll } from '@/lib/shared-poll';

/** Call presence ("hey Jarvis" should go purple at once) and the wake's
 *  boot log: one shared /room poll for every HUD component. */
export const ROOM_POLL_MS = 300;
/** Jarvis's word-synced live line, only while a call is up. */
export const LIVE_POLL_MS = 100;
/** Finished caption lines, only while a call is up. */
export const CAPTIONS_POLL_MS = 1500;
/** One captions path for everyone, so they share one poller. */
const CAPTIONS_PATH = '/captions?limit=3';

export function useRoomState(ms = ROOM_POLL_MS): BridgeRoomState | null {
  return useSharedPoll<BridgeRoomState>('/room', ms);
}

export function useCallState(): CallState | null {
  const raw = useRoomState();
  const pending = useRef(false);
  return useMemo(() => {
    if (raw) {
      const stage = raw.boot?.at(-1)?.[0];
      if (stage === 'online' || (!raw.room && !raw.waking && !stage)) pending.current = false;
      else if (stage || raw.waking) pending.current = true;
    }
    const call = callStateFrom(raw);
    return call && pending.current ? { ...call, live: false } : call;
  }, [raw]);
}

export function useRoom(ms = ROOM_POLL_MS): string | null {
  return roomFrom(useRoomState(ms));
}

export function useLiveCaption(enabled: boolean, ms = LIVE_POLL_MS): BridgeLiveCaption | null {
  const raw = useSharedPoll<{ live?: BridgeLiveCaption | null }>('/caption/live', ms, enabled);
  return useMemo(() => liveCaptionFrom(raw), [raw]);
}

export function useCaptions(
  enabled: boolean,
  ms = CAPTIONS_POLL_MS
): NonNullable<BridgeCaptions['captions']> {
  const raw = useSharedPoll<BridgeCaptions>(CAPTIONS_PATH, ms, enabled);
  return useMemo(() => (Array.isArray(raw?.captions) ? raw.captions : []), [raw]);
}
