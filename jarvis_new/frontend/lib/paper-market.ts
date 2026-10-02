/** Wire contract for the read-only, seven-day paper market experiment.
 * Money stays decimal text on the wire. Numbers here are display-only.
 */
export type PaperMarketSnapshot = {
  schema_version: 1;
  generated_at?: string;
  experiment: {
    id: string;
    started_at: string;
    expires_at: string;
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
  if (!record(value) || value.schema_version !== 1) return null;
  const { experiment: e, account: a, market: m, runtime: r } = value;
  if (!record(e) || !record(a) || !record(m) || !record(r)) return null;
  if (
    !strings(e, ['id', 'started_at', 'expires_at', 'status', 'model', 'effort']) ||
    !decimals(e, ['initial_cash_usd']) ||
    !decimals(a, ['cash_usd', 'realized_pnl_usd']) ||
    !decimals(a, ['equity_usd', 'total_pnl_usd', 'total_return_pct', 'unrealized_pnl_usd'], true) ||
    typeof a.valuation_complete !== 'boolean' ||
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
  return value as PaperMarketSnapshot;
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
