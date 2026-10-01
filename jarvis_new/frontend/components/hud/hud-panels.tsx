'use client';

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useSharedPoll } from '@/lib/shared-poll';

/** Voice-driven HUD panels (src/hud_panels.py, IRONMAN_SPEC §6).
 *  "Show mail" / "hide calendar" change which panels the bridge reports;
 *  this polls GET /panels and draws the open ones. No clicks, no orb:
 *  nothing here is interactive. Renders nothing when no panel is open. */

type Item = Record<string, string | undefined>;
type PanelData = { items?: Item[]; error?: string } & Record<string, unknown>;
type PanelsReply = { ok?: boolean; visible?: string[]; panels?: Record<string, PanelData> };

const POLL_MS = 4000;

const TITLES: Record<string, string> = {
  calendar: 'CALENDAR',
  mail: 'MAIL',
  tasks: 'TASKS',
  drafts: 'DRAFT QUEUE',
  suggestions: 'SUGGESTIONS',
  exams: 'EXAMS',
  system: 'SYSTEM',
};

/** Primary + secondary text for one row, per panel kind. */
function row(panel: string, it: Item): [string, string] {
  switch (panel) {
    case 'calendar':
      return [it.title ?? '', [it.when, it.where].filter(Boolean).join(' · ')];
    case 'mail':
      return [it.subject ?? '', `${it.kind === 'flagged' ? '▲ ' : ''}${it.who ?? ''}`];
    case 'tasks':
      return [it.title ?? '', [it.status, it.progress].filter(Boolean).join(' · ')];
    case 'drafts':
      return [it.subject ?? '', it.to ?? ''];
    default:
      return [it.title ?? '', it.when ?? ''];
  }
}

function SystemBody({ data }: { data: PanelData }) {
  const free = Number(data.disk_free_gb ?? 0);
  const pct = Number(data.disk_free_pct ?? 0);
  const load = Number(data.load_1m ?? 0);
  return (
    <div className="hud-panel__stats">
      <span>
        DISK <b>{free.toFixed(1)} GB</b> free ({pct.toFixed(0)}%)
      </span>
      <span>
        LOAD <b>{load.toFixed(2)}</b>
      </span>
    </div>
  );
}

/** How long a hidden panel plays its exit before it unmounts. */
const LEAVE_MS = 280;

/** Stable identity for a row, so a poll that returns the same items never
 *  remounts them and only genuinely new rows animate in. */
function rowKey(panel: string, it: Item): string {
  const [main, sub] = row(panel, it);
  return `${main}|${sub}`;
}

/** Row keys for a whole list: identical rows (two same notifications) get
 *  #2, #3... so React keys stay unique. Pure. */
function rowKeys(panel: string, items: Item[]): string[] {
  const count: Record<string, number> = {};
  return items.map((it) => {
    const base = rowKey(panel, it);
    count[base] = (count[base] ?? 0) + 1;
    return count[base] === 1 ? base : `${base}#${count[base]}`;
  });
}

type Shown = { name: string; data: PanelData; leaving: boolean };

export const PANEL_TITLES = TITLES;
export { row as panelRow, rowKeys as panelRowKeys };
export type { PanelData, Item as PanelItem };

/** Panel state for any view: the open panels plus the ones still playing
 *  their exit, and which rows are new since the last poll. */
export function usePanelsView(): {
  shown: Shown[];
  fresh: Record<string, Set<string>>;
} {
  const reply = useSharedPoll<PanelsReply>('/panels', POLL_MS);
  const visible = useMemo(() => reply?.visible ?? [], [reply]);

  // Keep a closed panel mounted for its exit animation, with its last data.
  const last = useRef<Record<string, PanelData>>({});
  const [leaving, setLeaving] = useState<string[]>([]);
  const prevVisible = useRef<string[]>([]);
  // Layout effect: mark a closed panel "leaving" before the browser paints,
  // or it would vanish for one frame and then reappear to animate out.
  useLayoutEffect(() => {
    visible.forEach((name) => {
      last.current[name] = reply?.panels?.[name] ?? last.current[name] ?? {};
    });
    const gone = prevVisible.current.filter((n) => !visible.includes(n));
    prevVisible.current = visible;
    if (gone.length === 0) return;
    setLeaving((cur) => [...cur.filter((n) => !visible.includes(n)), ...gone]);
    const t = setTimeout(() => setLeaving((cur) => cur.filter((n) => !gone.includes(n))), LEAVE_MS);
    return () => clearTimeout(t);
  }, [visible, reply]);

  // Rows seen per panel: a row is "fresh" only if its panel was already
  // open and the row wasn't there last time (no flash on first open).
  const seen = useRef<Record<string, Set<string>>>({});
  const fresh = useMemo(() => {
    const out: Record<string, Set<string>> = {};
    visible.forEach((name) => {
      const before = seen.current[name];
      const items = reply?.panels?.[name]?.items ?? [];
      out[name] = new Set(before ? rowKeys(name, items).filter((k) => !before.has(k)) : []);
    });
    return out;
  }, [visible, reply]);
  useEffect(() => {
    const next: Record<string, Set<string>> = {};
    visible.forEach((name) => {
      next[name] = new Set(rowKeys(name, reply?.panels?.[name]?.items ?? []));
    });
    seen.current = next;
  }, [visible, reply]);

  const shown: Shown[] = [
    ...visible.map((name) => ({ name, data: reply?.panels?.[name] ?? {}, leaving: false })),
    ...leaving
      .filter((name) => !visible.includes(name))
      .map((name) => ({ name, data: last.current[name] ?? {}, leaving: true })),
  ];
  return { shown, fresh };
}

export function HudPanels() {
  const { shown, fresh } = usePanelsView();
  if (shown.length === 0) return null;
  return (
    <div className="hud-panels" aria-live="polite" aria-label="Open panels">
      {shown.map(({ name, data, leaving: isLeaving }) => {
        const items = data.items ?? [];
        const keys = rowKeys(name, items);
        return (
          <section
            key={name}
            className="hud-panel"
            data-leaving={isLeaving ? 'true' : undefined}
            aria-hidden={isLeaving ? true : undefined}
            aria-label={TITLES[name] ?? name}
          >
            <header className="hud-panel__head">
              <span className="hud-panel__tag">{TITLES[name] ?? name.toUpperCase()}</span>
              {name !== 'system' && (
                // Keyed on the count so a change replays the pop.
                <span key={items.length} className="hud-panel__count">
                  {items.length}
                </span>
              )}
            </header>
            {name === 'system' ? (
              <SystemBody data={data} />
            ) : items.length === 0 ? (
              <p className="hud-panel__empty">{data.error ? 'Unavailable' : 'Nothing here'}</p>
            ) : (
              <ul className="hud-panel__list">
                {items.map((it, i) => {
                  const [main, sub] = row(name, it);
                  const key = keys[i];
                  return (
                    <li
                      key={key}
                      className="hud-panel__row"
                      data-fresh={fresh[name]?.has(key) ? 'true' : undefined}
                    >
                      <span className="hud-panel__main">{main}</span>
                      {sub && <span className="hud-panel__sub">{sub}</span>}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}
