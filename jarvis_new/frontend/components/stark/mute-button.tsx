'use client';

import s from './stark.module.css';

/** Block-glyph speaker with a slash through it (ASCII skin, one monospace face). */
const GLYPH = [
  '        ██          ',
  '      ████      ██  ',
  '  ██████ ██      ██ ',
  ' ███████ ██  ██   ██',
  ' ███████ ██   ████  ',
  ' ███████ ██    ██   ',
  ' ███████ ██   ████  ',
  '  ██████ ██  ██   ██',
  '      ████      ██  ',
  '        ██          ',
].join('\n');

/**
 * Large MUTED button shown while the HUD is muted (voice "mute"). The mic stays
 * live, so "unmute" also works by voice; clicking the button does the same.
 */
export function MuteButton({ muted, onToggle }: { muted: boolean | null; onToggle?: () => void }) {
  return (
    <div
      className={s.muteLayer}
      data-ix-static
      data-on={muted === true ? 'true' : undefined}
      aria-hidden={muted !== true}
    >
      <button
        type="button"
        className={s.muteBtn}
        onClick={onToggle}
        tabIndex={muted === true ? 0 : -1}
        aria-label="Jarvis is muted. Unmute"
      >
        <pre className={s.muteGlyph} aria-hidden="true">
          {GLYPH}
        </pre>
        <span className={s.muteWord}>MUTED</span>
        <span className={s.muteHint}>HUD MUTED // SAY UNMUTE OR CLICK</span>
      </button>
    </div>
  );
}
