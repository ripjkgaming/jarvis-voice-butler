'use client';

import { useSessionContext, useSessionMessages } from '@livekit/components-react';

/** Latest transcript segment, single line + mini wave strip hook point. */
export function LiveCaption() {
  const session = useSessionContext();
  const { messages } = useSessionMessages(session);
  const last = messages.at(-1);

  return (
    <div className="hud-caption" aria-live="polite">
      <span className="hud-caption__bars" aria-hidden="true">
        {Array.from({ length: 24 }, (_, i) => (
          <i key={i} style={{ animationDelay: `${(i % 8) * 0.12}s` }} />
        ))}
      </span>
      <p key={last ? messages.length : 'idle'} className="hud-caption__text">
        {last ? last.message : 'Listening, Sir…'}
      </p>
    </div>
  );
}
