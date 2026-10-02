'use client';

import type { InsightsTab } from '@/hooks/hud/use-insights';
import s from './insights.module.css';

export function InsightsLaunchers({
  onOpen,
  menu = false,
}: {
  onOpen: (tab: InsightsTab) => void;
  menu?: boolean;
}) {
  return (
    <nav className={s.launchers} data-menu={menu || undefined} aria-label="Insights">
      <button
        type="button"
        data-insights-launch="usage"
        onClick={() => onOpen('usage')}
        aria-haspopup="dialog"
      >
        <svg viewBox="0 0 20 20" aria-hidden="true">
          <path d="M3 16V9m7 7V3m7 13v-5" />
        </svg>
        <span>AI Usage{menu && <small>Tokens & estimated spend</small>}</span>
      </button>
      <button
        type="button"
        data-insights-launch="market"
        onClick={() => onOpen('market')}
        aria-haspopup="dialog"
      >
        <svg viewBox="0 0 20 20" aria-hidden="true">
          <path d="m2 14 5-5 4 3 7-9m-6 0h6v6" />
        </svg>
        <span>Paper Market{menu && <small>Portfolio & performance</small>}</span>
      </button>
    </nav>
  );
}
