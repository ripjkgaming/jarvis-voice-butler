'use client';

/** The desk under the comms log: three display cards with the critical
 *  read-outs of the AI usage tracker, the paper market experiment and the
 *  reply drafts awaiting approval. Each reuses the data layer of its full
 *  view (Insights drawer, Drafts window).
 *
 *  VOICE-ONLY: nothing here is clickable. "Check AI usage" / "open my AI
 *  usage" and "open paper trading" open the full Insights screen (agent
 *  tool open_insights -> GET /panels -> useInsights); "open my drafts"
 *  opens the Drafts window. */
import { useEffect, useMemo, useState } from 'react';
import { usePaperMarket } from '@/components/markets/PaperTradingPanel';
import { type UsageSnapshot, estimatedCost, subscribeUsage, tokenCount } from '@/lib/ai-usage';
import { type ReplyDraft } from '@/lib/drafts';
import { decimalNumber, equityChart, money, percentage } from '@/lib/paper-market';
import { useSharedPoll } from '@/lib/shared-poll';
import { Frame } from './frame';
import s from './stark.module.css';

type Tone = 'up' | 'down' | 'warn';

function toneOf(value: string | null | undefined): Tone | undefined {
  const n = decimalNumber(value);
  return n === null || n === 0 ? undefined : n > 0 ? 'up' : 'down';
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: Tone }) {
  return (
    <span className={s.deskStat} data-tone={tone}>
      <small>{label}</small>
      <b>{value}</b>
    </span>
  );
}

/** AI USAGE: today's tokens and spend, this week's spend, top model. */
function UsageCard({ index }: { index: number }) {
  const [today, setToday] = useState<UsageSnapshot | null>(null);
  const [week, setWeek] = useState<UsageSnapshot | null>(null);
  useEffect(() => subscribeUsage('today', setToday), []);
  useEffect(() => subscribeUsage('week', setWeek), []);
  const data = today?.data ?? null;
  const top = useMemo(
    () =>
      [...(data?.models ?? [])].sort((a, b) => (b.total_tokens ?? 0) - (a.total_tokens ?? 0))[0],
    [data]
  );
  return (
    <Frame label="AI USAGE" index={index} aside="TODAY" className={s.deskFrame}>
      {!data ? (
        <p className={s.quiet}>{today?.phase === 'error' ? 'Tracker offline.' : 'Reading…'}</p>
      ) : (
        <div className={s.deskStats}>
          <Stat label="TOKENS" value={tokenCount(data.summary.total_tokens, true)} />
          <Stat label="SPEND" value={estimatedCost(data.summary.estimated_cost_usd)} />
          <Stat
            label="7 DAYS"
            value={week?.data ? estimatedCost(week.data.summary.estimated_cost_usd) : '—'}
          />
        </div>
      )}
      <span className={s.deskSub}>{top ? `TOP · ${top.name}` : 'NO MODEL ACTIVITY YET'}</span>
    </Frame>
  );
}

/** PAPER MARKET: equity, return, P&L, market state and an equity line. */
function MarketCard({ index }: { index: number }) {
  const { snapshot, error } = usePaperMarket(undefined, 0);
  const chart = useMemo(
    () => (snapshot ? equityChart(snapshot.equity_history, 'equity_usd') : null),
    [snapshot]
  );
  const acct = snapshot?.account;
  const ret = acct ? percentage(acct.total_return_pct) : '—';
  const failed = snapshot?.runtime.last_cycle_status === 'error' || !!snapshot?.runtime.last_error;
  return (
    <Frame
      label="PAPER MARKET"
      index={index}
      aside={snapshot ? (snapshot.market.is_open ? 'OPEN' : 'CLOSED') : undefined}
      live={snapshot?.market.is_open}
      className={s.deskFrame}
    >
      {!snapshot || !acct ? (
        <p className={s.quiet}>{error ? 'Market feed offline.' : 'Reading…'}</p>
      ) : (
        <>
          <div className={s.deskStats}>
            <Stat label="EQUITY" value={money(acct.equity_usd)} />
            <Stat
              label="RETURN"
              value={ret === 'Unknown return' ? '—' : ret}
              tone={toneOf(acct.total_return_pct)}
            />
            <Stat
              label="P&L"
              value={money(acct.total_pnl_usd, true)}
              tone={toneOf(acct.total_pnl_usd)}
            />
          </div>
          {chart && chart.count > 1 ? (
            <svg
              className={s.deskSpark}
              viewBox="0 0 720 180"
              preserveAspectRatio="none"
              aria-hidden="true"
              data-tone={toneOf(acct.total_pnl_usd)}
            >
              {chart.segments.map((seg, i) => (
                <polyline key={i} points={seg.map((p) => `${p.x},${p.y}`).join(' ')} />
              ))}
            </svg>
          ) : null}
          <span className={s.deskSub} data-tone={failed ? 'warn' : undefined}>
            {failed
              ? 'LAST CYCLE FAILED'
              : `${snapshot.holdings.length} HOLDINGS · CASH ${money(acct.cash_usd)}`}
          </span>
        </>
      )}
    </Frame>
  );
}

function senderName(d: ReplyDraft): string {
  const raw = (d.sender || d.to || 'Unknown').trim();
  const m = raw.match(/^"?([^"<]+?)"?\s*</);
  return (m ? m[1] : raw).trim();
}

/** DRAFTS: replies awaiting Sir's approval (same feed as the Drafts window). */
function DraftsCard({ index }: { index: number }) {
  const raw = useSharedPoll<{ drafts?: ReplyDraft[] }>('/drafts', 3000);
  const drafts = raw?.drafts ?? null;
  const high = drafts?.filter((d) => d.priority === 'high').length ?? 0;
  return (
    <Frame
      label="AWAITING APPROVAL"
      index={index}
      aside={drafts ? `${drafts.length}${high ? ` · ${high} URGENT` : ''}` : undefined}
      live={high > 0}
      className={s.deskFrame}
    >
      {drafts === null ? (
        <p className={s.quiet}>Reaching the bridge…</p>
      ) : drafts.length === 0 ? (
        <p className={s.quiet}>No drafts waiting, Sir.</p>
      ) : (
        <ul className={s.deskDrafts}>
          {drafts.slice(0, 3).map((d) => (
            <li key={d.id} data-urgent={d.priority === 'high' ? 'true' : undefined}>
              <b>{senderName(d)}</b>
              <span>{d.subject.replace(/^(re:\s*)+/i, '')}</span>
            </li>
          ))}
        </ul>
      )}
    </Frame>
  );
}

export function Desk({ index }: { index: number }) {
  return (
    <div className={s.desk}>
      <UsageCard index={index} />
      <MarketCard index={index + 1} />
      <DraftsCard index={index + 2} />
    </div>
  );
}
