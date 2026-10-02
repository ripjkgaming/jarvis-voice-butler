'use client';

import { type ReactNode, useEffect, useMemo, useState } from 'react';
import { useCaptions, useLiveCaption, useRoom } from '@/hooks/hud/use-room-state';
import { useSaid } from '@/lib/hud-say';
import { voiceCaptionFrom } from '@/lib/voice-caption';

/** Words kept in the live line's DOM; older ones scroll off the top anyway. */
const LIVE_WORDS = 60;
/** Live-line poll while Jarvis is speaking vs. between lines. */
const LIVE_FAST_MS = 100;
const LIVE_IDLE_MS = 300;

/** Latest conversation line, single line + mini wave strip.
 *
 *  Sources (no LiveKit client in the webview — no WebRTC in WebKitGTK):
 *  typed /chat replies first (fresh only), then Jarvis's word-synced live
 *  line while he speaks, then the captions tail while a call is live,
 *  else the idle line.
 */
export function LiveCaption() {
  const said = useSaid();
  // Shared polls (lib/shared-poll): call presence, then the captions tail
  // and Jarvis's word-synced live line only while a call is up.
  const inCall = useRoom() !== null;
  const captions = useCaptions(inCall);
  // 100 ms while Jarvis is mid-sentence (each word lands as spoken);
  // 300 ms once the line is finished, waiting for the next one.
  const [speaking, setSpeaking] = useState(false);
  const liveRaw = useLiveCaption(inCall, speaking ? LIVE_FAST_MS : LIVE_IDLE_MS);
  useEffect(() => setSpeaking(!!liveRaw && !liveRaw.done), [liveRaw]);
  const live = inCall ? liveRaw : null;
  const line = useMemo(
    () => (inCall ? voiceCaptionFrom(captions, live) : null),
    [captions, inCall, live]
  );
  const showLive = !said && line?.live;
  const role = said ? 'typed' : (line?.role ?? 'jarvis');

  let body: ReactNode;
  if (showLive) {
    const words = line.text.split(' ');
    const start = Math.max(0, words.length - LIVE_WORDS);
    body = (
      <p key={line.key} className="hud-caption__text hud-caption__text--live">
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
      (line ? `${line.role === 'sir' ? 'Sir' : 'Jarvis'}: ${line.text}` : 'Listening, Sir…');
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
