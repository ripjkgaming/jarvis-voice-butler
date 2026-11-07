'use client';

import { memo, useEffect, useRef, useState } from 'react';
import { useAscii } from '@/lib/character/hooks';
import { AwareGlyph } from './aware-glyph';
import { type Metrics } from './metrics';
import type { Job } from './reactor-progress';
import s from './stark.module.css';
import type { PresenceState } from './use-hud-presence';

/** How long a finished job's row stays (flashing DONE) before it fades out. */
const DONE_MS = 2200;
/** Rows shown at once; the rest collapse into "+N". */
const MAX_ROWS = 4;
const BAR_CELLS = 14;

type Row = Job & { done?: number };

const gb = (bytes: number): number => bytes / 1024 ** 3;

/** RAM as "12.3/31 GB": the unit is part of the value cell and never clipped. */
function ramText(m: Metrics): string {
  if (!m.has || !m.memTotal) return '—';
  const total = gb(m.memTotal);
  return `${gb(m.memUsed).toFixed(1)}/${total >= 10 ? Math.round(total) : total.toFixed(1)} GB`;
}

function netText(m: Metrics): string {
  if (!m.has || !m.net) return '—';
  if (m.net.kind === 'none') return 'OFFLINE';
  const kind = m.net.kind === 'wifi' ? 'WIFI' : 'ETH';
  return m.net.signal != null && m.net.kind === 'wifi'
    ? `${kind} ${Math.round(m.net.signal)}%`
    : kind;
}

function bar(pct: number): string {
  const n = Math.round((Math.max(0, Math.min(100, pct)) / 100) * BAR_CELLS);
  return '█'.repeat(n) + '░'.repeat(BAR_CELLS - n);
}

const pad2 = (n: number) => String(n).padStart(2, '0');

/** One stat cell: label over value; the value element is re-keyed on change so
 *  a changed reading flashes brighter once, then settles back to the dim look. */
function Cell({ label, value }: { label: string; value: string }) {
  return (
    <div className={s.badgeCell}>
      <small>{label}</small>
      <b key={value}>{value}</b>
    </div>
  );
}

/** Local clock, re-rendered every 5 s (cheap: HH:MM only). */
function useMinuteClock(on: boolean): string {
  const [text, setText] = useState('--:--');
  useEffect(() => {
    if (!on) return;
    const tick = () => {
      const d = new Date();
      setText(`${pad2(d.getHours())}:${pad2(d.getMinutes())}`);
    };
    tick();
    const t = setInterval(tick, 5000);
    return () => clearInterval(t);
  }, [on]);
  return text;
}

/** Merge the live jobs into display rows; a job that vanishes flashes DONE. */
function useRows(jobs: Job[], on: boolean): Row[] {
  const [rows, setRows] = useState<Row[]>([]);
  const last = useRef<Map<string, Row>>(new Map());
  useEffect(() => {
    if (!on) return;
    const now = Date.now();
    const live = new Map(jobs.map((j) => [j.id, j]));
    const next: Row[] = jobs.map((j) => ({ ...j }));
    last.current.forEach((r, id) => {
      if (live.has(id)) return;
      if (r.done !== undefined) {
        if (now - r.done < DONE_MS) next.push(r);
      } else {
        next.push({ ...r, pct: 100, done: now });
      }
    });
    last.current = new Map(next.map((r) => [r.id, r]));
    setRows(next);
  }, [jobs, on]);
  // expire finished rows
  useEffect(() => {
    if (!rows.some((r) => r.done !== undefined)) return;
    const t = setTimeout(() => {
      const now = Date.now();
      const keep = rows.filter((r) => r.done === undefined || now - r.done < DONE_MS);
      last.current = new Map(keep.map((r) => [r.id, r]));
      setRows(keep);
    }, 400);
    return () => clearTimeout(t);
  }, [rows]);
  return rows;
}

/**
 * Corner "standby" badge: what the window shows when Jarvis has faded into the
 * wallpaper. A wide, low-opacity strip of live stats (only values the bridge
 * /sys snapshot really reports; missing ones read "—") and, above it, the
 * background tasks he is running (same data as the reactor progress arc),
 * which slide in when they start, update live and flash DONE when finished.
 * It never wakes the HUD and never takes pointer events.
 *
 * Mounted only while the HUD is not fully present (fade-in while he fades out,
 * fade-out while he wakes); the HUD root's presence engine drives its opacity.
 */
export const StandbyBadge = memo(function StandbyBadge({
  presence,
  m,
  jobs,
}: {
  presence: PresenceState;
  m: Metrics;
  jobs: Job[];
}) {
  const on = presence !== 'present';
  const ascii = useAscii();
  const time = useMinuteClock(on);
  const rows = useRows(jobs, on);
  if (!on) return null;
  const shown = rows.slice(0, MAX_ROWS);
  const extra = rows.length - shown.length;
  return (
    <aside
      className={s.badge}
      data-ix-badge
      aria-hidden="true"
      data-ascii={ascii ? 'true' : undefined}
    >
      {rows.length > 0 ? (
        <ul className={s.badgeJobs}>
          {extra > 0 ? <li className={s.badgeMore}>+{extra} MORE</li> : null}
          {shown.map((r) => (
            <li
              key={r.id}
              className={s.badgeJob}
              data-done={r.done !== undefined ? 'true' : undefined}
            >
              <span className={s.badgeTag}>{r.tag}</span>
              <span className={s.badgeTitle}>{r.title}</span>
              <span className={s.badgeStage}>{r.done !== undefined ? 'DONE' : r.stage}</span>
              <span className={s.badgeBar} data-indeterminate={r.pct === null ? 'true' : undefined}>
                {r.pct === null ? '▒▒▒▒▒▒▒▒▒▒▒▒▒▒' : bar(r.pct)}
              </span>
              <span className={s.badgePct}>{r.pct === null ? '··' : `${Math.round(r.pct)}%`}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <div className={s.badgeHead}>
        <i className={s.badgePip} />
        <span>J.A.R.V.I.S. · STANDBY</span>
        <AwareGlyph variant="badge" />
      </div>
      <div className={s.badgeStats}>
        <Cell label="CPU" value={m.has ? `${Math.round(m.cpuPct)}%` : '—'} />
        <Cell label="RAM" value={ramText(m)} />
        <Cell label="TEMP" value={m.temp === null ? '—' : `${Math.round(m.temp)}°C`} />
        <Cell label="NET" value={netText(m)} />
        <Cell label="TIME" value={time} />
      </div>
    </aside>
  );
});
