/** §2.1 status strip: one row of ASCII lamps from real readbacks only. */
import { lamps, type StripLamp } from '../store';
import { Bolt } from './glyphs';

const GLYPH: Record<StripLamp['tone'], string> = { on: '●', warn: '◐', guest: '●', off: '○', fail: '○', unknown: '' };
const WORDS: Record<string, string> = { LNK: 'Link', CALL: 'Call', GST: 'Guest mode', LCK: 'Laptop lock', PWR: 'Laptop power', PRS: 'Presence' };

export function StatusStrip({ onUpdate }: { onUpdate: () => void }) {
  return (
    <div class="strip" data-morph="strip" role="group" aria-label="Status">
      {lamps.value.map((l) =>
        l.key === 'UPD' ? (
          <button key="UPD" class="lamp upd" onClick={onUpdate} aria-label="Update available. Apply update.">
            UPD
          </button>
        ) : (
          <span
            key={l.key}
            class={`lamp t-${l.tone}`}
            data-stripfade={l.key === 'LNK' ? undefined : ''}
          >
            <span class="sr">{`${WORDS[l.key]}: ${l.value === '--' ? 'unknown' : l.value}${l.bolt ? ', charging' : ''}${l.stale ? `, ${l.stale} old` : ''}`}</span>
            <i aria-hidden="true">{GLYPH[l.tone] || '-'}</i>
            <span aria-hidden="true">{l.label}</span>
            <b aria-hidden="true">{l.value}</b>
            {l.bolt && <Bolt />}
            {l.stale && <small aria-hidden="true">{l.stale}</small>}
          </span>
        ),
      )}
    </div>
  );
}
