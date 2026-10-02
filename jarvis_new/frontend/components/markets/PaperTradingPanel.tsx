'use client';

import { useEffect, useId, useMemo, useState } from 'react';
import { bridgePaperMarket } from '@/lib/bridge';
import {
  type ChartMetric,
  type PaperMarketSnapshot,
  ageLabel,
  decimalNumber,
  equityChart,
  money,
  parsePaperMarketSnapshot,
  percentage,
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
function remaining(expires: string, now: number): string {
  const ms = Date.parse(expires) - now;
  if (!now || !Number.isFinite(ms)) return '7-day experiment';
  if (ms <= 0) return 'Experiment ended';
  const hours = Math.ceil(ms / 3_600_000);
  return hours >= 24
    ? `${Math.floor(hours / 24)}d ${hours % 24}h remaining`
    : `${hours}h remaining`;
}

/** Poll failures are visible even after a good read. Hidden panels stop polling. */
function usePaperMarket(override: PaperMarketSnapshot | undefined, refresh: number) {
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
    if (override !== undefined) return;
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
  }, [override, refresh]);
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
      (now > 0 && Date.parse(snapshot.experiment.expires_at) <= now)
    : false;
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
      ) : (
        <>
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
                    {snapshot.experiment.model === 'gpt-6-astra'
                      ? 'Astra'
                      : snapshot.experiment.model}{' '}
                    <span className={styles.effort}>{snapshot.experiment.effort}</span>
                  </dd>
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

          <footer className={styles.footer}>
            <div>
              <span className={styles.eyebrow}>LAST CYCLE</span>
              <span>
                {human(snapshot.runtime.last_cycle_status)} ·{' '}
                {dateLabel(snapshot.runtime.last_cycle_at, true)}
              </span>
            </div>
            {snapshot.runtime.last_error && (
              <p className={styles.warning} role="status">
                {snapshot.runtime.last_error}
              </p>
            )}
            {snapshot.runtime.last_refresh_error && (
              <p className={styles.warning} role="status">
                Quote refresh: {snapshot.runtime.last_refresh_error}
              </p>
            )}
            <p>{snapshot.runtime.fill_policy}</p>
          </footer>
        </>
      )}
    </section>
  );
}
