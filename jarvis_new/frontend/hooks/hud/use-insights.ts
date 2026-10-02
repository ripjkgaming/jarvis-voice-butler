'use client';

import { type RefObject, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { refreshWindowGeometry, useWindowGeometry } from '@/hooks/hud/use-window-geometry';
import { coverHud } from '@/lib/hud-overlay';
import { invoke, isTauri } from '@/lib/tauri';

export type InsightsTab = 'usage' | 'market';

/** UI-only overlay. The existing suit controller owns the mount-time focus reset;
 * both dialogs wait for that same reset before acquiring a native focus lease. */
export function useInsights(
  school: boolean,
  transitioning: boolean,
  suitOpen: boolean,
  focusReset: RefObject<Promise<unknown> | null>
) {
  const [tab, setTab] = useState<InsightsTab | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const opened = useRef(false);
  opened.current = tab !== null;
  const modeSignal = useRef(false);
  const modeGeneration = useRef(0);
  const wasTransitioning = useRef(transitioning);
  if (transitioning) modeSignal.current = true;
  else if (wasTransitioning.current) modeSignal.current = false;
  wasTransitioning.current = transitioning;
  const blocked = useRef(transitioning || suitOpen);
  blocked.current = transitioning || suitOpen || modeSignal.current;
  const { height } = useWindowGeometry();
  const barHeight = useRef(48);
  if (school && height > 0 && height <= 140) barHeight.current = height;
  const close = useCallback(() => setTab(null), []);
  const open = useCallback((next: InsightsTab = 'usage') => {
    if (blocked.current) return;
    // Capture before coverHud makes the launcher inert and native focus moves.
    // The lazy dialog mounts later, when activeElement may already be body.
    if (!opened.current) returnFocus.current = document.activeElement as HTMLElement | null;
    opened.current = true;
    setTab(next);
  }, []);
  useEffect(() => {
    if (transitioning || suitOpen) setTab(null);
  }, [transitioning, suitOpen]);
  useEffect(() => {
    const onMode = (event: Event) => {
      modeGeneration.current += 1;
      // Expand explicitly cancels entry/return, even if React never committed
      // their transitioning=true render. A committed transition still wins.
      modeSignal.current = (event as CustomEvent).detail !== 'expand';
      blocked.current = modeSignal.current || wasTransitioning.current || suitOpen;
      setTab(null);
    };
    window.addEventListener('jarvis-school', onMode);
    return () => window.removeEventListener('jarvis-school', onMode);
  }, [suitOpen]);

  const visible = tab !== null && !blocked.current;
  useLayoutEffect(() => {
    if (!visible) return;
    return coverHud();
  }, [visible, school]);
  useEffect(() => {
    if (!visible || !school || !isTauri()) return;
    const generation = modeGeneration.current;
    let current = true;
    let focusLease: number | null = null;
    const release = (lease: number) => {
      void invoke('suit_focus', { active: false, lease }).catch(() => undefined);
    };
    const extra = Math.max(240, Math.min(820, window.screen.availHeight - barHeight.current - 32));
    void invoke('school_menu', { extra })
      .then(async () => {
        await focusReset.current;
        if (!current || blocked.current || generation !== modeGeneration.current) return;
        const lease = await invoke<number | null>('suit_focus', { active: true, lease: null });
        if (typeof lease !== 'number') return;
        if (!current || blocked.current || generation !== modeGeneration.current) release(lease);
        else focusLease = lease;
      })
      .catch(() => undefined)
      .finally(refreshWindowGeometry);
    return () => {
      current = false;
      if (focusLease !== null) release(focusLease);
      // The transition or suit controller owns geometry on a handoff.
      if (!blocked.current && generation === modeGeneration.current) {
        void invoke('school_menu', { extra: 0 })
          .catch(() => undefined)
          .finally(refreshWindowGeometry);
      }
    };
  }, [visible, school, focusReset]);

  return { visible, tab: tab ?? 'usage', open, close, barHeight: barHeight.current, returnFocus };
}
