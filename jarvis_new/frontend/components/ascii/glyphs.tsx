'use client';

import { type CSSProperties, type ReactNode, memo } from 'react';
import { barParts, blockChart, fit, lamp, sparkline } from '@/lib/ascii';
import { AsciiWipe } from './ascii-wipe';
import g from './glyphs.module.css';
import { useDecode } from './use-decode';

type Tone = 'ok' | 'warn' | 'bad' | 'up' | 'down' | 'off' | 'amber' | 'ice';

/** Glyph meter `█████▌░░░░`: lit and unlit runs coloured apart. With
 *  `indeterminate`, a `▒▓█▓▒` head sweeps the track (CSS, steps()). */
export const GlyphBar = memo(function GlyphBar({
  pct,
  width = 20,
  tone,
  indeterminate,
  fine = true,
  className,
}: {
  pct: number | null;
  width?: number;
  tone?: Tone;
  indeterminate?: boolean;
  fine?: boolean;
  className?: string;
}) {
  const [on, off] = barParts(indeterminate || pct === null ? 0 : pct, width, { fine });
  return (
    <span
      className={`${g.bar} ${className ?? ''}`}
      data-tone={tone}
      data-indeterminate={indeterminate ? 'true' : undefined}
      style={{ '--cells': width } as CSSProperties}
      aria-hidden="true"
    >
      <span className={g.barOn}>{on}</span>
      <span className={g.barOff}>{off}</span>
      {indeterminate ? <span className={g.barScan}>▒▓█▓▒</span> : null}
    </span>
  );
});

/** `▁▂▄▇█` sparkline over [lo, hi], newest sample at the right. */
export const GlyphSpark = memo(function GlyphSpark({
  values,
  width = 24,
  lo = 0,
  hi = 100,
  tone,
  className,
}: {
  values: number[];
  width?: number;
  lo?: number;
  hi?: number;
  tone?: Tone;
  className?: string;
}) {
  return (
    <span className={`${g.spark} ${className ?? ''}`} data-tone={tone} aria-hidden="true">
      {sparkline(values, width, lo, hi)}
    </span>
  );
});

/** Multi-row block chart (`blockChart`), drawn as stacked text rows with
 *  a dotted baseline. Over [lo, hi], or auto-ranged when omitted. */
export const GlyphChart = memo(function GlyphChart({
  values,
  width = 28,
  rows = 2,
  lo,
  hi,
  tone,
  className,
}: {
  values: number[];
  width?: number;
  rows?: number;
  lo?: number;
  hi?: number;
  tone?: Tone;
  className?: string;
}) {
  const lines = blockChart(values, width, rows, lo, hi);
  return (
    <span className={`${g.chart} ${className ?? ''}`} data-tone={tone} aria-hidden="true">
      {lines.map((l, i) => (
        <span key={i}>{l}</span>
      ))}
      <span className={g.chartBase}>{'┈'.repeat(Math.max(1, Math.floor(width)))}</span>
    </span>
  );
});

/** `[■] LABEL ........ VALUE` status line (a display, never a control). */
export function GlyphLamp({ state }: { state: 'on' | 'warn' | 'off' }) {
  return (
    <span className={g.lamp} data-state={state} aria-hidden="true">
      [{lamp(state)}]
    </span>
  );
}

/** A spoken-command hint: `» "open my drafts"`. Display only. */
export function SayHint({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span className={`${g.say} ${className ?? ''}`}>
      <span aria-hidden="true">» </span>“{children}”
    </span>
  );
}

/** Fixed-width cell for tabular read-outs. */
export function Cell({ text, w, align }: { text: string; w: number; align?: 'l' | 'r' | 'c' }) {
  return <span className={g.cell}>{fit(text, w, align)}</span>;
}

/**
 * Box-drawing panel: `┌─[ LABEL ]──────── ASIDE ─ 01 ─┐`, glyph-true
 * corners and rules (CSS runs of ─, no measuring), hairline sides on the
 * glyph centre line, a decoding title and an optional ASCII wipe-in.
 * Pure layout: no observers, no per-frame work.
 */
export function AsciiBox({
  label,
  index,
  aside,
  live,
  tone,
  heavy,
  wipe = true,
  wipeDelay,
  className,
  bodyClassName,
  headClassName,
  children,
  as: Tag = 'section',
  ...rest
}: {
  label: string;
  index?: number;
  aside?: ReactNode;
  live?: boolean;
  tone?: Tone;
  heavy?: boolean;
  wipe?: boolean;
  wipeDelay?: number;
  className?: string;
  bodyClassName?: string;
  headClassName?: string;
  children?: ReactNode;
  as?: 'section' | 'div' | 'aside' | 'article' | 'header' | 'nav';
} & Omit<React.HTMLAttributes<HTMLElement>, 'children'>) {
  const shown = useDecode(label, 520, 120 + (index ?? 0) * 90);
  const delay = wipeDelay ?? (index ?? 0) * 90;
  const b = heavy ? ['┏━', '━┓', '┗', '┛'] : ['┌─', '─┐', '└', '┘'];
  return (
    <Tag
      className={`${g.box} ${className ?? ''}`}
      aria-label={label}
      data-live={live ? 'true' : undefined}
      data-tone={tone}
      data-heavy={heavy ? 'true' : undefined}
      {...rest}
    >
      <header className={`${g.head} ${headClassName ?? ''}`}>
        <span className={g.edge} aria-hidden="true">
          {b[0]}
        </span>
        <span className={g.title}>
          <span aria-hidden="true">[ </span>
          {shown}
          <span aria-hidden="true"> ]</span>
        </span>
        <span className={g.fill} aria-hidden="true" />
        {aside !== undefined && aside !== null && aside !== '' ? (
          <span className={g.aside}>{aside}</span>
        ) : null}
        {index !== undefined ? (
          <>
            <span className={g.fillShort} aria-hidden="true">
              ─
            </span>
            <span className={g.index}>{String(index).padStart(2, '0')}</span>
          </>
        ) : null}
        <span className={g.edge} aria-hidden="true">
          {b[1]}
        </span>
      </header>
      <div className={`${g.body} ${bodyClassName ?? ''}`}>{children}</div>
      <footer className={g.foot} aria-hidden="true">
        <span className={g.edge}>{b[2]}</span>
        <span className={g.fill} />
        <span className={g.edge}>{b[3]}</span>
      </footer>
      {wipe ? <AsciiWipe delay={delay} /> : null}
    </Tag>
  );
}
