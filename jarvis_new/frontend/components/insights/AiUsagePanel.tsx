'use client';

import { useEffect, useId, useMemo, useState } from 'react';
import {
  USAGE_RANGES,
  USAGE_TIMEZONE,
  type UsageBreakdown,
  type UsageDashboard,
  type UsageDay,
  type UsageMetric,
  type UsageRange,
  type UsageSnapshot,
  chartDays,
  dayLabel,
  estimatedCost,
  exactNumber,
  snapshotLabel,
  subscribeUsage,
  tokenCount,
} from '@/lib/ai-usage';
import styles from './ai-usage-panel.module.css';

function UsageChart({ days }: { days: UsageDay[] }) {
  const [metric, setMetric] = useState<UsageMetric>('total_tokens');
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const chart = useMemo(() => chartDays(days, metric), [days, metric]);
  const gradientId = useId().replace(/:/g, '');
  const active =
    chart.points.find((point) => point.day.date === selectedDate) ?? chart.points.at(-1);
  const barWidth = Math.min(86, Math.max(1, 744 / chart.spanDays - 5));
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const hasValues = chart.points.some((point) => point.ratio !== null);
  const format = (value: number | null) =>
    metric === 'total_tokens' ? tokenCount(value, true) : estimatedCost(value);

  return (
    <section className={styles.chartSection} aria-labelledby="usage-activity-title">
      <div className={styles.sectionHeading}>
        <div>
          <span className={styles.eyebrow}>ACTIVITY / DAILY REPORT</span>
          <h3 id="usage-activity-title">Consumption over time</h3>
        </div>
        <div className={styles.metricSwitch} role="group" aria-label="Chart metric">
          <button
            type="button"
            aria-pressed={metric === 'total_tokens'}
            onClick={() => setMetric('total_tokens')}
          >
            Tokens
          </button>
          <button
            type="button"
            aria-pressed={metric === 'estimated_cost_usd'}
            onClick={() => setMetric('estimated_cost_usd')}
          >
            Est. cost
          </button>
        </div>
      </div>
      {hasValues ? (
        <>
          <div className={styles.chartReadout}>
            <span>{active ? dayLabel(active.day.date) : '—'}</span>
            <strong>{active ? format(active.day[metric]) : 'Unavailable'}</strong>
            <span>
              {metric === 'total_tokens' ? 'reported tokens' : 'estimated USD'}
              {active?.day.missing_pricing && metric === 'estimated_cost_usd'
                ? ' · partial pricing'
                : ''}
            </span>
          </div>
          <div className={styles.chartScroll}>
            <svg
              className={styles.chart}
              viewBox="0 0 900 205"
              role="group"
              aria-label={`Daily ${metric === 'total_tokens' ? 'reported tokens' : 'estimated cost in USD'}; exact values in the daily data table below`}
            >
              <defs>
                <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                  <stop
                    offset="0%"
                    stopColor={metric === 'total_tokens' ? '#63e6ff' : '#ffbc65'}
                    stopOpacity="0.86"
                  />
                  <stop
                    offset="100%"
                    stopColor={metric === 'total_tokens' ? '#208ca8' : '#b77831'}
                    stopOpacity="0.15"
                  />
                </linearGradient>
              </defs>
              {ticks.map((tick) => (
                <g key={tick}>
                  <line
                    x1="75"
                    x2="880"
                    y1={166 - tick * 140}
                    y2={166 - tick * 140}
                    className={styles.gridLine}
                  />
                  <text x="62" y={170 - tick * 140} textAnchor="end" className={styles.axisText}>
                    {format(chart.max * tick)}
                  </text>
                </g>
              ))}
              {chart.points.map(({ day, x, ratio }) => {
                const cx = 127 + x * 702;
                const selected = active?.day.date === day.date;
                const value = day[metric];
                return (
                  <g key={day.date}>
                    {ratio !== null && ratio > 0 && (
                      <rect
                        x={cx - barWidth / 2}
                        y={166 - ratio * 140}
                        width={barWidth}
                        height={ratio * 140}
                        fill={`url(#${gradientId})`}
                        opacity={selected ? 1 : 0.7}
                      />
                    )}
                    {ratio === 0 && (
                      <line
                        x1={cx - barWidth / 2}
                        x2={cx + barWidth / 2}
                        y1="166"
                        y2="166"
                        className={styles.zeroBar}
                      />
                    )}
                    {ratio === null && (
                      <text x={cx} y="164" textAnchor="middle" className={styles.axisText}>
                        ?
                      </text>
                    )}
                    <rect
                      x={cx - Math.max(barWidth, 9) / 2}
                      y="20"
                      width={Math.max(barWidth, 9)}
                      height="148"
                      fill="transparent"
                      tabIndex={0}
                      role="button"
                      aria-label={`${day.date}: ${exactNumber(value)} ${metric === 'total_tokens' ? 'tokens' : 'estimated USD'}`}
                      onFocus={() => setSelectedDate(day.date)}
                      onMouseEnter={() => setSelectedDate(day.date)}
                      onClick={() => setSelectedDate(day.date)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault();
                          setSelectedDate(day.date);
                        }
                      }}
                    >
                      <title>
                        {`${day.date}: ${exactNumber(value)} ${metric === 'total_tokens' ? 'tokens' : 'estimated USD'}`}
                      </title>
                    </rect>
                  </g>
                );
              })}
              {[
                ...new Set([0, Math.floor((chart.points.length - 1) / 2), chart.points.length - 1]),
              ].map((index) => {
                const point = chart.points[index];
                return point ? (
                  <text
                    key={index}
                    x={127 + point.x * 702}
                    y="193"
                    textAnchor="middle"
                    className={styles.axisText}
                  >
                    {dayLabel(point.day.date)}
                  </text>
                ) : null;
              })}
            </svg>
          </div>
        </>
      ) : (
        <p className={styles.empty}>
          No {metric === 'total_tokens' ? 'token totals' : 'cost estimates'} reported for this
          period.
        </p>
      )}
      <div className={styles.chartFootnote}>
        <span>Daily source totals · {USAGE_TIMEZONE}</span>
        <span>
          {days.length === 1
            ? 'Hourly activity is not reported.'
            : 'Unreported days are not filled with zeroes.'}
        </span>
      </div>
      {days.length > 0 && (
        <details className={styles.dailyDetails}>
          <summary>View exact daily data</summary>
          <div className={styles.tableScroll}>
            <table className={styles.dailyTable}>
              <caption className={styles.srOnly}>Exact daily tracker totals</caption>
              <thead>
                <tr>
                  <th scope="col">Day</th>
                  <th scope="col">Reported tokens</th>
                  <th scope="col">Estimated USD</th>
                </tr>
              </thead>
              <tbody>
                {chart.points.map(({ day }) => (
                  <tr key={day.date}>
                    <th scope="row">{day.date}</th>
                    <td>{tokenCount(day.total_tokens)}</td>
                    <td>
                      {exactNumber(day.estimated_cost_usd)}
                      {day.missing_pricing ? ' · partial' : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  );
}

function AppBreakdown({ apps, total }: { apps: UsageBreakdown[]; total: number | null }) {
  return (
    <section className={styles.appsSection} aria-labelledby="usage-apps-title">
      <div className={styles.sectionHeading}>
        <div>
          <span className={styles.eyebrow}>APPLICATIONS</span>
          <h3 id="usage-apps-title">Where usage happens</h3>
        </div>
        <span className={styles.count}>{apps.length}</span>
      </div>
      {apps.length === 0 ? (
        <p className={styles.empty}>No application usage reported for this period.</p>
      ) : (
        <ul className={styles.appList}>
          {apps.map((app, index) => (
            <li key={app.name}>
              <div className={styles.appHeading}>
                <span className={styles.appIndex}>{String(index + 1).padStart(2, '0')}</span>
                <strong>{app.name}</strong>
                <span title={`Exact reported estimate: ${exactNumber(app.estimated_cost_usd)} USD`}>
                  {estimatedCost(app.estimated_cost_usd)}
                </span>
              </div>
              <div className={styles.appBar} aria-hidden="true">
                <span
                  style={{
                    width: `${total && app.total_tokens !== null ? Math.min(100, Math.max(0, (app.total_tokens / total) * 100)) : 0}%`,
                  }}
                />
              </div>
              <div className={styles.appDetail}>
                <span>{tokenCount(app.total_tokens)} tokens</span>
                <span>{app.missing_pricing ? 'Partial estimate' : 'Estimated USD'}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Models({ models }: { models: UsageBreakdown[] }) {
  return (
    <section className={styles.modelsSection} aria-labelledby="usage-models-title">
      <div className={styles.sectionHeading}>
        <div>
          <span className={styles.eyebrow}>MODEL ATTRIBUTION</span>
          <h3 id="usage-models-title">Models in the report</h3>
        </div>
        <span className={styles.count}>{models.length}</span>
      </div>
      <p className={styles.sectionNote}>
        Model totals may be unavailable when ccusage reports only category subtotals. Names are
        shown exactly as reported.
      </p>
      {models.length === 0 ? (
        <p className={styles.empty}>No model breakdown reported for this period.</p>
      ) : (
        <div className={styles.tableScroll}>
          <table className={styles.modelTable}>
            <caption className={styles.srOnly}>Reported model and application attribution</caption>
            <thead>
              <tr>
                <th scope="col">Model / application</th>
                <th scope="col">Reported tokens</th>
                <th scope="col">Estimated cost</th>
              </tr>
            </thead>
            <tbody>
              {models.map((model, index) => (
                <tr key={`${model.app}:${model.name}:${index}`}>
                  <th scope="row">
                    <strong>{model.name}</strong>
                    <span>{model.app ?? 'Application unavailable'}</span>
                  </th>
                  <td>
                    <strong>{tokenCount(model.total_tokens)}</strong>
                    {model.total_tokens === null && (
                      <span>Known subtotal: {tokenCount(model.reported_token_subtotal)}</span>
                    )}
                  </td>
                  <td
                    title={`Exact reported estimate: ${exactNumber(model.estimated_cost_usd)} USD`}
                  >
                    <strong>{estimatedCost(model.estimated_cost_usd)}</strong>
                    {model.missing_pricing && (
                      <span className={styles.amber}>Pricing incomplete</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function UsageContent({ data }: { data: UsageDashboard }) {
  const { summary, source_info: source } = data;
  const categories = [
    ['Input', summary.input_tokens],
    ['Output', summary.output_tokens],
    ['Cache read', summary.cache_read_tokens],
    ['Cache write', summary.cache_write_tokens],
    ['Unattributed', summary.unattributed_tokens],
  ] as const;
  return (
    <>
      <div className={styles.totals}>
        <section className={styles.tokenCard} aria-label="Authoritative token total">
          <div className={styles.cardLabel}>
            <span>REPORTED TOKEN TOTAL</span>
            <span className={styles.cardTag}>AUTHORITATIVE</span>
          </div>
          <strong className={styles.heroNumber}>{tokenCount(summary.total_tokens, true)}</strong>
          <span className={styles.exactValue}>{tokenCount(summary.total_tokens)} tokens</span>
          <span className={styles.cardNote}>Includes all categories in the ccusage total.</span>
        </section>
        <section className={styles.costCard} aria-label="Estimated usage cost in US dollars">
          <div className={styles.cardLabel}>
            <span>ESTIMATED USAGE COST</span>
            <span className={styles.cardTag}>
              {summary.missing_pricing ? 'PARTIAL PRICING' : 'USD'}
            </span>
          </div>
          <strong className={styles.heroNumber}>{estimatedCost(summary.estimated_cost_usd)}</strong>
          <span className={styles.exactValue}>
            Reported: {exactNumber(summary.estimated_cost_usd)} USD
          </span>
          <span className={styles.cardNote}>
            {summary.missing_pricing
              ? 'Missing prices are excluded from this estimate.'
              : 'ccusage estimate · billed cash is not reported.'}
          </span>
        </section>
      </div>
      {summary.missing_pricing && (
        <p className={styles.warning}>
          Pricing is incomplete
          {summary.unpriced_models?.length ? ` for ${summary.unpriced_models.join(', ')}` : ''}. The
          reported estimate is a subtotal; missing costs remain unknown.
        </p>
      )}
      <div className={styles.mainGrid}>
        <UsageChart days={data.timeline} />
        <AppBreakdown apps={data.apps} total={summary.total_tokens} />
      </div>
      <section className={styles.attribution} aria-label="Reported token categories">
        <div>
          <span className={styles.eyebrow}>TOKEN ATTRIBUTION</span>
          <p>Known categories, separate from the authoritative total.</p>
        </div>
        <dl>
          {categories.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd title={exactNumber(value)}>{tokenCount(value)}</dd>
            </div>
          ))}
        </dl>
        <p className={styles.sectionNote}>
          Input excludes cache. Unattributed tokens remain in the total; reasoning and request
          counts are not reported.
        </p>
      </section>
      <Models models={data.models} />
      <details className={styles.provenance}>
        <summary>
          Source &amp; accounting details{' '}
          <span>
            {source.name} v{source.version} · {source.pricing_mode} pricing
          </span>
        </summary>
        <dl>
          <div>
            <dt>Report snapshot</dt>
            <dd>{source.snapshot_at ?? 'Unavailable'}</dd>
          </div>
          <div>
            <dt>Dashboard response</dt>
            <dd>{data.updated_at}</dd>
          </div>
          <div>
            <dt>Report timezone</dt>
            <dd>{data.timezone}</dd>
          </div>
          <div>
            <dt>Source status</dt>
            <dd>{source.status}</dd>
          </div>
          <div>
            <dt>Period start</dt>
            <dd>{data.range.start ?? 'All available history'}</dd>
          </div>
          <div>
            <dt>Period end (exclusive)</dt>
            <dd>{data.range.end ?? 'All available history'}</dd>
          </div>
        </dl>
        {data.accounting_details && <p>{data.accounting_details}</p>}
        {data.pricing?.note && <p>{data.pricing.note}</p>}
        <ul>
          {data.warnings.map((warning, index) => (
            <li key={index}>{warning}</li>
          ))}
        </ul>
        <p>
          Compact numbers and chart labels are rounded for display. The main total, reported cost,
          and daily data preserve the source values; breakdown cost tooltips show the exact reported
          estimates.
        </p>
      </details>
    </>
  );
}

export default function AiUsagePanel() {
  const [range, setRange] = useState<UsageRange>('today');
  const [snapshot, setSnapshot] = useState<UsageSnapshot | null>(null);
  useEffect(() => subscribeUsage(range, setSnapshot), [range]);
  // Effects run after render: never flash the preceding range's values.
  const current = snapshot?.range === range ? snapshot : null;
  const data = current?.data ?? null;
  const phase = current?.phase ?? 'loading';
  return (
    <div className={styles.panel}>
      <header className={styles.header}>
        <div>
          <span className={styles.eyebrow}>J.A.R.V.I.S. / INSIGHTS</span>
          <h2>AI usage</h2>
          <p>Local intelligence, measured.</p>
        </div>
        <div className={styles.rangeControls} role="group" aria-label="Usage period">
          {USAGE_RANGES.map((option) => (
            <button
              key={option.key}
              type="button"
              aria-pressed={range === option.key}
              onClick={() => setRange(option.key)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </header>
      <div className={styles.sourceStrip}>
        <span
          className={phase === 'stale' || phase === 'error' ? styles.statusStale : styles.status}
          role="status"
        >
          {phase === 'loading'
            ? 'AWAITING REPORT'
            : phase === 'error'
              ? 'TRACKER UNAVAILABLE'
              : phase === 'stale'
                ? 'STALE SNAPSHOT'
                : current?.refreshing
                  ? 'REFRESHING'
                  : 'REPORT READY'}
        </span>
        <span>
          {data
            ? `${data.source_info.name} v${data.source_info.version} · ${data.source_info.pricing_mode} pricing`
            : 'ccusage report'}
        </span>
        <span>
          {data?.range.label ?? USAGE_RANGES.find((option) => option.key === range)?.label} ·{' '}
          {USAGE_TIMEZONE}
        </span>
      </div>
      {phase === 'stale' && (
        <p className={styles.warning} role="status">
          Refresh unavailable. Showing the last successful report for this period, captured{' '}
          {snapshotLabel(data?.source_info.snapshot_at ?? null)} SGT. Automatic retry runs while
          this panel is visible.
        </p>
      )}
      {data ? (
        <UsageContent data={data} />
      ) : (
        <div className={styles.emptyState} role="status" aria-busy={phase === 'loading'}>
          <span className={styles.emptyReticle} aria-hidden="true">
            ◎
          </span>
          <h3>{phase === 'error' ? 'Usage report unavailable' : 'Reading your usage report'}</h3>
          <p>
            {phase === 'error'
              ? 'The tracker could not provide a valid report for this period. Values will appear when the connection recovers.'
              : 'Waiting for the local ccusage snapshot.'}
          </p>
          <span>Automatic refresh every 60 seconds while visible.</span>
        </div>
      )}
      <footer className={styles.footer}>
        <span>
          {data
            ? `Snapshot · ${snapshotLabel(data.source_info.snapshot_at)} SGT`
            : 'No usage values loaded'}
        </span>
        <span>Read-only · refreshes every 60s while visible</span>
      </footer>
    </div>
  );
}
