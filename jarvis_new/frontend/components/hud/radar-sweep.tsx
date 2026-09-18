'use client';

import { useEffect, useState } from 'react';
import type { HudTaskEvent } from '@/hooks/hud/use-hud-events';

const SWEEP_TOOLS = new Set(['read_screen_text', 'take_os_screenshot', 'take_screenshot']);

/**
 * Cosmetic conic-gradient sweep overlay while screen-read tools run.
 * Stealth (raw captures) = monochrome, no toast, log only.
 */
export function RadarSweep({ events }: { events: HudTaskEvent[] }) {
  const [active, setActive] = useState(false);
  const [stealth, setStealth] = useState(false);

  useEffect(() => {
    const last = [...events].reverse().find((e) => e.tool && SWEEP_TOOLS.has(e.tool));
    if (!last) {
      setActive(false);
      return;
    }
    if (last.kind === 'tool_start') {
      setActive(true);
      setStealth((last.detail ?? '').includes('stealth') || last.tool === 'take_os_screenshot');
    } else if (last.kind === 'tool_finish') {
      const t = setTimeout(() => setActive(false), 600);
      return () => clearTimeout(t);
    }
  }, [events]);

  if (!active) return null;
  return <div className="hud-radar" data-stealth={stealth ? 'true' : 'false'} aria-hidden="true" />;
}
