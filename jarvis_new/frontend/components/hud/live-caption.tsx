'use client';

import { useEffect, useState } from 'react';
import { bridgeCaptions, bridgeRoom } from '@/lib/bridge';
import { useSaid } from '@/lib/hud-say';

/** Latest conversation line, single line + mini wave strip.
 *
 *  Sources (no LiveKit client in the webview — no WebRTC in WebKitGTK):
 *  typed /chat replies first (fresh only), then the agent-mirrored
 *  captions tail while a call is live, else the idle line.
 */
export function LiveCaption() {
  const said = useSaid();
  const [caption, setCaption] = useState<string | null>(null);
  const [role, setRole] = useState<'sir' | 'jarvis'>('jarvis');

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      const room = await bridgeRoom();
      if (cancelled) return;
      if (!room) {
        setCaption(null);
        return;
      }
      const lines = await bridgeCaptions(3);
      if (cancelled) return;
      const last = lines?.at(-1);
      // Fresh (2 min) lines only — stale greetings must not linger.
      if (last && Date.now() / 1000 - last.ts < 120) {
        const who = last.role === 'sir' ? 'Sir' : 'Jarvis';
        setRole(last.role === 'sir' ? 'sir' : 'jarvis');
        setCaption(`${who}: ${last.text}`);
      }
    };
    void poll();
    const timer = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const text = said ?? caption ?? 'Listening, Sir…';

  return (
    <div className="hud-caption" data-role={said ? 'typed' : role} aria-live="polite">
      <span className="hud-caption__bars" aria-hidden="true">
        {Array.from({ length: 24 }, (_, i) => (
          <i key={i} style={{ animationDelay: `${(i % 8) * 0.12}s` }} />
        ))}
      </span>
      <p key={text} className="hud-caption__text">
        {text}
      </p>
    </div>
  );
}
