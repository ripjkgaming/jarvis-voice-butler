'use client';

import { type BootView, useBootLog } from '@/hooks/hud/use-boot-log';

/** Rows kept on screen; older steps scroll off the top. */
const VISIBLE_ROWS = 6;

/** Call-setup readout over the reactor while "hey Jarvis" comes up:
 *  every line is a real step the wake client just took, timed from the
 *  wake word. Renders nothing outside a summon. */
export function BootLog() {
  const view = useBootLog();
  if (!view) return null;
  const rows = view.rows.slice(-VISIBLE_ROWS);
  return (
    <div className="hud-boot" data-online={view.online} role="status" aria-live="polite">
      <div className="hud-boot__head" aria-hidden="true">
        <span>LINK</span>
        <span>{view.online ? 'ESTABLISHED' : `STEP ${String(view.step).padStart(2, '0')}`}</span>
      </div>
      <ol className="hud-boot__rows">
        {rows.map((r) => (
          <li key={r.key} className="hud-boot__row" data-done={r.done}>
            <span className="hud-boot__mark" aria-hidden="true" />
            <span className="hud-boot__label">{r.label}</span>
            <span className="hud-boot__detail">{r.detail}</span>
            <span className="hud-boot__at">{r.at}</span>
          </li>
        ))}
        {view.sub ? (
          <li key={view.sub} className="hud-boot__sub">
            {view.sub}
          </li>
        ) : null}
      </ol>
    </div>
  );
}

/** Concise version for the school strip's stream slot. Its job is to
 *  cover the agent's join time: always something moving, no step count
 *  or clock that would draw attention to the wait. The strip owns the
 *  poll (it also decides whether the slot shows this at all). */
export function BootLine({ view }: { view: BootView }) {
  return (
    <span className="sbar-boot" data-online={view.online} role="status" aria-live="polite">
      <b>{view.online ? 'LINK UP' : 'LINKING'}</b>
      <span key={view.short} className="sbar-boot__label">
        {view.short}
      </span>
      {view.sub ? (
        <span key={view.sub} className="sbar-boot__sub">
          {view.sub}
        </span>
      ) : null}
    </span>
  );
}
