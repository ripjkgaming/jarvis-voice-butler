import type { BridgeSys } from '@/lib/bridge';

/** Everything the STARK HUD reads from one bridge /sys snapshot.
 *  Missing values stay null: the HUD shows '—', never an invented number. */
export type Metrics = {
  has: boolean;
  load: number;
  cores: number;
  cpuPct: number;
  memUsed: number;
  memTotal: number;
  memPct: number;
  temp: number | null;
  tempPct: number;
  batt: number | null;
  ac: boolean;
  watts: number | null;
  phone: { battery: number; charging: boolean; age_s: number } | null;
  tailnet: boolean | null;
  net: BridgeSys['net'] | null;
  volume: BridgeSys['volume'] | null;
  callLive: boolean;
  diskFree: number | null;
};

/** Core-temp gauge spans 30–100 °C. */
const TEMP_MIN_C = 30;
const TEMP_MAX_C = 100;

const clamp = (v: number) => Math.max(0, Math.min(100, v));

/** Pure: /sys snapshot -> numbers the instruments draw. */
export function metricsFrom(sys: BridgeSys | null): Metrics {
  const load = Number(sys?.load_1_5_15?.[0] ?? 0) || 0;
  const cores = Math.max(1, Number(sys?.cpu_count ?? 0) || 1);
  const memTotal = sys?.mem_bytes?.MemTotal ?? 0;
  const memAvail = sys?.mem_bytes?.MemAvailable ?? 0;
  const memUsed = Math.max(0, memTotal - memAvail);
  const temp = typeof sys?.cpu_temp_c === 'number' ? sys.cpu_temp_c : null;
  const lp = sys?.laptop_power ?? null;
  return {
    has: sys !== null,
    load,
    cores,
    cpuPct: clamp((load / cores) * 100),
    memUsed,
    memTotal,
    memPct: memTotal ? clamp((memUsed / memTotal) * 100) : 0,
    temp,
    tempPct: temp === null ? 0 : clamp(((temp - TEMP_MIN_C) / (TEMP_MAX_C - TEMP_MIN_C)) * 100),
    batt: typeof lp?.battery === 'number' ? lp.battery : null,
    ac: lp?.ac === true,
    watts: typeof lp?.watts === 'number' && lp.watts > 0 ? lp.watts : null,
    phone: sys?.phone ?? null,
    tailnet: sys?.phone_tailnet ? sys.phone_tailnet.online === true : null,
    net: sys?.net ?? null,
    volume: sys?.volume ?? null,
    callLive: sys?.call_live === true,
    diskFree: typeof sys?.home_free_bytes === 'number' ? sys.home_free_bytes : null,
  };
}

export function fmtBytes(n: number | null | undefined): string {
  if (!n) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 10 ? 0 : 1)} ${u[i]}`;
}

/** Rolling samples for the sparklines: one per /sys change, last 60. */
export type Sample = { cpu: number; mem: number; temp: number | null };
export const HISTORY_LEN = 60;

export function pushSample(history: Sample[], m: Metrics): Sample[] {
  if (!m.has) return history;
  const next = [...history, { cpu: m.cpuPct, mem: m.memPct, temp: m.temp }];
  return next.length > HISTORY_LEN ? next.slice(next.length - HISTORY_LEN) : next;
}

/** Ticker-free health line for the footer. */
export function healthLine(m: Metrics): { text: string; warn: boolean } {
  if (!m.has) return { text: 'SYNCING TELEMETRY', warn: false };
  const issues: string[] = [];
  if (m.temp !== null && m.temp >= 85) issues.push(`CORE ${Math.round(m.temp)}°C`);
  if (m.batt !== null && m.batt < 20 && !m.ac) issues.push(`ARC REACTOR ${Math.round(m.batt)}%`);
  if (m.memPct >= 92) issues.push('MEMORY PRESSURE');
  if (issues.length === 0) return { text: 'ALL SYSTEMS NOMINAL', warn: false };
  return { text: issues.join(' · '), warn: true };
}
