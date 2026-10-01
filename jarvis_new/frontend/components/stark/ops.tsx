'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { PANEL_TITLES, panelRow, panelRowKeys, usePanelsView } from '@/components/hud/hud-panels';
import { type BridgeActivity } from '@/lib/bridge';
import { useSharedPoll } from '@/lib/shared-poll';
import { Frame } from './frame';
import s from './stark.module.css';

const KIND_TAG: Record<string, string> = {
  download: 'DL',
  update: 'UPD',
  coding: 'CODE',
  research: 'RSCH',
  build: 'BUILD',
  task: 'TASK',
};

function fmtDuration(sec: number): string {
  const t = Math.max(0, Math.floor(sec));
  if (t < 60) return `${t}s`;
  if (t < 3600) return `${Math.floor(t / 60)}m ${String(t % 60).padStart(2, '0')}s`;
  return `${Math.floor(t / 3600)}h ${String(Math.floor((t % 3600) / 60)).padStart(2, '0')}m`;
}

function fmtAgo(sec: number): string {
  const t = Math.max(0, Math.floor(sec));
  if (t < 60) return 'NOW';
  if (t < 3600) return `${Math.floor(t / 60)}M AGO`;
  if (t < 86400) return `${Math.floor(t / 3600)}H AGO`;
  return `${Math.floor(t / 86400)}D AGO`;
}

/** Shared /activity poll (downloads, updates, builds, research, tools). */
export function useActivity(): BridgeActivity[] {
  const raw = useSharedPoll<{ items?: BridgeActivity[] }>('/activity', 1000);
  return useMemo(() => (Array.isArray(raw?.items) ? raw.items : []), [raw]);
}

/** EXECUTION: what Jarvis and the machine are doing right now. */
export function Execution({ items, index }: { items: BridgeActivity[]; index: number }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  const running = items.filter((a) => a.status === 'running');
  useEffect(() => {
    setNow(Date.now() / 1000);
    // Elapsed timers tick each second only while something runs.
    const ms = running.length > 0 ? 1000 : 15000;
    const timer = setInterval(() => {
      if (!document.hidden) setNow(Date.now() / 1000);
    }, ms);
    return () => clearInterval(timer);
  }, [items, running.length]);
  const done = items.filter((a) => a.status !== 'running').slice(0, 4);
  return (
    <Frame
      label="EXECUTION"
      index={index}
      live={running.length > 0}
      aside={`${running.length} LIVE`}
      className={s.execFrame}
    >
      {items.length === 0 ? (
        <p className={s.quiet}>All systems quiet. Awaiting instruction, Sir.</p>
      ) : (
        <ul className={s.exec}>
          {running.slice(0, 4).map((a) => {
            const pct = a.progress == null ? null : Math.max(0, Math.min(100, a.progress));
            return (
              <li key={a.id} className={s.execRow} data-running="true">
                <span className={s.execTag}>{KIND_TAG[a.kind] ?? 'TASK'}</span>
                <span className={s.execTitle}>{a.title}</span>
                <span className={s.execTime}>
                  {pct !== null ? `${Math.round(pct)}% · ` : ''}
                  {fmtDuration(now - a.started)}
                </span>
                <span
                  className={s.execBar}
                  data-indeterminate={pct === null ? 'true' : undefined}
                  aria-hidden="true"
                >
                  <i style={pct !== null ? { width: `${pct}%` } : undefined} />
                </span>
                {a.detail ? <span className={s.execDetail}>{a.detail}</span> : null}
              </li>
            );
          })}
          {done.map((a) => (
            <li key={a.id} className={s.execRow} data-status={a.status}>
              <span className={s.execMark} aria-hidden="true">
                {a.status === 'done' ? '✓' : a.status === 'cancelled' ? '–' : '✕'}
              </span>
              <span className={s.execTitle}>{a.title}</span>
              <span className={s.execTime}>{fmtAgo(now - (a.finished ?? a.updated))}</span>
            </li>
          ))}
        </ul>
      )}
    </Frame>
  );
}

/** Voice panels ("show mail", "show calendar"): one frame per open panel,
 *  or a single hint line naming the commands when none is open. */
export function VoicePanels({ index }: { index: number }) {
  const { shown, fresh } = usePanelsView();
  if (shown.length === 0) {
    return (
      <div className={s.panelHint}>
        <span className={s.panelHintLabel}>PANELS</span>
        <span>SAY “SHOW MAIL” · “SHOW CALENDAR” · “SHOW TASKS”</span>
      </div>
    );
  }
  return (
    <div className={s.panels}>
      {shown.map(({ name, data, leaving }, i) => {
        const items = data.items ?? [];
        const keys = panelRowKeys(name, items);
        return (
          <Frame
            key={name}
            label={PANEL_TITLES[name] ?? name.toUpperCase()}
            index={index + i}
            aside={name === 'system' ? undefined : String(items.length)}
            className={`${s.panelFrame} ${leaving ? s.leaving : ''}`}
          >
            {name === 'system' ? (
              <p className={s.panelStat}>
                DISK <b>{Number(data.disk_free_gb ?? 0).toFixed(1)} GB</b> FREE · LOAD{' '}
                <b>{Number(data.load_1m ?? 0).toFixed(2)}</b>
              </p>
            ) : items.length === 0 ? (
              <p className={s.quiet}>{data.error ? 'Unavailable.' : 'Nothing here, Sir.'}</p>
            ) : (
              <ul className={s.panelList}>
                {items.map((it, k) => {
                  const [main, sub] = panelRow(name, it);
                  return (
                    <li
                      key={keys[k]}
                      className={s.panelRow}
                      data-fresh={fresh[name]?.has(keys[k]) ? 'true' : undefined}
                    >
                      <span className={s.panelMain}>{main}</span>
                      {sub ? <span className={s.panelSub}>{sub}</span> : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </Frame>
        );
      })}
    </div>
  );
}

type LogRow = { time: string; tag: string; text: string; warn: boolean };

const QUIET_TAGS = /^(latency|hud:|wa-autoreply|mailwatch|presence)/;

/** One actions.log line -> time, tag, text. Pure. */
export function parseLogLine(line: string): LogRow {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})\s+(\S+)\s*(.*)$/.exec(line.trim());
  if (!m) return { time: '', tag: '', text: line.trim(), warn: false };
  const text = m[4];
  return {
    time: m[2],
    tag: m[3].toUpperCase(),
    text,
    warn: /\b(error|fail(ed)?|ok=False|denied|refused|rejected)\b/i.test(text),
  };
}

/** COMMS LOG: Jarvis's own actions (actions.log), newest at the bottom.
 *  Chatty plumbing (latency, HUD mirrors, watchers) stays out of view. */
export function CommsLog({ index }: { index: number }) {
  const raw = useSharedPoll<{ actions?: string[] }>('/actions?limit=80', 2000);
  const rows = useMemo(() => {
    const lines = raw?.actions ?? [];
    return lines
      .filter((l) => {
        const tag = l.split(/\s+/)[1] ?? '';
        return !QUIET_TAGS.test(tag);
      })
      .slice(-40)
      .map(parseLogLine);
  }, [raw]);
  const box = useRef<HTMLOListElement>(null);
  useEffect(() => {
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [rows]);
  return (
    <Frame label="COMMS LOG" index={index} className={s.logFrame} bodyClassName={s.logBody}>
      {rows.length === 0 ? (
        <p className={s.quiet}>Log quiet, Sir.</p>
      ) : (
        <ol ref={box} className={s.log}>
          {rows.map((r, i) => (
            <li
              key={`${i}-${r.time}-${r.tag}`}
              className={s.logRow}
              data-warn={r.warn ? 'true' : undefined}
            >
              <span className={s.logTime}>{r.time}</span>
              <span className={s.logTag}>{r.tag}</span>
              <span className={s.logText}>{r.text}</span>
            </li>
          ))}
        </ol>
      )}
    </Frame>
  );
}
