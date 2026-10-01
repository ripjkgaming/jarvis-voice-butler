'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useCallState, useRoomState } from '@/hooks/hud/use-room-state';
import { bridgeSummon } from '@/lib/bridge';
import { isTauri, summonTalk } from '@/lib/tauri';
import { type VoiceRequest, voiceLinkFrom } from '@/lib/voice-link';

/** One summon controller survives swaps between the HUD and school bar.
 * A local request paints immediately, then observed backend stages own it. */
export function useVoiceLink(muted: boolean | null) {
  const raw = useRoomState();
  const [request, setRequest] = useState<VoiceRequest | null>(null);
  const [, setNow] = useState(Date.now);
  const busy = useRef(false);
  const sequence = useRef(0);
  const lastPending = useRef<typeof raw>(null);
  const lastStage = raw?.boot?.at(-1)?.[0];
  if (lastStage && lastStage !== 'online') lastPending.current = raw;
  else if (lastStage === 'online' || (raw && !raw.room && !raw.waking)) lastPending.current = null;
  const link = voiceLinkFrom(
    raw?.boot?.length ? raw : (lastPending.current ?? raw),
    request,
    Date.now()
  );
  const live = useCallState()?.live ?? false;

  useEffect(() => {
    const first = raw?.boot?.[0]?.[1];
    if (
      request &&
      first !== undefined &&
      first !== request.baseline &&
      first * 1000 >= request.at - 1000
    ) {
      sequence.current += 1;
      busy.current = false;
      setRequest(null);
    }
  }, [raw, request]);
  useEffect(() => {
    if (link.phase === 'stalled' && request && !request.failed) {
      // A hung native request must not make the displayed retry inert.
      sequence.current += 1;
      busy.current = false;
    }
  }, [link.phase, request]);
  useEffect(() => {
    if (link.until === null) return;
    const timer = setTimeout(() => setNow(Date.now()), Math.max(0, link.until - Date.now()) + 10);
    return () => clearTimeout(timer);
  }, [link.until]);
  useEffect(() => {
    // Resuming must not replay a ready acknowledgement from a hidden window.
    const sync = () => setNow(Date.now());
    document.addEventListener('visibilitychange', sync);
    return () => document.removeEventListener('visibilitychange', sync);
  }, []);

  const summon = useCallback(async () => {
    if (busy.current || live || link.joining || muted === true) return;
    const generation = ++sequence.current;
    const pending = { at: Date.now(), baseline: raw?.boot?.[0]?.[1] ?? null, failed: false };
    busy.current = true;
    setNow(pending.at);
    setRequest(pending);
    try {
      const ok = isTauri() ? (await summonTalk()).ok === true : await bridgeSummon();
      if (!ok && sequence.current === generation) setRequest({ ...pending, failed: true });
    } catch {
      if (sequence.current === generation) setRequest({ ...pending, failed: true });
    } finally {
      if (sequence.current === generation) busy.current = false;
    }
  }, [live, link.joining, muted, raw]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== 'NumpadEnter' || e.repeat || e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (target?.matches('input, textarea, [contenteditable="true"]')) return;
      e.preventDefault();
      void summon();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [summon]);
  return link;
}
