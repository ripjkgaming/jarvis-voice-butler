'use client';

import { useEffect, useMemo, useRef } from 'react';
import { useSharedPoll } from '@/lib/shared-poll';

/** Tails the local JARVIS activity log via bridge /actions (last ~50, 2s poll). */
export function CommandLog({ fullscreen = false }: { fullscreen?: boolean }) {
  // Shared /actions poll: the list (and the scroll-to-bottom below) only
  // changes when a new action lands.
  const raw = useSharedPoll<{ actions?: string[] }>('/actions?limit=50', 2000);
  const lines = useMemo(() => raw?.actions ?? [], [raw]);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = boxRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <div className="hud-log" data-fullscreen={fullscreen ? 'true' : 'false'}>
      <div className="hud-log__head">
        <span>ACTIVITY LOG</span>
        <span className="hud-log__path">RECENT SYSTEM ACTIONS</span>
      </div>
      <div ref={boxRef} className="hud-log__body">
        {lines.length === 0 ? (
          <p className="hud-log__empty">— log quiet, Sir —</p>
        ) : (
          lines.map((l, i) => (
            <p key={i} className="hud-log__line">
              {l}
            </p>
          ))
        )}
      </div>
    </div>
  );
}
