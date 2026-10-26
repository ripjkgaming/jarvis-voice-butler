'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { type BridgeCaptions } from '@/lib/bridge';
import { useSharedPoll } from '@/lib/shared-poll';
import { listenToTalk } from '@/lib/tauri';
import { isWakeCaption } from '@/lib/voice-link';

/** A real wake is separate from room setup. An already-connected call never
 * pretends to reconnect. A single bounded acknowledgment expires independently. */
export function useVoiceActivation(live: boolean, muted: boolean | null) {
  const [activation, setActivation] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedAt = useRef(Date.now());
  const seen = useRef('');
  const captions = useSharedPoll<BridgeCaptions>('/captions?limit=3', 300, live);
  const activate = useCallback(() => {
    if (muted === true) return;
    clearTimeout(timer.current ?? undefined);
    setActivation(Date.now());
    timer.current = setTimeout(() => setActivation(0), 1000);
  }, [muted]);
  useEffect(() => () => clearTimeout(timer.current ?? undefined), []);
  useEffect(() => {
    let disposed = false;
    let off = () => {};
    void listenToTalk(activate).then((cleanup) => {
      if (disposed) cleanup();
      else off = cleanup;
    });
    return () => {
      disposed = true;
      off();
    };
  }, [activate]);
  useEffect(() => {
    const wake = captions?.captions
      ?.filter(
        (c) =>
          c.ts * 1000 >= mountedAt.current - 1000 &&
          Date.now() - c.ts * 1000 < 2500 &&
          isWakeCaption(c)
      )
      .at(-1);
    const id = wake ? `${wake.ts}:${wake.text}` : '';
    if (id && id !== seen.current) {
      seen.current = id;
      activate();
    }
  }, [captions, activate]);
  return activation;
}
