'use client';

import { memo } from 'react';
import { useSharedPoll } from '@/lib/shared-poll';
import s from './stark.module.css';

/** Bridge GET /aware/status (src/aware/sidecar.py status()). */
type AwareStatus = {
  ok?: boolean;
  state?: 'off' | 'paused' | 'armed' | 'capturing' | 'skipped' | 'offer' | 'takeover';
  shadow?: boolean;
  next_tick_s?: number | null;
  last_reason?: string;
  calls_today?: number;
};

/** One ASCII glyph per state (docs/always-aware/PLAN.md §6). `◉` is the
 *  honesty light: lit only while a capture burst is running. */
const GLYPH: Record<NonNullable<AwareStatus['state']>, string> = {
  off: '·',
  paused: '‖',
  armed: '○',
  capturing: '◉',
  skipped: '◌',
  offer: '?',
  takeover: '◎',
};

const WORD: Record<NonNullable<AwareStatus['state']>, string> = {
  off: 'OFF',
  paused: 'PAUSED',
  armed: 'ARMED',
  capturing: 'SEEING',
  skipped: 'SKIP',
  offer: 'OFFER',
  takeover: 'ON IT',
};

const pad2 = (n: number) => String(n).padStart(2, '0');

function countdown(secs: number | null | undefined): string {
  if (secs == null || secs < 0) return '';
  return `${pad2(Math.floor(secs / 60))}:${pad2(Math.floor(secs % 60))}`;
}

/**
 * Always Aware indicator: a single glyph cell, voice-controlled only. It has
 * no pointer targets (pointer-events: none) and never wakes the HUD. When the
 * feature is off it shows a dim `·` so its absence is honest, not hidden.
 * `variant="badge"` is the inline form used inside the standby badge head.
 */
export const AwareGlyph = memo(function AwareGlyph({
  variant = 'hud',
}: {
  variant?: 'hud' | 'badge';
}) {
  const st = useSharedPoll<AwareStatus>('/aware/status', 1500);
  const state = st?.ok && st.state && st.state in GLYPH ? st.state : 'off';
  const warn = state === 'offer' || state === 'takeover';
  const tail =
    state === 'armed'
      ? countdown(st?.next_tick_s)
      : state === 'skipped'
        ? (st?.last_reason || '').replace(/_/g, ' ').toUpperCase().slice(0, 18)
        : '';
  return (
    <span
      className={variant === 'badge' ? s.awareBadge : s.aware}
      data-aware={state}
      data-warn={warn ? 'true' : undefined}
      data-shadow={st?.shadow ? 'true' : undefined}
      aria-hidden="true"
    >
      <b className={s.awareGlyph}>{GLYPH[state]}</b>
      <span className={s.awareWord}>
        AWARE{st?.shadow && state !== 'off' ? '·SHADOW' : ''} {WORD[state]}
      </span>
      {tail && variant === 'hud' ? <small className={s.awareTail}>{tail}</small> : null}
    </span>
  );
});
