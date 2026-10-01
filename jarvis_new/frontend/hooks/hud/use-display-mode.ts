'use client';

import { useCallback, useEffect, useState } from 'react';

export type DisplayMode = 'dual' | 'solo';
export type HudMode = DisplayMode;

// v2 key: v1 persisted the *auto-detected* mode, which locked the 1280x720
// overlay into solo on screens whose reported width trips the old
// single-screen heuristic. Fresh key → fresh auto-detect.
const KEY = 'jarvis:display-mode-v2';

function detectNarrow(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    // Viewport only: the overlay window is a fixed 1280x720 dual-mode
    // surface. screen.width lies in some webviews (and multi-scale
    // desktops), so it must never force solo.
    if (window.innerWidth < 900) return true;
  } catch {
    return false;
  }
  return false;
}

/** Dual/solo mode. Auto-detect is viewport-only; only an explicit toggle
 *  touch persists (a manual choice always wins over re-detect). */
export function useDisplayMode() {
  const [mode, setMode] = useState<DisplayMode>(() => {
    if (typeof window === 'undefined') return 'dual';
    try {
      const saved = window.localStorage.getItem(KEY) as DisplayMode | null;
      if (saved === 'solo' || saved === 'dual') return saved;
    } catch {
      /* ignore */
    }
    return detectNarrow() ? 'solo' : 'dual';
  });

  useEffect(() => {
    const onResize = () => {
      try {
        if (window.localStorage.getItem(KEY)) return; // manual choice wins
      } catch {
        /* ignore */
      }
      setMode(detectNarrow() ? 'solo' : 'dual');
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const toggle = useCallback(() => {
    setMode((m) => {
      const next = m === 'dual' ? 'solo' : 'dual';
      try {
        window.localStorage.setItem(KEY, next);
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  return { mode, setMode, toggle, isSolo: mode === 'solo' };
}
