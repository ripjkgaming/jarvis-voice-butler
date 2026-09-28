'use client';

import { type ReactNode, useEffect, useState } from 'react';
import { type BridgeActivity, bridgeActivity } from '@/lib/bridge';

/** Rows the panel holds: running first, then the freshest finished. */
const MAX_RUNNING = 5;
const MAX_DONE = 3;
const POLL_MS = 1000;

const KIND_LABEL: Record<string, string> = {
  download: 'DL',
  update: 'UPD',
  coding: 'CODE',
  research: 'RSCH',
  build: 'BUILD',
  task: 'TASK',
};

/** Stroke glyphs, 14px, currentColor: one per activity kind. */
const GLYPHS: Record<string, ReactNode> = {
  download: <path d="M7 2v7m0 0L4 6m3 3 3-3M2.5 12h9" />,
  update: (
    <path d="M11.5 5.5A4.6 4.6 0 0 0 3 4.3M2.5 8.5A4.6 4.6 0 0 0 11 9.7M3 1.8v2.6h2.6M11 12.2V9.6H8.4" />
  ),
  coding: <path d="M5 4 2 7l3 3m4-6 3 3-3 3M8 2.5 6 11.5" />,
  research: <path d="M6 10.5a4.5 4.5 0 1 1 0-9 4.5 4.5 0 0 1 0 9Zm3.2-1.3L12.5 12.5" />,
  build: <path d="M2 11.5h10M3.5 11.5V6l3.5-3 3.5 3v5.5M5.8 11.5V8.5h2.4v3" />,
  task: <path d="M4.5 3 9 7l-4.5 4" />,
};

function fmtDuration(s: number): string {
  const t = Math.max(0, Math.floor(s));
  if (t < 60) return `${t}s`;
  if (t < 3600) return `${Math.floor(t / 60)}m${String(t % 60).padStart(2, '0')}s`;
  return `${Math.floor(t / 3600)}h${String(Math.floor((t % 3600) / 60)).padStart(2, '0')}m`;
}

function fmtAgo(s: number): string {
  const t = Math.max(0, Math.floor(s));
  if (t < 60) return 'just now';
  if (t < 3600) return `${Math.floor(t / 60)}m ago`;
  return `${Math.floor(t / 3600)}h ago`;
}

function Glyph({ kind }: { kind: string }) {
  return (
    <svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true">
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {GLYPHS[kind] ?? GLYPHS.task}
      </g>
    </svg>
  );
}

function RunningRow({ a, now }: { a: BridgeActivity; now: number }) {
  const pct = a.progress == null ? null : Math.max(0, Math.min(100, a.progress));
  return (
    <li className="hud-act" data-kind={a.kind}>
      <span className="hud-act__glyph">
        <Glyph kind={a.kind} />
      </span>
      <div className="hud-act__body">
        <div className="hud-act__top">
          <span className="hud-act__tag">{KIND_LABEL[a.kind] ?? 'TASK'}</span>
          <span className="hud-act__title">{a.title}</span>
          <span className="hud-act__num">
            {pct != null ? `${Math.round(pct)}%` : ''}
            <span className="hud-act__time">{fmtDuration(now - a.started)}</span>
          </span>
        </div>
        {a.detail ? <div className="hud-act__detail">{a.detail}</div> : null}
        <div
          className="hud-act__bar"
          data-indeterminate={pct == null ? 'true' : undefined}
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct ?? undefined}
          aria-label={a.title}
        >
          <i style={pct != null ? { width: `${pct}%` } : undefined} />
        </div>
      </div>
    </li>
  );
}

function DoneRow({ a, now }: { a: BridgeActivity; now: number }) {
  const ok = a.status === 'done';
  const mark = ok ? '✓' : a.status === 'cancelled' ? '–' : '✕';
  return (
    <li className="hud-act hud-act--done" data-kind={a.kind} data-status={a.status}>
      <span className="hud-act__mark" aria-hidden="true">
        {mark}
      </span>
      <span className="hud-act__title">{a.title}</span>
      <span className="hud-act__time">
        {ok ? '' : `${a.status} · `}
        {fmtAgo(now - (a.finished ?? a.updated))}
      </span>
    </li>
  );
}

/** EXECUTION: live system activity (downloads, updates, coding and
 *  research projects, Jarvis tools) from the bridge's /activity feed.
 *  Read-only and voice-first: nothing here needs a pointer. */
export function ExecFeed() {
  const [items, setItems] = useState<BridgeActivity[]>([]);
  const [now, setNow] = useState(() => Date.now() / 1000);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      const next = await bridgeActivity();
      if (cancelled) return;
      if (next) setItems(next);
      setNow(Date.now() / 1000);
      timer = setTimeout(tick, POLL_MS);
    };
    void tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  const running = items.filter((a) => a.status === 'running');
  const done = items.filter((a) => a.status !== 'running');
  const shownRunning = running.slice(0, MAX_RUNNING);
  const shownDone = done.slice(0, MAX_DONE);
  const hidden = running.length - shownRunning.length;

  return (
    <div className="hud-exec" data-busy={running.length > 0 ? 'true' : undefined}>
      <div className="hud-exec__head">
        <span>EXECUTION</span>
        <span className="hud-exec__count">
          {running.length} live{done.length ? ` · ${done.length} done` : ''}
        </span>
      </div>
      {items.length === 0 ? (
        <p className="hud-exec__empty">All systems quiet. Waiting for your next instruction.</p>
      ) : (
        <ul className="hud-act__list">
          {shownRunning.map((a) => (
            <RunningRow key={a.id} a={a} now={now} />
          ))}
          {hidden > 0 ? <li className="hud-act__more">+{hidden} more running</li> : null}
          {shownDone.map((a) => (
            <DoneRow key={a.id} a={a} now={now} />
          ))}
        </ul>
      )}
    </div>
  );
}
