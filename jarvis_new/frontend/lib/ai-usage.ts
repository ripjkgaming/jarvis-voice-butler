'use client';

import { bridgeGet } from '@/lib/bridge';

export const USAGE_TIMEZONE = 'Asia/Singapore';
export const USAGE_REFRESH_MS = 60_000;
export const USAGE_RANGES = [
  { key: 'today', label: 'Daily' },
  { key: 'week', label: 'Weekly' },
  { key: 'month', label: 'Monthly' },
  { key: 'all', label: 'All time' },
] as const;
export type UsageRange = (typeof USAGE_RANGES)[number]['key'];
export type UsageMetric = 'total_tokens' | 'estimated_cost_usd';

export type UsageTotals = {
  total_tokens: number | null;
  estimated_cost_usd: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
  reported_token_subtotal: number | null;
  unattributed_tokens: number | null;
  missing_pricing?: boolean;
  unpriced_models?: string[];
  token_total_note?: string | null;
};
export type UsageBreakdown = UsageTotals & { name: string; app?: string; model?: string };
export type UsageDay = UsageTotals & { date: string };
export type UsageDashboard = {
  source_mode: 'ccusage';
  updated_at: string;
  timezone: string;
  range: { key: UsageRange; label: string; start: string | null; end: string | null };
  source_info: {
    name: string;
    version: string;
    pricing_mode: string;
    timezone: string;
    status: string;
    snapshot_at: string | null;
  };
  summary: UsageTotals;
  apps: UsageBreakdown[];
  models: UsageBreakdown[];
  timeline: UsageDay[];
  warnings: string[];
  accounting_note?: string;
  accounting_details?: string;
  pricing?: { note?: string };
};
export type UsageSnapshot = {
  range: UsageRange;
  data: UsageDashboard | null;
  phase: 'loading' | 'ready' | 'stale' | 'error';
  refreshing: boolean;
};

const numericFields = [
  'total_tokens',
  'estimated_cost_usd',
  'input_tokens',
  'output_tokens',
  'cache_read_tokens',
  'cache_write_tokens',
  'reported_token_subtotal',
  'unattributed_tokens',
] as const;
function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function totals(value: unknown): value is UsageTotals {
  return (
    record(value) &&
    numericFields.every(
      (key) =>
        value[key] === null || (typeof value[key] === 'number' && Number.isFinite(value[key]))
    ) &&
    (value.missing_pricing === undefined || typeof value.missing_pricing === 'boolean') &&
    (value.unpriced_models === undefined ||
      (Array.isArray(value.unpriced_models) &&
        value.unpriced_models.every((v) => typeof v === 'string')))
  );
}

/** Validate without coercing, rounding, renaming models, or rebuilding totals. */
export function isUsageDashboard(value: unknown, range: UsageRange): value is UsageDashboard {
  if (!record(value) || !record(value.source_info) || !record(value.range)) return false;
  const source = value.source_info;
  return (
    value.source_mode === 'ccusage' &&
    source.name === 'ccusage' &&
    typeof source.version === 'string' &&
    typeof source.pricing_mode === 'string' &&
    typeof source.status === 'string' &&
    (source.snapshot_at === null || typeof source.snapshot_at === 'string') &&
    source.timezone === USAGE_TIMEZONE &&
    value.timezone === USAGE_TIMEZONE &&
    typeof value.updated_at === 'string' &&
    value.range.key === range &&
    typeof value.range.label === 'string' &&
    (value.range.start === null || typeof value.range.start === 'string') &&
    (value.range.end === null || typeof value.range.end === 'string') &&
    (value.accounting_details === undefined || typeof value.accounting_details === 'string') &&
    (value.pricing === undefined ||
      (record(value.pricing) &&
        (value.pricing.note === undefined || typeof value.pricing.note === 'string'))) &&
    totals(value.summary) &&
    [value.apps, value.models].every(
      (rows) =>
        Array.isArray(rows) &&
        rows.every(
          (row) =>
            record(row) &&
            typeof row.name === 'string' &&
            (row.app === undefined || typeof row.app === 'string') &&
            totals(row)
        )
    ) &&
    Array.isArray(value.timeline) &&
    value.timeline.every(
      (row) =>
        record(row) &&
        typeof row.date === 'string' &&
        /^\d{4}-\d{2}-\d{2}$/.test(row.date) &&
        Number.isFinite(Date.parse(`${row.date}T00:00:00Z`)) &&
        new Date(`${row.date}T00:00:00Z`).toISOString().slice(0, 10) === row.date &&
        totals(row)
    ) &&
    Array.isArray(value.warnings) &&
    value.warnings.every((warning) => typeof warning === 'string')
  );
}

export function usagePath(range: UsageRange): string {
  return `/insights/usage?range=${range}&timezone=${encodeURIComponent(USAGE_TIMEZONE)}`;
}

/** One mounted panel owns one cancellable, visibility-aware polling loop. */
export function subscribeUsage(
  range: UsageRange,
  onChange: (value: UsageSnapshot) => void
): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let request: AbortController | null = null;
  let lastStarted = -Infinity;
  let state: UsageSnapshot = { range, data: null, phase: 'loading', refreshing: false };
  const visible = () => !document.hidden;
  const emit = (next: UsageSnapshot) => {
    state = next;
    if (!stopped) onChange(next);
  };
  const schedule = () => {
    clearTimeout(timer);
    timer = undefined;
    if (!stopped && visible() && !request) {
      timer = setTimeout(
        () => void refresh(),
        Math.max(0, lastStarted + USAGE_REFRESH_MS - Date.now())
      );
    }
  };
  const refresh = async () => {
    timer = undefined;
    if (stopped || !visible() || request) return;
    const controller = new AbortController();
    request = controller;
    lastStarted = Date.now();
    emit({ ...state, refreshing: true });
    try {
      const result = await bridgeGet<unknown>(usagePath(range), 15_000, {
        signal: controller.signal,
      });
      if (stopped || controller.signal.aborted) return;
      if (isUsageDashboard(result, range)) {
        emit({
          range,
          data: result,
          phase: result.source_info.status === 'ready' ? 'ready' : 'stale',
          refreshing: false,
        });
      } else {
        emit({ ...state, phase: state.data ? 'stale' : 'error', refreshing: false });
      }
    } catch {
      if (!stopped && !controller.signal.aborted) {
        emit({ ...state, phase: state.data ? 'stale' : 'error', refreshing: false });
      }
    } finally {
      request = null;
      schedule();
    }
  };
  const onVisibility = () => {
    if (!visible()) {
      clearTimeout(timer);
      timer = undefined;
      request?.abort();
      if (state.refreshing) emit({ ...state, refreshing: false });
    } else schedule();
  };
  document.addEventListener('visibilitychange', onVisibility);
  emit(state);
  schedule();
  return () => {
    stopped = true;
    clearTimeout(timer);
    request?.abort();
    document.removeEventListener('visibilitychange', onVisibility);
  };
}

export function exactNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? 'Unavailable' : String(value);
}
export function tokenCount(value: number | null | undefined, compact = false): string {
  if (value === null || value === undefined) return 'Unavailable';
  return new Intl.NumberFormat(
    'en-US',
    compact ? { notation: 'compact', maximumFractionDigits: 2 } : { maximumFractionDigits: 0 }
  ).format(value);
}
export function estimatedCost(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'Unavailable';
  if (value > 0 && value < 0.01) return '<$0.01';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(value);
}

/** Date-only source periods must never be shifted through the browser timezone. */
export function dayLabel(date: string): string {
  const [year, month, day] = date.split('-').map(Number);
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(Date.UTC(year, month - 1, day)));
}
export function snapshotLabel(timestamp: string | null, timezone = USAGE_TIMEZONE): string {
  if (!timestamp || !Number.isFinite(Date.parse(timestamp))) return 'Timestamp unavailable';
  return new Intl.DateTimeFormat('en-SG', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZone: timezone,
  }).format(new Date(timestamp));
}

export function chartDays(days: UsageDay[], metric: UsageMetric) {
  const sorted = [...days].sort((a, b) => a.date.localeCompare(b.date));
  const max = Math.max(0, ...sorted.map((day) => day[metric] ?? 0));
  const first = sorted.length ? Date.parse(`${sorted[0].date}T00:00:00Z`) : 0;
  const last = sorted.length ? Date.parse(`${sorted[sorted.length - 1].date}T00:00:00Z`) : 0;
  return {
    max,
    spanDays: Math.max(1, (last - first) / 86_400_000 + 1),
    points: sorted.map((day) => ({
      day,
      x: last === first ? 0.5 : (Date.parse(`${day.date}T00:00:00Z`) - first) / (last - first),
      ratio: day[metric] === null ? null : max > 0 ? day[metric] / max : 0,
    })),
  };
}
