'use client';

import { type ReactNode, useEffect, useState } from 'react';
import {
  type BridgeLiveCaption,
  bridgeCaptions,
  bridgeLiveCaption,
  bridgeRoom,
} from '@/lib/bridge';
import { useSaid } from '@/lib/hud-say';

/** Words kept in the live line's DOM; older ones scroll off the top anyway. */
const LIVE_WORDS = 60;
/** Live-line poll while a call is up: fast enough to land each word as it's spoken. */
const LIVE_POLL_MS = 100;

type LogLine = { ts: number; role: string; text: string };

/** Latest conversation line, single line + mini wave strip.
 *
 *  Sources (no LiveKit client in the webview — no WebRTC in WebKitGTK):
 *  typed /chat replies first (fresh only), then Jarvis's word-synced live
 *  line while he speaks, then the captions tail while a call is live,
 *  else the idle line.
 */
export function LiveCaption() {
  const said = useSaid();
  const [inCall, setInCall] = useState(false);
  const [logLine, setLogLine] = useState<LogLine | null>(null);
  const [live, setLive] = useState<BridgeLiveCaption | null>(null);

  // Slow lane: call presence + captions tail (Sir's lines, history).
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      const room = await bridgeRoom();
      if (cancelled) return;
      setInCall(!!room);
      if (!room) {
        setLogLine(null);
        return;
      }
      const lines = await bridgeCaptions(3);
      if (cancelled) return;
      const last = lines?.at(-1);
      // Fresh (2 min) lines only — stale greetings must not linger.
      if (last && Date.now() / 1000 - last.ts < 120) setLogLine(last);
    };
    void poll();
    const timer = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  // Fast lane: Jarvis's live line, only while a call is up. Chained
  // timeouts, never overlapping requests.
  useEffect(() => {
    if (!inCall) {
      setLive(null);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      const next = await bridgeLiveCaption();
      if (cancelled) return;
      setLive((prev) =>
        prev && next && prev.id === next.id && prev.text === next.text && prev.done === next.done
          ? prev
          : next
      );
      timer = setTimeout(tick, LIVE_POLL_MS);
    };
    void tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [inCall]);

  // Live wins while Jarvis is speaking, and after he finishes until a
  // newer log line (Sir talking, the next turn) supersedes it.
  const showLive = !said && live && live.text && (!live.done || live.ts >= (logLine?.ts ?? 0));

  const role = said ? 'typed' : showLive ? 'jarvis' : logLine?.role === 'sir' ? 'sir' : 'jarvis';

  let body: ReactNode;
  if (showLive) {
    const words = live.text.split(' ');
    const start = Math.max(0, words.length - LIVE_WORDS);
    body = (
      <p key={live.id} className="hud-caption__text hud-caption__text--live">
        <span>
          Jarvis:{' '}
          {words.slice(start).map((word, i) => (
            // Index keys: earlier words stay mounted, only new ones animate in.
            <span key={start + i} className="hud-caption__word">
              {word}{' '}
            </span>
          ))}
        </span>
      </p>
    );
  } else {
    const text =
      said ??
      (inCall && logLine
        ? `${logLine.role === 'sir' ? 'Sir' : 'Jarvis'}: ${logLine.text}`
        : 'Listening, Sir…');
    body = (
      <p key={text} className="hud-caption__text">
        {text}
      </p>
    );
  }

  return (
    <div className="hud-caption" data-role={role} aria-live="polite">
      <span className="hud-caption__bars" aria-hidden="true">
        {Array.from({ length: 24 }, (_, i) => (
          <i key={i} style={{ animationDelay: `${(i % 8) * 0.12}s` }} />
        ))}
      </span>
      {body}
    </div>
  );
}
