/** §2.3 captions: last 40 lines, newest last, wrapped; the box scrolls and
 * stays pinned to the newest line unless the user scrolled up. Finals ice, partials cyan 60%.
 * Only final lines reach the polite live region. */
import { captions } from '../store';
import { useEffect, useRef } from 'preact/hooks';
import { Bolt } from './glyphs';

const MAX_LINES = 40;

export function Captions() {
  const lines = captions.value.lines.slice(-MAX_LINES);
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  useEffect(() => {
    const el = box.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [captions.value]);
  const lastFinal = [...captions.value.lines].reverse().find((l) => l.final && l.who !== 'system');
  return (
    <div
      ref={box}
      class="m captions frame"
      data-morph="core.captions"
      tabIndex={0}
      onScroll={(e) => {
        const el = e.currentTarget;
        pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      }}
    >
      {lines.length === 0 && <div class="cap empty">Start a call or type a message.</div>}
      {lines.map((l) => (
        <div key={l.id} class={`cap ${l.who}${l.final ? '' : ' partial'}`}>
          {l.text}
          {l.viaRoute !== undefined && (
            <span class="via">
              {' '}
              <Bolt />
              {l.viaRoute}ms
            </span>
          )}
        </div>
      ))}
      <div class="sr" aria-live="polite">
        {lastFinal ? `${lastFinal.who === 'user' ? 'You' : 'Jarvis'}: ${lastFinal.text}` : ''}
      </div>
    </div>
  );
}

/** One-line ticker on DECK: the latest final caption, ellipsised. */
export function Ticker() {
  const last = [...captions.value.lines].reverse().find((l) => l.final);
  return (
    <div class="m ticker" data-morph="deck.ticker" aria-hidden="true">
      <span data-label="">{last ? `${last.who === 'user' ? 'YOU>' : last.who === 'jarvis' ? 'JARVIS>' : ''} ${last.text}` : 'NO TRANSCRIPT'}</span>
    </div>
  );
}
