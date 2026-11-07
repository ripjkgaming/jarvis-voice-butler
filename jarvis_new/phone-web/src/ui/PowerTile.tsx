/** §2.4 POWER: battery/AC readback; tap expands (FLIP scaleY with counter-
 * scaled children, micro spring) to SLEEP (tap), RESTART and SHUTDOWN
 * (hold 1200 ms, then fingerprint). */
import { fmtAge, type ActionState, type Lamp, type Power } from '../store';
import { Decode } from './Decode';
import { Bolt, Print } from './glyphs';
import { HoldButton } from './HoldButton';

export function PowerTile({
  power,
  now,
  open,
  disabled,
  states,
  onToggle,
  onAction,
}: {
  power: Lamp<Power>;
  now: number;
  open: boolean;
  disabled: boolean;
  states: Record<string, ActionState | undefined>;
  onToggle: () => void;
  onAction: (a: 'sleep' | 'restart' | 'shutdown') => void;
  onResized: () => void;
}) {
  const p = power.value;
  const value = p === null ? '--' : `${p.percent}%`;

  const pending = ['sleep', 'restart', 'shutdown'].some((a) => states[a]?.phase === 'pending');
  return (
    <div class={`m tile power wide r2 frame ticks${open ? ' open' : ''}`} data-morph="deck.tile.POWER">
      <div data-label="">
        <button
          class="power-head"
          aria-expanded={open}
          aria-label={`Laptop power, ${p === null ? 'unknown' : `${p.percent} percent${p.charging ? ', charging' : ''}`}. ${open ? 'Hide' : 'Show'} sleep, restart and shutdown.`}
          onClick={onToggle}
        >
          <div class="tile-head">
            <Decode cls="tile-label" text="POWER" />
            <span class={`tile-lamp t-${p !== null && !p.charging && p.percent <= 20 ? 'warn' : 'on'}`} aria-hidden="true">
              {pending ? '…' : p === null ? '--' : p.charging || p.percent > 20 ? '●' : '◐'}
            </span>
          </div>
          <span class={`tile-val${p === null ? ' dim' : ''}`} aria-hidden="true">
            <Decode text={value} />
            {p?.charging && <Bolt />}
            <small class="ac">{p === null ? '' : p.charging ? ' AC' : ' BAT'}</small>
          </span>
          <div class="tile-foot" aria-hidden="true">
            <span>{p === null || !power.at ? '--' : fmtAge(now - power.at)}</span>
            <Print cls="fp" />
          </div>
        </button>
        <div class="holds" hidden={!open}>
          <HoldButton tap label="SLEEP" aria="Sleep the laptop" disabled={disabled} onFire={() => onAction('sleep')} />
          <HoldButton label="RESTART" aria="Restart the laptop. Hold, then fingerprint." disabled={disabled} onFire={() => onAction('restart')} />
          <HoldButton label="SHUTDOWN" aria="Shut down the laptop. Hold, then fingerprint." disabled={disabled} onFire={() => onAction('shutdown')} />
        </div>
      </div>
    </div>
  );
}
