/** §2.4 tile: label + lamp, real readback value, age and ⌬ when the action
 * needs a fingerprint. Micro motion runs on the inner wrapper only (the tile
 * itself is a morph element). */
import type { ComponentChildren } from 'preact';
import { fmtAge, type ActionState } from '../store';
import { Decode } from './Decode';
import { Print } from './glyphs';

export type TileProps = {
  id: string;
  label: string;
  value: string | null;
  at: number;
  now: number;
  state?: ActionState;
  fingerprint?: boolean;
  disabled?: boolean;
  cls?: string;
  aria: string;
  pressed?: boolean;
  onTap: () => void;
  children?: ComponentChildren;
  tone?: string;
};

export function Tile(p: TileProps) {
  const phase = p.state?.phase ?? 'idle';
  const failedRecently = phase === 'failed' && p.now - (p.state?.at ?? 0) < 2000;
  const tone = failedRecently ? 'off' : p.value === null ? 'off' : phase === 'warn' ? 'warn' : 'on';
  const lampTxt = phase === 'pending' ? 'Working…' : failedRecently ? '○' : p.value === null ? '--' : phase === 'warn' ? '◐' : '●';
  return (
    <button
      class={`m tile frame ticks ${p.cls ?? ''}${failedRecently ? ' fail' : ''}`}
      data-morph={`deck.tile.${p.id}`}
      aria-label={p.aria}
      aria-pressed={p.pressed}
      aria-busy={phase === 'pending'}
      disabled={p.disabled}
      onClick={p.onTap}
    >
      <div class="tile-in" data-label="">
        <div class="tile-head">
          <Decode cls="tile-label" text={p.label} />
          <span class={`tile-lamp t-${tone}`} aria-hidden="true">
            {lampTxt}
          </span>
        </div>
        <span class={`tile-val${p.value === null ? ' dim' : ''}`} aria-hidden="true">
          <Decode text={p.value ?? '--'} />
        </span>
        <div class="tile-foot" aria-hidden="true">
          <span>{p.value === null || !p.at ? '--' : fmtAge(p.now - p.at)}</span>
          {p.fingerprint && <Print cls="fp" />}
        </div>
        {p.children}
      </div>
    </button>
  );
}
