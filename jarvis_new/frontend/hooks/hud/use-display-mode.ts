'use client';

import { useCallback, useEffect, useState } from 'react';

export type DisplayMode = 'dual' | 'solo';
export type HudMode = DisplayMode;

const KEY = 'jarvis:display-mode';

function detectSingleScreen(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    // Small viewport or single-screen laptops collapse to solo.
    if (window.innerWidth < 900) return true;
    if (typeof window.screen !== 'undefined' && window.screen.width < 1400) return true;
  } catch {
    return false;
  }
  return false;
}

/** Persisted dual/solo toggle. Solo collapses HudShell to orb + caption. */
export function useDisplayMode() {
  const [mode, setMode] = useState<DisplayMode>(() => {
    if (typeof window === 'undefined') return 'dual';
    try {
      const saved = window.localStorage.getItem(KEY) as DisplayMode | null;
      if (saved === 'solo' || saved === 'dual') return saved;
    } catch {
      /* ignore */
    }
    return detectSingleScreen() ? 'solo' : 'dual';
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(KEY, mode);
    } catch {
      /* ignore */
    }
  }, [mode]);

  useEffect(() => {
    const onResize = () => {
      try {
        const saved = window.localStorage.getItem(KEY);
        if (saved) return; // manual choice wins
      } catch {
        /* ignore */
      }
      setMode(detectSingleScreen() ? 'solo' : 'dual');
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const toggle = useCallback(() => {
    setMode((m) => (m === 'dual' ? 'solo' : 'dual'));
  }, []);

  return { mode, setMode, toggle, isSolo: mode === 'solo' };
}
