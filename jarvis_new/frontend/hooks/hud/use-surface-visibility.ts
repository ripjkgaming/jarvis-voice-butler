'use client';

import { useEffect } from 'react';

/** Companion windows share the HUD's CSS animation pause while hidden. */
export function useSurfaceVisibility(): void {
  useEffect(() => {
    const root = document.documentElement;
    const sync = () => root.classList.toggle('hud-hidden', document.hidden);
    sync();
    document.addEventListener('visibilitychange', sync);
    return () => {
      document.removeEventListener('visibilitychange', sync);
      root.classList.remove('hud-hidden');
    };
  }, []);
}
