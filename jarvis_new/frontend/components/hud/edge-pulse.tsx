'use client';

import { useEffect, useState } from 'react';
import type { HudTaskEvent } from '@/hooks/hud/use-hud-events';

/**
 * Border glow on tool_finish(success). Solo = subtle 4s auto-dismiss;
 * dual/external = full-strength until acked (click).
 */
export function EdgePulse({ events, solo = false }: { events: HudTaskEvent[]; solo?: boolean }) {
  const [glow, setGlow] = useState(false);

  useEffect(() => {
    const last = events.at(-1);
    if (last?.kind === 'tool_finish' && last.ok !== false) {
      setGlow(true);
      if (solo) {
        const t = setTimeout(() => setGlow(false), 4000);
        return () => clearTimeout(t);
      }
    }
  }, [events, solo]);

  if (!glow) return null;
  return (
    <button
      type="button"
      aria-label="Acknowledge task completion"
      className="hud-edge"
      data-solo={solo ? 'true' : 'false'}
      onClick={() => setGlow(false)}
    />
  );
}
