'use client';

import { useEffect, useId, useMemo, useState } from 'react';
import { bridgePaperMarket } from '@/lib/bridge';
import {
  type ChartMetric,
  type PaperMarketSnapshot,
  ageLabel,
  asciiBar,
  decimalNumber,
  equityChart,
  money,
  parsePaperMarketSnapshot,
  percentage,
  portfolioBlockers,
  trendGlyph,
  usageCost,
} from '@/lib/paper-market';
import styles from './paper-trading-panel.module.css';

export type { PaperMarketSnapshot } from '@/lib/paper-market';

const human = (value: string | null) => (value ? value.replace(/_/g, ' ') : 'Not recorded');
const tone = (value: string | null | undefined) => {
  const number = decimalNumber(value);
  return number === null || number === 0 ? '' : number > 0 ? styles.positive : styles.negative;
};
function dateLabel(value: string | null, full = false): string {
  if (!value || !Number.isFinite(Date.parse(value))) return 'Not recorded';
  return new Intl.DateTimeFormat('en-SG', {
    timeZone: 'Asia/Singapore',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    ...(full ? { timeZoneName: 'short' as const } : {}),
  }).format(new Date(value));
}
function quantity(value: string): string {
  const number = decimalNumber(value);
  return number === null ? 'Unknown' : number.toLocaleString('en-US', { maximumFractionDigits: 6 });
}
function remaining(expires: string | null, now: number): string {
  if (!expires) return 'Start time pending';
  const ms = Date.parse(expires) - now;
  if (!now || !Number.isFinite(ms)) return '7-day experiment';
  if (ms <= 0) return 'Experiment ended';
  const hours = Math.ceil(ms / 3_600_000);
  return hours >= 24
    ? `${Math.floor(hours / 24)}d ${hours % 24}h remaining`
    : `${hours}h remaining`;
}

/** Poll failures are visible even after a good read. Hidden panels stop polling. */
export function usePaperMarket(
  override: PaperMarketSnapshot | undefined,
  refresh: number,
  enabled = true
) {
  const [snapshot, setSnapshot] = useState<PaperMarketSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState(0);
  useEffect(() => {
    let clock: ReturnType<typeof setInterval> | undefined;
    const visibility = () => {
      clearInterval(clock);
      if (document.hidden) return;
      setNow(Date.now());
      clock = setInterval(() => setNow(Date.now()), 15_000);
    };
    visibility();
    document.addEventListener('visibilitychange', visibility);
    return () => {
      clearInterval(clock);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, []);
  useEffect(() => {
    if (override !== undefined || !enabled) return;
    let stopped = false;
    let inFlight = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | null = null;
    async function poll() {
      if (stopped || inFlight || document.hidden) return;
      inFlight = true;
      const request = new AbortController();
      controller = request;
      setLoading(true);
      try {
        const result = await bridgePaperMarket(request.signal);
        if (stopped || request.signal.aborted) return;
        const next = parsePaperMarketSnapshot(result);
        if (next) {
          setSnapshot(next);
          setError(null);
        } else {
          setError(
            result === null
              ? 'Paper market service is unavailable. Retrying automatically.'
              : 'Paper market returned an invalid snapshot. Retrying automatically.'
          );
        }
        setNow(Date.now());
      } catch {
        if (!stopped && !request.signal.aborted)
          setError('Paper market service is unavailable. Retrying automatically.');
      } finally {
        inFlight = false;
        if (!stopped) {
          setLoading(false);
          if (!document.hidden) timer = setTimeout(poll, request.signal.aborted ? 0 : 15_000);
        }
      }
    }
    const visibility = () => {
      clearTimeout(timer);
      if (document.hidden) {
        controller?.abort();
      } else {
        setNow(Date.now());
        void poll();
      }
    };
    document.addEventListener('visibilitychange', visibility);
    void poll();
    return () => {
      stopped = true;
      controller?.abort();
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, [override, refresh, enabled]);
  if (override !== undefined) {
    const valid = parsePaperMarketSnapshot(override);
    return {
      snapshot: valid,
      error: valid ? null : 'Paper market snapshot is invalid.',
      loading: false,
      now,
    };
  }
  return { snapshot, error, loading, now };
}

function EquityChart({ snapshot }: { snapshot: PaperMarketSnapshot }) {
  const [metric, setMetric] = useState<ChartMetric>('equity_usd');
  const chart = useMemo(
    () => equityChart(snapshot.equity_history, metric),
    [snapshot.equity_history, metric]
  );
  const id = useId();
  const label = metric === 'equity_usd' ? 'Account equity' : 'Total profit / loss';
  return (
    <section className={styles.chartCard} aria-label="Recorded account history">
      <div className={styles.sectionHead}>
        <div>
          <span className={styles.eyebrow}>PERFORMANCE</span>
          <h3>{label}</h3>
        </div>
        <div className={styles.switcher} aria-label="Chart metric">
          <button
            type="button"
            aria-pressed={metric === 'equity_usd'}
            onClick={() => setMetric('equity_usd')}
          >
            Equity
          </button>
          <button
            type="button"
            aria-pressed={metric === 'total_pnl_usd'}
            onClick={() => setMetric('total_pnl_usd')}
          >
            P&amp;L
          </button>
        </div>
      </div>
      <div className={styles.chart}>
        {chart.count > 0 ? (
          <>
            <div className={styles.chartScale}>
              <span>{money(chart.high.toFixed(2))}</span>
              <span>{money(chart.low.toFixed(2))}</span>
            </div>
            <svg viewBox="0 0 720 180" role="img" aria-labelledby={id} preserveAspectRatio="none">
              <title
                id={id}
              >{`${label}: ${chart.count} recorded valuations. Missing valuations break the line.`}</title>
              {[18, 66, 114, 162].map((y) => (
                <path key={y} d={`M0 ${y}H720`} className={styles.gridLine} />
              ))}
              {chart.segments.map((segment, index) => (
                <g key={index}>
                  {segment.length > 1 && (
                    <polyline
                      className={styles.chartLine}
                      points={segment.map((p) => `${p.x},${p.y}`).join(' ')}
                    />
                  )}
                  {segment.map((p, i) => (
                    <circle
                      key={`${p.at}-${i}`}
                      cx={p.x}
                      cy={p.y}
                      r={chart.count === 1 ? 1.5 : 0.5}
                      className={styles.chartPoint}
                    >
                      <title>{`${dateLabel(p.at, true)} · ${money(String(p.value), metric === 'total_pnl_usd')}`}</title>
                    </circle>
                  ))}
                </g>
              ))}
            </svg>
          </>
        ) : (
          <div className={styles.chartEmpty}>
            <span className={styles.emptyMark} aria-hidden="true">
              · · ·
            </span>
            <strong>No complete valuations recorded yet</strong>
            <span>History appears as the experiment records account values.</span>
          </div>
        )}
      </div>
      <div className={styles.chartDates}>
        <span>{chart.first ? dateLabel(chart.first) : 'Awaiting first valuation'}</span>
        <span>
          {chart.count === 1
            ? '1 recorded valuation'
            : chart.last
              ? dateLabel(chart.last)
              : 'No history yet'}
        </span>
      </div>
      <p className={styles.chartNote}>
        {chart.count === 1
          ? 'First recorded valuation. A trend needs more observations.'
          : 'Recorded valuations only. Gaps indicate unavailable marks.'}
      </p>
    </section>
  );
}

function ComparisonPanel({
  comparison,
  now,
}: {
  comparison: NonNullable<PaperMarketSnapshot['comparison']>;
  now: number;
}) {
  const series = comparison.portfolios.map((p) => ({
    name: p.name,
    history: p.snapshot.equity_history,
  }));
  const benchmark = comparison.benchmark as {
    equity_history?: { at: string; equity_usd: string | null }[];
    symbol?: string;
  } | null;
  const stamps = [
    ...series.flatMap((s) => s.history.map((h) => Date.parse(h.at)).filter(Number.isFinite)),
    ...(benchmark?.equity_history ?? []).map((p) => Date.parse(p.at)).filter(Number.isFinite),
  ];
  const start = stamps.length ? Math.min(...stamps) : 0;
  const end = stamps.length ? Math.max(...stamps) : 0;
  const makePoints = (
    history: { at: string; equity_usd: string | null; valuation_complete?: boolean }[]
  ) =>
    history.map((p) => ({
      at: p.at,
      t: Date.parse(p.at),
      value: p.valuation_complete === false ? null : decimalNumber(p.equity_usd),
    }));
  const lineData = series.map((s) => ({ name: s.name, points: makePoints(s.history) }));
  if (benchmark?.equity_history?.length) {
    lineData.push({
      name: benchmark.symbol || 'Benchmark',
      points: makePoints(benchmark.equity_history),
    });
  }
  const allValues = lineData.flatMap((line) =>
    line.points.flatMap((point) => (point.value === null ? [] : [point.value / 10]))
  );
  const minValue = Math.min(0, ...allValues);
  const maxValue = Math.max(0, ...allValues);
  const range = Math.max(maxValue - minValue, 1);
  const y = (value: number) => 18 + ((maxValue - value) / range) * 144;
  const tones = [styles.seriesOne, styles.seriesTwo, styles.seriesThree, styles.seriesBenchmark];
  const news =
    comparison.news && typeof comparison.news === 'object'
      ? (comparison.news as Record<string, unknown>)
      : {};
  const newsStatus = String(news.news_connection ?? news.status ?? 'status unavailable').replace(
    /_/g,
    ' '
  );
  const latestReport =
    typeof news.news_latest_report_at === 'string'
      ? news.news_latest_report_at
      : typeof news.latest_report_at === 'string'
        ? news.latest_report_at
        : null;
  const market = comparison.portfolios[0]?.snapshot.market;
  const quoteRows = comparison.portfolios.flatMap((p) => p.snapshot.quotes);
  const freshQuotes = quoteRows.filter((q) => q.status === 'fresh').length;
  const readiness =
    comparison.readiness && typeof comparison.readiness === 'object'
      ? (comparison.readiness as Record<string, unknown>)
      : {};
  const newsState = news;
  const settings = comparison.settings as Record<string, unknown>;
  const models = comparison.portfolios.map(
    (p) => `${p.snapshot.experiment.model} ${p.snapshot.experiment.effort}`
  );
  const newestQuote =
    quoteRows
      .map((q) => q.quoted_at)
      .filter((v): v is string => !!v && Number.isFinite(Date.parse(v)))
      .sort()
      .at(-1) ?? null;
  const totalCost = comparison.portfolios
    .map((p) => decimalNumber(usageCost(comparison, p)))
    .filter((v): v is number => v !== null);
  return (
    <>
      <div className={styles.statusStrip} role="status" aria-label="Experiment status">
        <span className={market?.is_open ? styles.marketOpen : styles.marketClosed}>
          <i />[
          {market ? human(market.status).toUpperCase() : human(comparison.status).toUpperCase()}]
        </span>
        <span>
          STATE <b>{human(comparison.status).toUpperCase()}</b>
        </span>
        <span>
          T-MINUS{' '}
          <b>
            {comparison.expires_at ? remaining(comparison.expires_at, now) : 'Start time pending'}
          </b>
        </span>
        <span>
          STARTED{' '}
          <b>{comparison.started_at ? dateLabel(comparison.started_at, true) : 'Not started'}</b>
        </span>
        <span>
          ENDS{' '}
          <b>{comparison.expires_at ? dateLabel(comparison.expires_at, true) : 'Not scheduled'}</b>
        </span>
        <span>
          IRONSIGHT <b>{newsStatus.toUpperCase()}</b>
          {latestReport ? ` · ${ageLabel(latestReport, now)}` : ''}
        </span>
        <span>
          QUOTES{' '}
          <b>
            {asciiBar(quoteRows.length ? freshQuotes / quoteRows.length : 0, 8)} {freshQuotes}/
            {quoteRows.length}
          </b>{' '}
          fresh
          {newestQuote ? ` · ${ageLabel(newestQuote, now)}` : ''}
        </span>
        <span>
          TRADE <b>{String(settings.trading_model ?? models[0] ?? 'unknown')}</b>{' '}
          {String(settings.trading_effort ?? '')}
        </span>
        <span>
          SCREEN <b>{String(settings.screening_model ?? 'unknown')}</b>{' '}
          {String(settings.screening_effort ?? '')}
        </span>
        <span>
          EXP <b>{comparison.id}</b>
        </span>
      </div>
      <section className={styles.chartCard} aria-label="Normalized portfolio comparison">
        <div className={styles.sectionHead}>
          <div>
            <span className={styles.eyebrow}>SHARED PERFORMANCE</span>
            <h3>Equity from $1,000</h3>
          </div>
        </div>
        {lineData.some((l) => l.points.some((p) => p.value !== null && Number.isFinite(p.t))) ? (
          <svg
            viewBox="0 0 720 180"
            role="img"
            aria-label="Portfolio equity and benchmark comparison"
            preserveAspectRatio="none"
          >
            {[18, 66, 114, 162].map((y) => (
              <path key={y} d={`M0 ${y}H720`} className={styles.gridLine} />
            ))}
            {[0, 1, 2, 3, 4].map((step) => (
              <text key={step} x="2" y={18 + step * 36 - 3} className={styles.chartAxisLabel}>
                {money(String((maxValue - (range * step) / 4) * 10))}
              </text>
            ))}
            {lineData.map((line, i) => {
              const segments: string[][] = [[]];
              for (const point of [...line.points].sort((a, b) => a.t - b.t)) {
                if (!Number.isFinite(point.t) || point.value === null) {
                  if (segments.at(-1)?.length) segments.push([]);
                  continue;
                }
                segments
                  .at(-1)!
                  .push(
                    `${12 + ((point.t - start) / Math.max(end - start, 1)) * 696},${y(point.value / 10)}`
                  );
              }
              return segments
                .filter((segment) => segment.length)
                .map((segment, index) => (
                  <polyline
                    key={`${line.name}-${index}`}
                    points={segment.join(' ')}
                    className={tones[i % tones.length]}
                  />
                ));
            })}
          </svg>
        ) : (
          <div className={styles.chartEmpty}>
            <strong>No valuations recorded yet</strong>
            <span>Performance lines appear after the first complete marks.</span>
          </div>
        )}
        <div className={styles.chartDates}>
          {lineData.map((line, i) => (
            <span key={line.name}>
              <i className={tones[i % tones.length]} /> {line.name}
            </span>
          ))}
        </div>
      </section>
      <div className={styles.activityGrid}>
        {comparison.portfolios.map((p) => {
          const s = p.snapshot,
            complete = s.account.valuation_complete;
          const recent = [...s.decisions].sort((a, b) => Date.parse(b.at) - Date.parse(a.at))[0];
          const errors = s.decisions.filter((d) => d.error).length;
          const evidence =
            recent?.evidence && typeof recent.evidence === 'object'
              ? (recent.evidence as Record<string, unknown>)
              : {};
          const reports = Array.isArray(evidence.news) ? evidence.news : [];
          const blockers = portfolioBlockers(comparison, p);
          const blocked = blockers.length > 0 && s.fills.length === 0;
          const drawdown = decimalNumber(s.account.maximum_drawdown_pct ?? null);
          return (
            <details className={styles.card} key={p.id} open data-portfolio={p.id}>
              <summary>
                <strong>
                  {trendGlyph(complete ? s.account.total_pnl_usd : null)} {p.name}
                </strong>
                <span className={styles.subtle}>
                  {p.strategy} · {blocked ? 'BLOCKED' : human(s.experiment.status)}
                </span>
              </summary>
              {blocked && (
                <div className={styles.errorBanner} role="status">
                  <strong>[BLOCKED] needs credentials</strong>
                  <span>{blockers.join(' · ')} No fills are simulated while blocked.</span>
                </div>
              )}
              <div className={styles.metrics}>
                <div className={styles.equityMetric}>
                  <span className={styles.eyebrow}>EQUITY</span>
                  <strong>{money(complete ? s.account.equity_usd : null)}</strong>
                  <small>Started at {money(s.experiment.initial_cash_usd)}</small>
                </div>
                <div>
                  <span className={styles.eyebrow}>RETURN</span>
                  <strong className={tone(s.account.total_pnl_usd)}>
                    {money(complete ? s.account.total_pnl_usd : null, true)}
                  </strong>
                  <small>{percentage(complete ? s.account.total_return_pct : null)}</small>
                </div>
                <div>
                  <span className={styles.eyebrow}>CASH / INVESTED</span>
                  <strong>
                    {money(s.account.cash_usd)} / {money(s.account.invested_value_usd ?? null)}
                  </strong>
                  <small>
                    {s.holdings.length} holdings · {s.account.trade_count ?? s.fills.length} trades
                  </small>
                </div>
              </div>
              <dl className={styles.replayMetrics}>
                <div>
                  <dt>Realized P&amp;L</dt>
                  <dd>{money(s.account.realized_pnl_usd, true)}</dd>
                </div>
                <div>
                  <dt>Unrealized P&amp;L</dt>
                  <dd>{money(complete ? s.account.unrealized_pnl_usd : null, true)}</dd>
                </div>
                <div>
                  <dt>Max drawdown</dt>
                  <dd>
                    {drawdown === null
                      ? 'Unknown'
                      : `${asciiBar(Math.min(drawdown, 20) / 20, 8)} -${Math.abs(drawdown).toFixed(2)}%`}
                  </dd>
                </div>
                <div>
                  <dt>Usage cost (API, not in returns)</dt>
                  <dd>
                    {usageCost(comparison, p) === null
                      ? 'Unavailable'
                      : money(usageCost(comparison, p))}
                  </dd>
                </div>
                <div>
                  <dt>Latest decision</dt>
                  <dd>
                    {recent
                      ? `${human(recent.status)} · ${dateLabel(recent.at)}`
                      : 'Pending first review'}
                  </dd>
                </div>
                <div>
                  <dt>Errors / pending</dt>
                  <dd>
                    {errors > 0 ? '[!] ' : ''}
                    {errors} errors · {human(s.runtime.last_cycle_status)}
                  </dd>
                </div>
                <div>
                  <dt>Runtime model</dt>
                  <dd>
                    {s.experiment.model} · {s.experiment.effort}
                  </dd>
                </div>
                <div>
                  <dt>Marks as of</dt>
                  <dd>{dateLabel(s.account.marks_as_of ?? null, true)}</dd>
                </div>
              </dl>
              <details>
                <summary>Inspect decisions, fills, holdings and market metadata</summary>
                <p>{recent?.rationale || 'No rationale recorded.'}</p>
                {recent?.error && <p className={styles.warning}>{recent.error}</p>}
                <p>Pending/error status: {s.runtime.last_error || 'No runtime error recorded'}</p>
                {reports.length > 0 && (
                  <section aria-label="Supporting news reports">
                    <h4>Supporting news</h4>
                    {reports.map((report, index) => {
                      const item =
                        report && typeof report === 'object'
                          ? (report as Record<string, unknown>)
                          : {};
                      return (
                        <p key={String(item.id ?? index)}>
                          <strong>{String(item.title ?? item.summary ?? 'News report')}</strong> ·{' '}
                          {String(item.source ?? 'Unknown source')} · ID{' '}
                          {String(item.id ?? 'not recorded')}
                          {item.received_at
                            ? ` · received ${dateLabel(String(item.received_at), true)}`
                            : ''}
                        </p>
                      );
                    })}
                  </section>
                )}
                {typeof evidence.source_trader === 'string' && (
                  <p>
                    Copy source: {String(evidence.source_trader)}
                    {evidence.source_trade_id ? ` · trade ${String(evidence.source_trade_id)}` : ''}
                  </p>
                )}
                {typeof evidence.scaling_calculation === 'string' && (
                  <p>Copy scaling: {String(evidence.scaling_calculation)}</p>
                )}
                <ol className={styles.fills}>
                  {s.fills.map((f) => (
                    <li key={f.id}>
                      <strong>
                        {f.side} {f.symbol}
                      </strong>
                      <p>
                        {quantity(f.quantity)} @ {money(f.price_usd)} · {dateLabel(f.at, true)}
                      </p>
                    </li>
                  ))}
                </ol>
                <ol className={styles.decisions}>
                  {s.decisions.slice(0, 5).map((d) => (
                    <li key={d.id}>
                      <strong>
                        {human(d.status)} · {dateLabel(d.at, true)}
                      </strong>
                      <p>{d.rationale || d.error || 'No details'}</p>
                      <small>
                        {d.model} · {d.effort}
                      </small>
                      {Array.isArray((d.evidence as Record<string, unknown> | undefined)?.news) && (
                        <small>
                          Evidence reports:{' '}
                          {((d.evidence as Record<string, unknown>).news as unknown[]).length}
                        </small>
                      )}
                    </li>
                  ))}
                </ol>
                <p>
                  {s.holdings
                    .map((h) => `${h.symbol}: ${quantity(h.quantity)} shares`)
                    .join(' · ') || 'No holdings'}
                </p>
              </details>
            </details>
          );
        })}
      </div>
      <details className={styles.card}>
        <summary>Experiment readiness, news feed and observation gaps</summary>
        <p>
          Readiness:{' '}
          {readiness.ready === true
            ? 'Ready'
            : readiness.status
              ? human(String(readiness.status))
              : 'Checks pending'}
          {Array.isArray(readiness.blockers) && readiness.blockers.length
            ? ` · ${readiness.blockers.map(String).join('; ')}`
            : ''}
        </p>
        <p>
          News feed: {newsStatus}
          {latestReport ? ` · latest report ${dateLabel(latestReport, true)}` : ''}
          {typeof newsState.news_last_error === 'string' ? ` · ${newsState.news_last_error}` : ''}
        </p>
        <p>
          API cost (excluded from returns):{' '}
          {totalCost.length
            ? money(String(totalCost.reduce((a, b) => a + b, 0).toFixed(2)))
            : (comparison.usage_costs?.status ?? 'unavailable')}
        </p>
        <p>
          Benchmark: {benchmark?.symbol || 'Not available'} ·{' '}
          {benchmark?.equity_history?.length || 0} observations
        </p>
        <p>
          {comparison.gaps.length
            ? `${comparison.gaps.length} recorded service or market gaps affect observation continuity.`
            : 'No gaps recorded.'}
        </p>
      </details>
    </>
  );
}

export function PaperTradingPanel({
  embedded = false,
  onClose,
  snapshot: suppliedSnapshot,
}: {
  embedded?: boolean;
  onClose?: () => void;
  snapshot?: PaperMarketSnapshot;
}) {
  const [refresh, setRefresh] = useState(0);
  const { snapshot, error, loading, now } = usePaperMarket(suppliedSnapshot, refresh);
  const titleId = useId();
  const decisions = snapshot
    ? [...snapshot.decisions].sort((a, b) => Date.parse(b.at) - Date.parse(a.at)).slice(0, 6)
    : [];
  const fills = snapshot
    ? [...snapshot.fills].sort((a, b) => Date.parse(b.at) - Date.parse(a.at)).slice(0, 8)
    : [];
  const expired = snapshot
    ? snapshot.experiment.status === 'expired' ||
      (now > 0 &&
        snapshot.experiment.expires_at !== null &&
        Date.parse(snapshot.experiment.expires_at) <= now)
    : false;
  const replay = snapshot?.runtime.historical_replay;
  const complete = snapshot?.account.valuation_complete === true;
  const staleMarks = snapshot?.account.valuation_fresh === false;

  return (
    <section
      className={`${styles.panel} ${embedded ? styles.embedded : styles.standalone}`}
      aria-labelledby={titleId}
    >
      <header className={styles.header}>
        <div className={styles.identity}>
          <span className={styles.eyebrow}>J.A.R.V.I.S. / MARKET LAB</span>
          <h2 id={titleId}>
            Paper market <span>USD</span>
          </h2>
          <p>Autonomous decisions. Simulated capital.</p>
        </div>
        <div className={styles.headerActions}>
          <span className={styles.paperBadge}>PAPER ONLY</span>
          {suppliedSnapshot === undefined && (
            <button
              type="button"
              className={styles.iconButton}
              onClick={() => setRefresh((v) => v + 1)}
              disabled={loading}
              aria-label="Refresh paper market"
              title="Refresh paper market"
            >
              ↻
            </button>
          )}
          {onClose && (
            <button
              type="button"
              className={styles.iconButton}
              onClick={onClose}
              aria-label="Close paper market"
            >
              ×
            </button>
          )}
        </div>
      </header>

      {error && (
        <div className={styles.errorBanner} role="alert">
          <strong>DATA UNAVAILABLE</strong>
          <span>
            {error}
            {snapshot ? ' Showing the last received snapshot; values may be out of date.' : ''}
          </span>
        </div>
      )}
      {!snapshot ? (
        <div className={styles.unavailable} role="status">
          <span className={styles.emptyMark} aria-hidden="true">
            ◇
          </span>
          <h3>{error ? 'Unable to load the experiment' : 'Connecting to paper market'}</h3>
          <p>
            {error
              ? 'Balances, holdings and performance are unknown until the service responds.'
              : 'Waiting for a verified account snapshot.'}
          </p>
        </div>
      ) : snapshot.comparison ? (
        <ComparisonPanel comparison={snapshot.comparison} now={now} />
      ) : (
        <>
          {replay && (
            <section className={styles.replayCard} aria-label="30-day accelerated paper simulation">
              <div className={styles.replayHeading}>
                <div>
                  <span className={styles.eyebrow}>
                    30 DAYS IN {replay.duration_hours} HOURS · HISTORICAL REPLAY
                  </span>
                  <h3>
                    {human(replay.status)} · {replay.sessions_completed}/{replay.sessions_total}{' '}
                    sessions
                  </h3>
                </div>
                <strong>
                  {Number(replay.pnl_usd) >= 0 ? '+' : ''}${replay.pnl_usd}
                </strong>
              </div>
              <progress
                max={replay.sessions_total}
                value={replay.sessions_completed}
                aria-label="Replay progress"
              />
              <p>
                AAPL · {replay.start_date} to {replay.end_date} ·{' '}
                {replay.status === 'running'
                  ? `Expected finish ${dateLabel(replay.expected_finish_at, true)}`
                  : 'Run finished'}
              </p>
              <dl className={styles.replayMetrics}>
                <div>
                  <dt>Paper equity</dt>
                  <dd>${replay.final_equity_usd}</dd>
                </div>
                <div>
                  <dt>Return</dt>
                  <dd>{replay.return_pct}%</dd>
                </div>
                <div>
                  <dt>Decision score</dt>
                  <dd>
                    {replay.decision_score_pct === null
                      ? 'Pending'
                      : `${replay.decision_score_pct}%`}{' '}
                    ({replay.correct_decisions}/{replay.scored_decisions})
                  </dd>
                </div>
                <div>
                  <dt>Buy &amp; hold</dt>
                  <dd>{replay.buy_hold_return_pct}%</dd>
                </div>
                <div>
                  <dt>Max drawdown</dt>
                  <dd>{replay.max_drawdown_pct}%</dd>
                </div>
                <div>
                  <dt>Unscored errors</dt>
                  <dd>{replay.model_errors}</dd>
                </div>
              </dl>
              <p>
                Outcome score compares decisions with the next session’s result after trading costs.
                Errors receive no score. Open positions are valued at the latest replay close.
              </p>
              <details>
                <summary>Latest decisions and scoring</summary>
                <p>{replay.scoring}</p>
                {replay.decisions
                  .slice(-5)
                  .reverse()
                  .map((d) => (
                    <p key={d.date}>
                      <b>
                        {d.date} ·{' '}
                        {d.correct === null ? 'Unscored' : d.correct ? 'Correct' : 'Incorrect'}
                      </b>{' '}
                      · {d.error || d.rationale} · ${d.equity_usd}
                    </p>
                  ))}
              </details>
            </section>
          )}
          <div className={styles.statusStrip}>
            <span className={snapshot.market.is_open ? styles.marketOpen : styles.marketClosed}>
              <i />
              {human(snapshot.market.status)}
            </span>
            <span>
              {snapshot.market.is_open ? 'Session closes' : 'Next open'}{' '}
              <b>
                {dateLabel(
                  snapshot.market.is_open
                    ? snapshot.market.session_close_at
                    : snapshot.market.next_open_at,
                  true
                )}
              </b>
            </span>
            <span className={styles.refreshAge}>
              Refreshed {ageLabel(snapshot.runtime.last_refresh_at, now)}
            </span>
          </div>

          <div className={styles.metrics}>
            <div className={styles.equityMetric}>
              <span className={styles.eyebrow}>
                {staleMarks ? 'LAST OBSERVED EQUITY' : 'ACCOUNT EQUITY'}
              </span>
              <strong>{money(complete ? snapshot.account.equity_usd : null)}</strong>
              <small>Initial capital {money(snapshot.experiment.initial_cash_usd)}</small>
            </div>
            <div>
              <span className={styles.eyebrow}>TOTAL P&amp;L</span>
              <strong className={tone(complete ? snapshot.account.total_pnl_usd : null)}>
                {money(complete ? snapshot.account.total_pnl_usd : null, true)}
              </strong>
              <small className={tone(complete ? snapshot.account.total_return_pct : null)}>
                {percentage(complete ? snapshot.account.total_return_pct : null)}
              </small>
            </div>
            <div>
              <span className={styles.eyebrow}>AVAILABLE CASH</span>
              <strong>{money(snapshot.account.cash_usd)}</strong>
              <small>
                {snapshot.holdings.length} open{' '}
                {snapshot.holdings.length === 1 ? 'position' : 'positions'}
              </small>
            </div>
          </div>
          {!complete && (
            <p className={styles.valuationWarning}>
              Valuation incomplete · One or more holding marks are unavailable. Equity and total
              P&amp;L are unknown.
            </p>
          )}
          {complete && staleMarks && (
            <p className={styles.valuationWarning}>
              Last observed valuation · Holding marks are stale. Equity and P&amp;L use those
              recorded prices
              {snapshot.account.marks_as_of
                ? ` as of ${dateLabel(snapshot.account.marks_as_of, true)}`
                : '; mark timestamp unknown'}
              .
            </p>
          )}

          <div className={styles.performanceGrid}>
            <EquityChart snapshot={snapshot} />
            <aside className={styles.experiment} aria-label="Experiment settings">
              <span className={styles.eyebrow}>SEVEN-DAY EXPERIMENT</span>
              <div className={styles.experimentStatus}>
                <span className={expired ? styles.finishedDot : styles.activeDot} />
                <strong>{expired ? 'Expired' : human(snapshot.experiment.status)}</strong>
              </div>
              <p className={styles.countdown}>{remaining(snapshot.experiment.expires_at, now)}</p>
              <dl>
                <div>
                  <dt>Decision model</dt>
                  <dd>
                    {snapshot.experiment.model === 'dots-studio/dots-3-note-preview:free'
                      ? 'Dots 3 Note Preview'
                      : snapshot.experiment.model === 'gpt-6-astra'
                        ? 'Astra'
                        : snapshot.experiment.model}{' '}
                    <span className={styles.effort}>{snapshot.experiment.effort}</span>
                  </dd>
                </div>
                <div>
                  <dt>Automatic trader</dt>
                  <dd>
                    {expired
                      ? 'Finished'
                      : snapshot.runtime.autotrading_enabled
                        ? 'Enabled'
                        : 'Paused'}
                  </dd>
                </div>
                <div>
                  <dt>Risk checks</dt>
                  <dd>Every minute</dd>
                </div>
                <div>
                  <dt>Cadence</dt>
                  <dd>Every {Math.round(snapshot.runtime.decision_interval_seconds / 60)} min</dd>
                </div>
                <div>
                  <dt>Started</dt>
                  <dd>{dateLabel(snapshot.experiment.started_at, true)}</dd>
                </div>
                <div>
                  <dt>Expires</dt>
                  <dd>{dateLabel(snapshot.experiment.expires_at, true)}</dd>
                </div>
                <div>
                  <dt>Realized P&amp;L</dt>
                  <dd className={tone(snapshot.account.realized_pnl_usd)}>
                    {money(snapshot.account.realized_pnl_usd, true)}
                  </dd>
                </div>
                <div>
                  <dt>Unrealized P&amp;L</dt>
                  <dd className={tone(complete ? snapshot.account.unrealized_pnl_usd : null)}>
                    {money(complete ? snapshot.account.unrealized_pnl_usd : null, true)}
                  </dd>
                </div>
              </dl>
            </aside>
          </div>

          <section className={styles.card} aria-labelledby={`${titleId}-holdings`}>
            <div className={styles.sectionHead}>
              <h3 id={`${titleId}-holdings`}>
                Holdings <span className={styles.count}>{snapshot.holdings.length}</span>
              </h3>
              <span className={styles.subtle}>Marks from latest available quotes</span>
            </div>
            {snapshot.holdings.length === 0 ? (
              <div className={styles.emptyRow}>
                <span>No open positions</span>
                <span>Capital remains in cash until a simulated order fills.</span>
              </div>
            ) : (
              <div className={styles.tableScroll}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>Stock</th>
                      <th>Shares</th>
                      <th>Avg. cost</th>
                      <th>Mark</th>
                      <th>Value</th>
                      <th>Unrealized P&amp;L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {snapshot.holdings.map((holding) => (
                      <tr key={holding.symbol}>
                        <td>
                          <strong>{holding.symbol}</strong>
                          <small>{ageLabel(holding.quote_at, now)}</small>
                        </td>
                        <td>{quantity(holding.quantity)}</td>
                        <td>{money(holding.average_cost_usd)}</td>
                        <td>{money(holding.mark_price_usd)}</td>
                        <td>{money(holding.market_value_usd)}</td>
                        <td className={tone(holding.unrealized_pnl_usd)}>
                          {money(holding.unrealized_pnl_usd, true)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className={styles.card} aria-labelledby={`${titleId}-quotes`}>
            <div className={styles.sectionHead}>
              <h3 id={`${titleId}-quotes`}>Market watch</h3>
              <span className={styles.subtle}>{snapshot.runtime.feed}</span>
            </div>
            {snapshot.quotes.length === 0 ? (
              <div className={styles.emptyRow}>No quotes received.</div>
            ) : (
              <div className={styles.quotes}>
                {snapshot.quotes.map((quote) => (
                  <div className={styles.quote} key={quote.symbol}>
                    <div>
                      <strong>{quote.symbol}</strong>
                      <span
                        className={quote.price_usd === null ? styles.warning : styles.quoteStatus}
                      >
                        {human(quote.status)}
                      </span>
                    </div>
                    <b>{money(quote.price_usd)}</b>
                    <small title={quote.quoted_at ?? 'No quote timestamp'}>
                      {ageLabel(quote.quoted_at, now, quote.age_seconds)} · {quote.source}
                    </small>
                    {quote.error && <p className={styles.quoteError}>{quote.error}</p>}
                  </div>
                ))}
              </div>
            )}
          </section>

          <div className={styles.activityGrid}>
            <section className={styles.card} aria-labelledby={`${titleId}-decisions`}>
              <div className={styles.sectionHead}>
                <h3 id={`${titleId}-decisions`}>Decision journal</h3>
                <span className={styles.subtle}>Recent {decisions.length}</span>
              </div>
              {decisions.length === 0 ? (
                <div className={styles.emptyRow}>
                  <span>No decisions recorded</span>
                  <span>The first eligible cycle will appear here with its rationale.</span>
                </div>
              ) : (
                <ol className={styles.decisions}>
                  {decisions.map((decision) => (
                    <li key={decision.id}>
                      <div className={styles.decisionMeta}>
                        <strong className={decision.error ? styles.warning : ''}>
                          {human(decision.status)}
                        </strong>
                        <time dateTime={decision.at}>{dateLabel(decision.at)}</time>
                      </div>
                      <p>{decision.rationale || 'No rationale recorded.'}</p>
                      {decision.error && <p className={styles.warning}>{decision.error}</p>}
                      {decision.actions.length > 0 && (
                        <div className={styles.actionTags}>
                          {decision.actions.map((action, index) => (
                            <span key={index}>
                              {action.side} {action.symbol} ·{' '}
                              {action.quantity !== null
                                ? `${quantity(action.quantity)} shares`
                                : money(action.notional_usd)}
                            </span>
                          ))}
                        </div>
                      )}
                      <small className={styles.decisionModel}>
                        {decision.model} · {decision.effort}
                      </small>
                    </li>
                  ))}
                </ol>
              )}
            </section>
            <section className={styles.card} aria-labelledby={`${titleId}-fills`}>
              <div className={styles.sectionHead}>
                <h3 id={`${titleId}-fills`}>Simulated fills</h3>
                <span className={styles.subtle}>Recent {fills.length}</span>
              </div>
              {fills.length === 0 ? (
                <div className={styles.emptyRow}>
                  <span>No fills yet</span>
                  <span>Orders appear only after a recorded simulated execution.</span>
                </div>
              ) : (
                <ol className={styles.fills}>
                  {fills.map((fill) => (
                    <li key={fill.id}>
                      <div>
                        <span className={fill.side === 'buy' ? styles.buy : styles.sell}>
                          {fill.side}
                        </span>
                        <strong>{fill.symbol}</strong>
                        <b>{money(fill.notional_usd)}</b>
                      </div>
                      <p>
                        {quantity(fill.quantity)} shares @ {money(fill.price_usd)}
                      </p>
                      <div className={styles.fillDetail}>
                        <time dateTime={fill.at}>{dateLabel(fill.at)}</time>
                        {fill.side === 'sell' && (
                          <span className={tone(fill.realized_pnl_usd)}>
                            {money(fill.realized_pnl_usd, true)} realized
                          </span>
                        )}
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          </div>
        </>
      )}
    </section>
  );
}
