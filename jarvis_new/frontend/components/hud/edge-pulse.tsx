'use client';

import { useEffect, useState } from 'react';
import type { HudTaskEvent } from '@/hooks/hud/use-hud-events';

/**
 * Task-done acknowledgement. Solo = subtle chip, 4s auto-dismiss;
 * dual = chip until acked (click). Never a fullscreen click-catcher —
 * the overlay must stay usable hands-free while the glow confirms.
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
      className="hud-ack"
      data-solo={solo ? 'true' : 'false'}
      onClick={() => setGlow(false)}
    >
      <span className="hud-ack__dot" aria-hidden="true" />
      Task done — tap to ack
    </button>
  );
}
