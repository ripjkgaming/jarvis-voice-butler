'use client';

import { type MutableRefObject, useCallback, useEffect, useRef, useState } from 'react';
import { useDataTopic } from '@/lib/voice-session-react';
import { useBridgeCallSource } from './bridge-feed';
import { parseCallEvent, reduceCall } from './call-reducer';
import type { CallView } from './types';

export { parseCallEvent, reduceCall, toMs } from './call-reducer';

export type CallFeed = {
  call: CallView | null;
  /** Jarvis output level 0..1, smoothed by the visualiser. Read in rAF. */
  level: MutableRefObject<number>;
  /** Feed any data-channel payload; non-call payloads are ignored. */
  onEvent: (payload: unknown, now?: number) => void;
  /** Drop the current call (dismiss after the summary). */
  clear: () => void;
};

/** Call event state with no transport attached: wire `onEvent` to any
 *  source (the HUD data channel, a window event, the demo script). */
export function useCallEvents(): CallFeed {
  const [call, setCall] = useState<CallView | null>(null);
  const level = useRef(0);
  const onEvent = useCallback((payload: unknown, now?: number) => {
    const ev = parseCallEvent(payload);
    if (!ev) return;
    if (ev.event === 'speaking') {
      level.current = Math.max(0, Math.min(1, ev.level));
      return;
    }
    if (ev.event === 'state' && ev.state !== 'handling') level.current = 0;
    const t = now ?? Date.now();
    setCall((v) => reduceCall(v, ev, t));
  }, []);
  const clear = useCallback(() => setCall(null), []);
  return { call, level, onEvent, clear };
}

/** How long the ended card stays up before the overlay dismisses itself. */
export const ENDED_LINGER_MS = 45_000;

/** Window relay + bridge poll + self-dismiss: the transport-independent
 *  half of a live feed. */
function useLiveSources(feed: CallFeed, bridge: boolean) {
  const { onEvent, clear, call } = feed;
  useEffect(() => {
    const on = (e: Event) => onEvent((e as CustomEvent).detail);
    window.addEventListener('jarvis-call', on);
    return () => window.removeEventListener('jarvis-call', on);
  }, [onEvent]);
  const ended = call?.state === 'ended';
  useBridgeCallSource(onEvent, !!call && !ended, bridge);
  useEffect(() => {
    if (!ended) return;
    const t = setTimeout(clear, ENDED_LINGER_MS);
    return () => clearTimeout(t);
  }, [ended, clear]);
}

/** Live feed: subscribes to the HUD data channel (`jarvis-tasks` and
 *  `jarvis`, the same topics as hooks/hud/use-hud-events.ts), to a
 *  `jarvis-call` window CustomEvent (detail = event JSON) for bridges that
 *  relay through the shell, and polls the bridge's `/calls/events` cursor
 *  (bridge-feed.ts; the path the backend uses today). Needs
 *  <VoiceSessionProvider> above it. */
export function useCallFeed({ bridge = true }: { bridge?: boolean } = {}): CallFeed {
  const feed = useCallEvents();
  useDataTopic('jarvis-tasks', feed.onEvent);
  useDataTopic('jarvis', feed.onEvent);
  useLiveSources(feed, bridge);
  return feed;
}

/** Bridge-only live feed (no voice session needed): `/calls-demo?source=bridge`. */
export function useBridgeCallFeed(): CallFeed {
  const feed = useCallEvents();
  useLiveSources(feed, true);
  return feed;
}
