/** DECK live-status block: the same real readbacks as the strip (lamps), in
 * long form so the deck reads at a glance. No data of its own. */
import { lamps, type StripLamp } from '../store';
import { Bolt } from './glyphs';

const LONG: Record<string, string> = { LCK: 'LOCK', GST: 'GUEST', PRS: 'PRESENCE', PWR: 'BATTERY', LNK: 'LINK', CALL: 'CALL' };
const ORDER = ['LCK', 'GST', 'PRS', 'PWR', 'LNK', 'CALL'];

export function DeckStatus() {
  const by = new Map<string, StripLamp>(lamps.value.map((l) => [l.key, l]));
  return (
    <div class="m dstat frame ticks" data-morph="deck.stat" role="group" aria-label="Live status">
      {ORDER.map((k) => {
        const l = by.get(k);
        if (!l) return null;
        return (
          <div key={k} class={`dcell t-${l.tone}`}>
            <span class="dk" aria-hidden="true">{LONG[k]}</span>
            <b class="dv" aria-hidden="true">
              {l.value}
              {l.bolt && <Bolt />}
              {l.stale && <small>{l.stale}</small>}
            </b>
          </div>
        );
      })}
    </div>
  );
}
