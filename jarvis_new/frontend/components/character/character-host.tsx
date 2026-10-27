'use client';

import { useEffect } from 'react';
import { CharacterEngine, exposeEngine } from '@/lib/character/engine';
import { GlowLayer, exposeGlow } from '@/lib/character/glow-gl';
import { startCharacterStore } from '@/lib/character/store';

/** Mounted once in the HUD shell: loads the roster and runs the engine. */
export function CharacterHost() {
  useEffect(() => {
    startCharacterStore();
    const engine = new CharacterEngine();
    exposeEngine(engine);
    const stop = engine.start();
    const glow = new GlowLayer();
    exposeGlow(glow);
    const stopGlow = glow.start();
    return () => {
      stop();
      stopGlow();
    };
  }, []);
  return null;
}
