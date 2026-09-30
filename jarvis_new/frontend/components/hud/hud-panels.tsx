'use client';

import { useEffect, useState } from 'react';
import { bridgeGet } from '@/lib/bridge';

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

export function HudPanels() {
  const [reply, setReply] = useState<PanelsReply | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const next = await bridgeGet<PanelsReply>('/panels');
      if (alive) setReply(next);
    };
    void tick();
    const id = window.setInterval(() => void tick(), POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  const open = reply?.visible ?? [];
  if (open.length === 0) return null;
  return (
    <div className="hud-panels" aria-live="polite" aria-label="Open panels">
      {open.map((name) => {
        const data = reply?.panels?.[name] ?? {};
        const items = data.items ?? [];
        return (
          <section key={name} className="hud-panel" aria-label={TITLES[name] ?? name}>
            <header className="hud-panel__head">
              <span className="hud-panel__tag">{TITLES[name] ?? name.toUpperCase()}</span>
              {name !== 'system' && <span className="hud-panel__count">{items.length}</span>}
            </header>
            {name === 'system' ? (
              <SystemBody data={data} />
            ) : items.length === 0 ? (
              <p className="hud-panel__empty">{data.error ? 'Unavailable' : 'Nothing here'}</p>
            ) : (
              <ul className="hud-panel__list">
                {items.map((it, i) => {
                  const [main, sub] = row(name, it);
                  return (
                    <li key={`${name}-${i}`} className="hud-panel__row">
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
