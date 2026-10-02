'use client';

import { type CSSProperties, type KeyboardEvent, useEffect, useRef } from 'react';
import dynamic from 'next/dynamic';
import type { InsightsTab } from '@/hooks/hud/use-insights';
import s from './insights.module.css';

const Loading = () => (
  <div className={s.loading} role="status">
    Opening workspace…
  </div>
);
const AiUsagePanel = dynamic(() => import('./AiUsagePanel'), { ssr: false, loading: Loading });
const PaperTradingPanel = dynamic(
  () => import('@/components/markets/PaperTradingPanel').then((module) => module.PaperTradingPanel),
  { ssr: false, loading: Loading }
);
const TABS = [
  { id: 'usage', label: 'AI Usage', code: '01', description: 'Tokens & estimated spend' },
  { id: 'market', label: 'Paper Market', code: '02', description: 'Portfolio & performance' },
] as const;

export default function InsightsDrawer({
  tab,
  onTab,
  onClose,
  school,
  barHeight,
  returnFocus,
}: {
  tab: InsightsTab;
  onTab: (tab: InsightsTab) => void;
  onClose: () => void;
  school: boolean;
  barHeight: number;
  returnFocus: HTMLElement | null;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const tabs = useRef(new Map<InsightsTab, HTMLButtonElement>());
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const previous = returnFocus ?? document.activeElement;
    closeButton.current?.focus({ preventScroll: true });
    const keydown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopImmediatePropagation();
        close.current();
        return;
      }
      if (event.key !== 'Tab' || !dialog.current) return;
      const candidates = Array.from(
        dialog.current.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])'
        )
      ).filter((element) => element.tabIndex >= 0 && element.getClientRects().length > 0);
      const first = candidates[0];
      const last = candidates.at(-1);
      if (!first || !last) {
        event.preventDefault();
        dialog.current.focus();
      } else if (
        event.shiftKey &&
        (document.activeElement === first || !dialog.current.contains(document.activeElement))
      ) {
        event.preventDefault();
        last.focus();
      } else if (
        !event.shiftKey &&
        (document.activeElement === last || !dialog.current.contains(document.activeElement))
      ) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', keydown, true);
    return () => {
      document.removeEventListener('keydown', keydown, true);
      if (previous instanceof HTMLElement && previous.isConnected && !previous.closest('[inert]')) {
        previous.focus({ preventScroll: true });
      } else {
        document
          .querySelector<HTMLElement>(school ? '.sbar-start' : '[data-insights-launch="usage"]')
          ?.focus({ preventScroll: true });
      }
    };
  }, [school, returnFocus]);

  const tabKeys = (event: KeyboardEvent) => {
    let next: InsightsTab;
    if (event.key === 'Home') next = 'usage';
    else if (event.key === 'End') next = 'market';
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight')
      next = tab === 'usage' ? 'market' : 'usage';
    else return;
    event.preventDefault();
    onTab(next);
    tabs.current.get(next)?.focus();
  };

  return (
    <div
      className={s.overlay}
      data-insights="true"
      data-school={school || undefined}
      style={{ '--insights-bar-height': `${school ? barHeight : 0}px` } as CSSProperties}
    >
      <div
        ref={dialog}
        className={s.dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="insights-title"
        tabIndex={-1}
        onKeyDown={(event) => event.stopPropagation()}
      >
        <header className={s.header}>
          <div>
            <span className={s.eyebrow}>J.A.R.V.I.S. / INTELLIGENCE</span>
            <h1 id="insights-title">Insights</h1>
          </div>
          <span className={s.context}>PERSONAL OPERATIONS</span>
          <button
            type="button"
            ref={closeButton}
            className={s.close}
            onClick={onClose}
            aria-label="Close Insights"
          >
            <span aria-hidden="true">×</span> Close <kbd>ESC</kbd>
          </button>
        </header>
        <div className={s.tabs} role="tablist" aria-label="Insights panels" onKeyDown={tabKeys}>
          {TABS.map((item) => (
            <button
              key={item.id}
              ref={(element) => {
                if (element) tabs.current.set(item.id, element);
                else tabs.current.delete(item.id);
              }}
              type="button"
              role="tab"
              id={`insights-tab-${item.id}`}
              aria-selected={tab === item.id}
              aria-controls="insights-panel"
              tabIndex={tab === item.id ? 0 : -1}
              onClick={() => onTab(item.id)}
            >
              <span className={s.tabCode}>{item.code}</span>
              <span>
                <b>{item.label}</b>
                <small>{item.description}</small>
              </span>
              <span className={s.tabMark} aria-hidden="true">
                ↗
              </span>
            </button>
          ))}
        </div>
        <div
          className={s.content}
          id="insights-panel"
          role="tabpanel"
          aria-labelledby={`insights-tab-${tab}`}
        >
          {tab === 'usage' ? <AiUsagePanel /> : <PaperTradingPanel embedded onClose={onClose} />}
        </div>
      </div>
    </div>
  );
}
