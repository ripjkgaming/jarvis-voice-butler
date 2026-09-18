'use client';

import { useEffect, useRef, useState } from 'react';
import { bridgeActions } from '@/lib/bridge';

/** Tails ~/.jarvis/actions.log via bridge /actions (last ~50, 2s poll). */
export function CommandLog({ fullscreen = false }: { fullscreen?: boolean }) {
  const [lines, setLines] = useState<string[]>([]);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      if (document.hidden) return;
      try {
        const actions = await bridgeActions(50);
        if (alive) setLines(actions);
      } catch {
        /* keep last */
      }
    };
    tick();
    const timer = setInterval(tick, 2000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const el = boxRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <div className="hud-log" data-fullscreen={fullscreen ? 'true' : 'false'}>
      <div className="hud-log__head">
        <span>TERMINAL</span>
        <span className="hud-log__path">~/.jarvis/actions.log</span>
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
