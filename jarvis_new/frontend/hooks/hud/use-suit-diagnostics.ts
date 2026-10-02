'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { refreshWindowGeometry, useWindowGeometry } from '@/hooks/hud/use-window-geometry';
import { bridgePost } from '@/lib/bridge';
import { coverHud } from '@/lib/hud-overlay';
import { type SuitCommandCursor, consumeSuitCommand } from '@/lib/suit-diagnostics';
import { invoke, isTauri } from '@/lib/tauri';

/** Piggyback on the HUD's shared snapshot. Closed diagnostics own no poll,
 * renderer, animation loop, or native window. */
export function useSuitDiagnostics(
  command: unknown,
  school: boolean,
  transitioning: boolean,
  mode?: 'school' | 'normal'
) {
  const [opened, setOpened] = useState(false);
  const [request, setRequest] = useState(0);
  const mountedAt = useRef(Date.now());
  const cursor = useRef<SuitCommandCursor | null>(null);
  const active = useRef(true);
  const focusReset = useRef<Promise<unknown> | null>(null);
  const initialModeSeen = useRef(false);
  const transition = useRef(transitioning);
  const modeGeneration = useRef(0);
  const wasTransitioning = useRef(transitioning);
  // A native signal can precede the transition prop by one render. Keep its
  // geometry ownership until the transition has actually started and ended.
  if (transitioning) transition.current = true;
  else if (wasTransitioning.current) transition.current = false;
  wasTransitioning.current = transitioning;
  const { height } = useWindowGeometry();
  const barHeight = useRef(48);
  if (school && height > 0 && height <= 140) barHeight.current = height;

  const consume = useCallback((value: unknown) => {
    const next = consumeSuitCommand(value, cursor.current, mountedAt.current);
    cursor.current = next.cursor;
    if (next.open !== null && active.current) {
      setOpened(next.open);
      if (next.open) setRequest((value) => value + 1);
    }
  }, []);

  const close = useCallback(() => {
    setOpened(false);
    // Consume this acknowledgement before the next /sys snapshot, so an old
    // response cannot replay the command we have just dismissed locally.
    void bridgePost<{ suit_diagnostics?: unknown }>('/suit', { open: false }).then((reply) => {
      if (active.current) consume(reply?.suit_diagnostics);
    });
  }, [consume]);

  useEffect(() => {
    active.current = true;
    // Reloads start hidden, so release any orphaned native focus override.
    // Acquisitions below wait for this reset before requesting their own lease.
    focusReset.current = isTauri()
      ? invoke('suit_focus', { active: false, lease: null })
      : Promise.resolve();
    void focusReset.current.catch(() => undefined);
    return () => {
      active.current = false;
    };
  }, []);
  useEffect(() => {
    consume(command);
  }, [command, consume]);
  useEffect(() => {
    if (mode === undefined || initialModeSeen.current) return;
    initialModeSeen.current = true;
    // A webview reload mounts hidden diagnostics, but its native school
    // surface can still be expanded. Restore the bar once on initial mode
    // discovery; ordinary later polls and transitions retain their geometry.
    if (
      mode === 'school' &&
      !opened &&
      !transition.current &&
      modeGeneration.current === 0 &&
      isTauri()
    ) {
      void invoke('school_menu', { extra: 0 })
        .catch(() => undefined)
        .finally(refreshWindowGeometry);
    }
  }, [mode, opened, transitioning]);
  useEffect(() => {
    const onMode = (event: Event) => {
      // Native transition owns geometry from this signal onward. Its canvas
      // must never compete with a diagnostics menu-close resize.
      modeGeneration.current += 1;
      // Expand is also the native cancellation signal. Do not wait for a
      // transitioning=true render that a rapid cancellation can skip entirely.
      transition.current = (event as CustomEvent).detail !== 'expand' || wasTransitioning.current;
      setOpened(false);
    };
    window.addEventListener('jarvis-school', onMode);
    return () => window.removeEventListener('jarvis-school', onMode);
  }, []);

  const visible = opened && !transition.current;
  useLayoutEffect(() => {
    if (!visible) return;
    const root = document.documentElement;
    root.dataset.suitOpen = 'true';
    const release = coverHud();
    return () => {
      delete root.dataset.suitOpen;
      release();
    };
  }, [visible, school]);

  useEffect(() => {
    if (!visible || !school || !isTauri()) return;
    const generation = modeGeneration.current;
    let current = true;
    let focusLease: number | null = null;
    const releaseFocus = (lease: number) => {
      void invoke('suit_focus', { active: false, lease }).catch(() => undefined);
    };
    // school_menu is already anchored to the shell's primary output. It keeps
    // the existing taskbar at the bottom while making room above it.
    const extra = Math.max(240, Math.min(720, window.screen.availHeight - barHeight.current - 48));
    refreshWindowGeometry();
    void invoke('school_menu', { extra })
      .then(async () => {
        await focusReset.current;
        if (!current || transition.current || generation !== modeGeneration.current) return;
        const lease = await invoke<number | null>('suit_focus', { active: true, lease: null });
        if (typeof lease !== 'number') return;
        if (!current || transition.current || generation !== modeGeneration.current)
          releaseFocus(lease);
        else focusLease = lease;
      })
      .catch(() => undefined)
      .finally(refreshWindowGeometry);
    return () => {
      current = false;
      if (focusLease !== null) releaseFocus(focusLease);
    };
  }, [visible, school, request]);
  useEffect(() => {
    if (!visible || !school || !isTauri()) return;
    const generation = modeGeneration.current;
    return () => {
      if (!transition.current && generation === modeGeneration.current) {
        void invoke('school_menu', { extra: 0 })
          .catch(() => undefined)
          .finally(refreshWindowGeometry);
      }
    };
  }, [visible, school]);

  return { open: visible, close, barHeight: barHeight.current, focusReset };
}
