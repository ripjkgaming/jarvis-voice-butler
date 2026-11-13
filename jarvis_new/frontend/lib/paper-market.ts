/** Wire contract for the read-only, seven-day paper market experiment.
 * Money stays decimal text on the wire. Numbers here are display-only.
 */
export type PaperMarketSnapshot = {
  schema_version: 1;
  /** Optional multi-portfolio envelope added by the comparison service. */
  comparison?: PaperMarketComparison;
  generated_at?: string;
  experiment: {
    id: string;
    started_at: string | null;
    expires_at: string | null;
    initial_cash_usd: string;
    status: string;
    model: string;
    effort: string;
  };
  account: {
    cash_usd: string;
    equity_usd: string | null;
    total_pnl_usd: string | null;
    total_return_pct: string | null;
    realized_pnl_usd: string;
    unrealized_pnl_usd: string | null;
    valuation_complete: boolean;
    valuation_fresh?: boolean;
    marks_as_of?: string | null;
    invested_value_usd?: string | null;
    maximum_drawdown_pct?: string | null;
    trade_count?: number;
  };
  market: {
    is_open: boolean;
    next_open_at: string | null;
    session_close_at: string | null;
    status: string;
  };
  quotes: {
    symbol: string;
    price_usd: string | null;
    quoted_at: string | null;
    age_seconds: number | null;
    source: string;
    status: string;
    error?: string | null;
  }[];
  holdings: {
    symbol: string;
    quantity: string;
    average_cost_usd: string;
    mark_price_usd: string | null;
    market_value_usd: string | null;
    unrealized_pnl_usd: string | null;
    quote_at: string | null;
  }[];
  equity_history: {
    at: string;
    equity_usd: string | null;
    cash_usd: string;
    total_pnl_usd: string | null;
    valuation_complete: boolean;
  }[];
  decisions: {
    id: string;
    at: string;
    status: string;
    rationale: string | null;
    error: string | null;
    model: string;
    effort: string;
    actions: {
      symbol: string;
      side: 'buy' | 'sell';
      notional_usd: string | null;
      quantity: string | null;
    }[];
    evidence?: unknown;
    usage?: unknown;
    verification?: unknown;
  }[];
  fills: {
    id: string;
    at: string;
    symbol: string;
    side: string;
    quantity: string;
    price_usd: string;
    notional_usd: string;
    realized_pnl_usd: string;
  }[];
  runtime: {
    historical_replay?: {
      status: string;
      start_date: string;
      end_date: string;
      sessions_total: number;
      sessions_completed: number;
      final_equity_usd: string;
      pnl_usd: string;
      return_pct: string;
      buy_hold_return_pct: string;
      decision_score_pct: number | null;
      correct_decisions: number;
      scored_decisions: number;
      model_errors: number;
      max_drawdown_pct: string;
      expected_finish_at: string;
      duration_hours: number;
      scoring: string;
      decisions: {
        date: string;
        correct: boolean | null;
        rationale?: string;
        error?: string;
        equity_usd: string;
      }[];
    };
    autotrading_enabled?: boolean;
    risk_interval_seconds?: number;
    last_cycle_at: string | null;
    last_cycle_status: string | null;
    last_error: string | null;
    last_refresh_at: string | null;
    last_refresh_error?: string | null;
    feed: string;
    fill_policy: string;
    decision_interval_seconds: number;
  };
};

export type PaperMarketComparison = {
  id: string;
  status: string;
  started_at: string | null;
  expires_at: string | null;
  settings: Record<string, unknown>;
  readiness: unknown;
  news: unknown;
  portfolios: {
    id: string;
    name: string;
    strategy: string;
    snapshot: PaperMarketSnapshot;
    usage_cost_usd?: string | null;
  }[];
  benchmark: unknown;
  equity_history: unknown[];
  gaps: unknown[];
  usage_costs?: { included_in_returns?: boolean; usd?: string | null; status?: string } | null;
  remaining_seconds?: number | null;
};

export function decimalNumber(value: unknown): number | null {
  if (typeof value !== 'string' || !/^-?\d+(?:\.\d+)?$/.test(value)) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
const strings = (value: Record<string, unknown>, keys: string[]) =>
  keys.every((key) => typeof value[key] === 'string');
const optionalStrings = (value: Record<string, unknown>, keys: string[]) =>
  keys.every((key) => value[key] === null || typeof value[key] === 'string');
const decimals = (value: Record<string, unknown>, keys: string[], nullable = false) =>
  keys.every((key) => (nullable && value[key] === null) || decimalNumber(value[key]) !== null);
const items = (value: unknown, valid: (item: Record<string, unknown>) => boolean) =>
  Array.isArray(value) && value.every((item) => record(item) && valid(item));

/** Reject unavailable/error payloads instead of rendering invented zero balances. */
export function parsePaperMarketSnapshot(value: unknown): PaperMarketSnapshot | null {
  // The comparison service may expose the envelope directly instead of wrapping it in
  // the legacy single-account root. Use a real ledger snapshot as the compatibility base.
  if (
    record(value) &&
    value.schema_version === undefined &&
    (record(value.comparison) || (typeof value.id === 'string' && Array.isArray(value.portfolios)))
  ) {
    const c = (record(value.comparison) ? value.comparison : value) as Record<string, unknown>;
    if (!Array.isArray(c.portfolios) || c.portfolios.length === 0) return null;
    const first = c.portfolios[0];
    if (!record(first)) return null;
    const ledger = record(first.snapshot) ? first.snapshot : first;
    const base = parsePaperMarketSnapshot(ledger);
    if (!base) return null;
    return parsePaperMarketSnapshot({ ...base, comparison: c });
  }
  if (!record(value) || value.schema_version !== 1) return null;
  const { experiment: e, account: a, market: m, runtime: r } = value;
  if (!record(e) || !record(a) || !record(m) || !record(r)) return null;
  if (
    !strings(e, ['id', 'status', 'model', 'effort']) ||
    !optionalStrings(e, ['started_at', 'expires_at']) ||
    !decimals(e, ['initial_cash_usd']) ||
    !decimals(a, ['cash_usd', 'realized_pnl_usd']) ||
    !decimals(a, ['equity_usd', 'total_pnl_usd', 'total_return_pct', 'unrealized_pnl_usd'], true) ||
    typeof a.valuation_complete !== 'boolean' ||
    (a.invested_value_usd !== undefined &&
      decimalNumber(a.invested_value_usd) === null &&
      a.invested_value_usd !== null) ||
    (a.maximum_drawdown_pct !== undefined && decimalNumber(a.maximum_drawdown_pct) === null) ||
    (a.trade_count !== undefined &&
      (typeof a.trade_count !== 'number' ||
        !Number.isFinite(a.trade_count) ||
        a.trade_count < 0)) ||
    (a.valuation_fresh !== undefined && typeof a.valuation_fresh !== 'boolean') ||
    (a.marks_as_of !== undefined && a.marks_as_of !== null && typeof a.marks_as_of !== 'string') ||
    typeof m.is_open !== 'boolean' ||
    !strings(m, ['status']) ||
    !optionalStrings(m, ['next_open_at', 'session_close_at']) ||
    !optionalStrings(r, ['last_cycle_at', 'last_cycle_status', 'last_error', 'last_refresh_at']) ||
    (r.last_refresh_error !== undefined &&
      r.last_refresh_error !== null &&
      typeof r.last_refresh_error !== 'string') ||
    !strings(r, ['feed', 'fill_policy']) ||
    typeof r.decision_interval_seconds !== 'number' ||
    !Number.isFinite(r.decision_interval_seconds)
  )
    return null;
  if (
    !items(
      value.quotes,
      (q) =>
        strings(q, ['symbol', 'source', 'status']) &&
        decimals(q, ['price_usd'], true) &&
        optionalStrings(q, ['quoted_at']) &&
        (q.error === undefined || q.error === null || typeof q.error === 'string') &&
        (q.age_seconds === null ||
          (typeof q.age_seconds === 'number' && Number.isFinite(q.age_seconds)))
    ) ||
    !items(
      value.holdings,
      (h) =>
        strings(h, ['symbol']) &&
        decimals(h, ['quantity', 'average_cost_usd']) &&
        decimals(h, ['mark_price_usd', 'market_value_usd', 'unrealized_pnl_usd'], true) &&
        optionalStrings(h, ['quote_at'])
    ) ||
    !items(
      value.equity_history,
      (h) =>
        strings(h, ['at']) &&
        decimals(h, ['cash_usd']) &&
        decimals(h, ['equity_usd', 'total_pnl_usd'], true) &&
        typeof h.valuation_complete === 'boolean'
    ) ||
    !items(
      value.decisions,
      (d) =>
        strings(d, ['id', 'at', 'status', 'model', 'effort']) &&
        optionalStrings(d, ['rationale', 'error']) &&
        items(
          d.actions,
          (action) =>
            strings(action, ['symbol']) &&
            (action.side === 'buy' || action.side === 'sell') &&
            decimals(action, ['notional_usd', 'quantity'], true)
        )
    ) ||
    !items(
      value.fills,
      (f) =>
        strings(f, ['id', 'at', 'symbol', 'side']) &&
        decimals(f, ['quantity', 'price_usd', 'notional_usd', 'realized_pnl_usd'])
    )
  )
    return null;
  const replay = r.historical_replay;
  if (
    replay !== undefined &&
    (!record(replay) ||
      !strings(replay, ['status', 'start_date', 'end_date', 'expected_finish_at', 'scoring']) ||
      !decimals(replay, [
        'final_equity_usd',
        'pnl_usd',
        'return_pct',
        'buy_hold_return_pct',
        'max_drawdown_pct',
      ]) ||
      ![
        'sessions_total',
        'sessions_completed',
        'correct_decisions',
        'scored_decisions',
        'model_errors',
        'duration_hours',
      ].every(
        (key) => typeof replay[key] === 'number' && Number.isFinite(replay[key]) && replay[key] >= 0
      ) ||
      !(
        replay.decision_score_pct === null ||
        (typeof replay.decision_score_pct === 'number' &&
          Number.isFinite(replay.decision_score_pct) &&
          replay.decision_score_pct >= 0 &&
          replay.decision_score_pct <= 100)
      ) ||
      !items(
        replay.decisions,
        (d) =>
          strings(d, ['date', 'equity_usd']) &&
          (d.correct === null || typeof d.correct === 'boolean') &&
          (d.rationale === undefined || typeof d.rationale === 'string') &&
          (d.error === undefined || typeof d.error === 'string')
      ))
  )
    return null;
  const result = value as PaperMarketSnapshot;
  if (value.comparison !== undefined) {
    const c = value.comparison;
    if (
      !record(c) ||
      !strings(c, ['id', 'status']) ||
      !optionalStrings(c, ['started_at', 'expires_at']) ||
      !record(c.settings) ||
      !Array.isArray(c.portfolios) ||
      !Array.isArray(c.equity_history) ||
      !Array.isArray(c.gaps)
    )
      return null;
    const portfolios = c.portfolios.map((p) => {
      if (!record(p) || !strings(p, ['id', 'name', 'strategy'])) return null;
      // Accept either a nested snapshot or a ledger snapshot spread alongside identity metadata.
      const raw = record(p.snapshot) ? p.snapshot : p;
      const snapshot = parsePaperMarketSnapshot(raw);
      if (
        !snapshot ||
        (typeof p.usage_cost_usd !== 'undefined' &&
          p.usage_cost_usd !== null &&
          decimalNumber(p.usage_cost_usd) === null)
      )
        return null;
      return {
        id: p.id as string,
        name: p.name as string,
        strategy: p.strategy as string,
        snapshot,
        usage_cost_usd: p.usage_cost_usd as string | null | undefined,
      };
    });
    if (portfolios.some((p) => p === null)) return null;
    result.comparison = {
      id: c.id as string,
      status: c.status as string,
      started_at: c.started_at as string | null,
      expires_at: c.expires_at as string | null,
      settings: c.settings,
      readiness: c.readiness,
      news: c.news,
      portfolios: portfolios as NonNullable<(typeof portfolios)[number]>[],
      benchmark: c.benchmark,
      equity_history: c.equity_history,
      gaps: c.gaps,
      usage_costs: record(c.usage_costs)
        ? (c.usage_costs as PaperMarketComparison['usage_costs'])
        : null,
      remaining_seconds:
        typeof c.remaining_seconds === 'number' && Number.isFinite(c.remaining_seconds)
          ? c.remaining_seconds
          : null,
    };
  }
  return result;
}

export type ChartMetric = 'equity_usd' | 'total_pnl_usd';
export type EquityPoint = { x: number; y: number; at: string; value: number };

/** Only observed, complete valuations become points. Missing marks break the line. */
export function equityChart(history: PaperMarketSnapshot['equity_history'], metric: ChartMetric) {
  const samples = history
    .map((entry) => ({ entry, time: Date.parse(entry.at) }))
    .filter(({ time }) => Number.isFinite(time))
    .sort((a, b) => a.time - b.time);
  const values = samples.flatMap(({ entry }) => {
    const value = entry.valuation_complete ? decimalNumber(entry[metric]) : null;
    return value === null ? [] : [value];
  });
  if (!values.length)
    return { segments: [] as EquityPoint[][], count: 0, low: 0, high: 0, first: null, last: null };
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = max === min ? Math.max(Math.abs(max) * 0.002, 1) : (max - min) * 0.18;
  const low = min - pad;
  const high = max + pad;
  const start = samples[0].time;
  const end = samples[samples.length - 1].time;
  const segments: EquityPoint[][] = [];
  let segment: EquityPoint[] | null = null;
  for (const { entry, time } of samples) {
    const value = entry.valuation_complete ? decimalNumber(entry[metric]) : null;
    if (value === null) {
      segment = null;
      continue;
    }
    if (segment === null) {
      segment = [];
      segments.push(segment);
    }
    segment.push({
      x: end === start ? 360 : 12 + ((time - start) / (end - start)) * 696,
      y: 18 + ((high - value) / (high - low)) * 144,
      value,
      at: entry.at,
    });
  }
  return {
    segments,
    count: values.length,
    low,
    high,
    first: samples[0].entry.at,
    last: samples[samples.length - 1].entry.at,
  };
}

export function money(value: string | null | undefined, signed = false): string {
  const number = decimalNumber(value);
  if (number === null) return 'Unknown';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    ...(signed ? { signDisplay: 'exceptZero' as const } : {}),
  }).format(Object.is(number, -0) ? 0 : number);
}

export function percentage(value: string | null): string {
  const number = decimalNumber(value);
  return number === null ? 'Unknown return' : `${number > 0 ? '+' : ''}${number.toFixed(2)}%`;
}

export function ageLabel(at: string | null, now: number, fallback?: number | null): string {
  const timestamp = at === null ? NaN : Date.parse(at);
  const seconds = now > 0 && Number.isFinite(timestamp) ? (now - timestamp) / 1000 : fallback;
  if (seconds === undefined || seconds === null || !Number.isFinite(seconds)) return 'Age unknown';
  if (seconds < -60) return 'Timestamp ahead';
  if (seconds < 60) return `${Math.max(0, Math.floor(seconds))}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

/** Blockers keep a portfolio from trading (e.g. copy trader without credentials).
 * Taken from the shared readiness evidence and the portfolio's own runtime; never invented.
 */
export function portfolioBlockers(
  comparison: PaperMarketComparison,
  portfolio: PaperMarketComparison['portfolios'][number]
): string[] {
  const out: string[] = [];
  const readiness = record(comparison.readiness) ? comparison.readiness : {};
  const checks = record(readiness.checks) ? readiness.checks : {};
  const key = portfolio.id === 'copy' || portfolio.id === 'bot' ? portfolio.id : null;
  const check = key && record(checks[key]) ? checks[key] : null;
  if (check && check.ready !== true && Array.isArray(check.blockers))
    out.push(...check.blockers.filter((b): b is string => typeof b === 'string'));
  const runtime = portfolio.snapshot.runtime as Record<string, unknown>;
  if (runtime.last_cycle_status === 'blocked' && Array.isArray(runtime.blockers))
    out.push(...runtime.blockers.filter((b): b is string => typeof b === 'string'));
  // A copy portfolio with no fills and a failed check is blocked even before launch.
  if (portfolio.strategy === 'verified_copy' && !out.length && check && check.ready !== true)
    out.push('integration verification pending');
  return [...new Set(out)];
}

/** Per-portfolio API cost; falls back to the shared total only when it is a real number. */
export function usageCost(
  comparison: PaperMarketComparison,
  portfolio: PaperMarketComparison['portfolios'][number]
): string | null {
  if (portfolio.usage_cost_usd !== undefined && portfolio.usage_cost_usd !== null)
    return portfolio.usage_cost_usd;
  return null;
}

/** ASCII meter: filled cells out of `width`, clamped. Display only. */
export function asciiBar(fraction: number, width = 10): string {
  const f = Number.isFinite(fraction) ? Math.min(1, Math.max(0, fraction)) : 0;
  const n = Math.round(f * width);
  return '█'.repeat(n) + '░'.repeat(width - n);
}

export function trendGlyph(value: string | null | undefined): string {
  const n = decimalNumber(value);
  return n === null || n === 0 ? '■' : n > 0 ? '▲' : '▼';
}
