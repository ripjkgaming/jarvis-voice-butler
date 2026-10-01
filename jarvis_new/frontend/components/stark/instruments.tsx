'use client';

import { type Metrics, type Sample, fmtBytes } from './metrics';
import s from './stark.module.css';

/** Polyline points for 0-100 values over a 0..100 x 0..30 box. Pure. */
function sparkPoints(values: number[]): string {
  if (values.length < 2) return '';
  const step = 100 / (values.length - 1);
  return values
    .map((v, i) => {
      const y = 29 - (Math.max(0, Math.min(100, v)) / 100) * 27;
      return `${(i * step).toFixed(2)},${y.toFixed(2)}`;
    })
    .join(' ');
}

function Spark({ values, warn }: { values: number[]; warn?: boolean }) {
  const pts = sparkPoints(values);
  return (
    <svg
      className={s.spark}
      data-warn={warn ? 'true' : undefined}
      viewBox="0 0 100 30"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <path d="M0 29.5H100M0 15H100M0 1H100" className={s.sparkGrid} />
      {pts ? (
        <>
          <polygon points={`0,30 ${pts} 100,30`} className={s.sparkArea} />
          <polyline points={pts} className={s.sparkLine} />
        </>
      ) : null}
    </svg>
  );
}

function Trend({
  label,
  value,
  sub,
  values,
  warn,
}: {
  label: string;
  value: string;
  sub: string;
  values: number[];
  warn?: boolean;
}) {
  return (
    <div className={s.trend} data-warn={warn ? 'true' : undefined}>
      <div className={s.trendHead}>
        <span className={s.trendLabel}>{label}</span>
        <span className={s.trendSub}>{sub}</span>
      </div>
      <div className={s.trendRow}>
        <span className={s.trendValue}>{value}</span>
        <Spark values={values} warn={warn} />
      </div>
    </div>
  );
}

/** CPU / memory / core-temp trends over the last ~2 minutes. */
export function Trends({ m, history }: { m: Metrics; history: Sample[] }) {
  const dash = '—';
  const temps = history.map((h) => (h.temp === null ? 0 : ((h.temp - 30) / 70) * 100));
  return (
    <div className={s.trends}>
      <Trend
        label="CPU"
        value={m.has ? `${Math.round(m.cpuPct)}%` : dash}
        sub={m.has ? `LOAD ${m.load.toFixed(2)}` : ''}
        values={history.map((h) => h.cpu)}
        warn={m.cpuPct >= 90}
      />
      <Trend
        label="MEMORY"
        value={m.has ? `${Math.round(m.memPct)}%` : dash}
        sub={m.has ? `${fmtBytes(m.memUsed)} / ${fmtBytes(m.memTotal)}` : ''}
        values={history.map((h) => h.mem)}
        warn={m.memPct >= 92}
      />
      <Trend
        label="CORE TEMP"
        value={m.temp === null ? dash : `${Math.round(m.temp)}°`}
        sub="30–100 °C"
        values={temps}
        warn={m.temp !== null && m.temp >= 85}
      />
    </div>
  );
}

/** Segmented cell bar: 20 cells, lit up to pct. */
function Cells({ pct, warn }: { pct: number; warn?: boolean }) {
  const lit = Math.round((Math.max(0, Math.min(100, pct)) / 100) * 20);
  return (
    <span className={s.cells} data-warn={warn ? 'true' : undefined} aria-hidden="true">
      {Array.from({ length: 20 }, (_, i) => (
        <i key={i} data-on={i < lit ? 'true' : undefined} />
      ))}
    </span>
  );
}

/** ARC REACTOR (laptop battery) and SUIT POWER (phone battery). */
export function Power({ m }: { m: Metrics }) {
  const batt = m.batt;
  const lowBatt = batt !== null && batt < 20 && !m.ac;
  const phone = m.phone;
  const suitSub = phone
    ? phone.charging
      ? phone.battery >= 100
        ? 'FULL'
        : 'CHARGING'
      : phone.battery < 20
        ? 'LOW POWER'
        : 'ON BATTERY'
    : m.tailnet
      ? 'APP IDLE'
      : 'NO LINK';
  return (
    <div className={s.power}>
      <div className={s.powerRow} data-warn={lowBatt ? 'true' : undefined}>
        <div className={s.powerHead}>
          <span className={s.powerLabel}>ARC REACTOR</span>
          <span className={s.powerValue}>{batt === null ? '—' : `${Math.round(batt)}%`}</span>
        </div>
        <Cells pct={batt ?? 0} warn={lowBatt} />
        <span className={s.powerSub}>
          {m.ac ? 'MAINS' : 'BATTERY'}
          {m.watts !== null ? ` · ${m.watts.toFixed(1)} W` : ''}
        </span>
      </div>
      <div
        className={s.powerRow}
        data-warn={phone && !phone.charging && phone.battery < 20 ? 'true' : undefined}
        data-off={phone ? undefined : 'true'}
      >
        <div className={s.powerHead}>
          <span className={s.powerLabel}>SUIT POWER</span>
          <span className={s.powerValue}>{phone ? `${Math.round(phone.battery)}%` : '—'}</span>
        </div>
        <Cells pct={phone?.battery ?? 0} />
        <span className={s.powerSub}>{suitSub}</span>
      </div>
    </div>
  );
}

type Link = { label: string; value: string; state: 'on' | 'warn' | 'off' };

function links(m: Metrics): Link[] {
  const net = m.net;
  const phone = m.phone;
  const vol = m.volume;
  return [
    {
      label: 'NETWORK',
      value: !net
        ? '—'
        : net.kind === 'none'
          ? 'OFFLINE'
          : `${net.kind === 'wifi' ? 'WI-FI' : 'WIRED'} ${net.name ?? ''}${
              typeof net.signal === 'number' ? ` · ${net.signal}%` : ''
            }`.trim(),
      state: !net ? 'off' : net.kind === 'none' ? 'warn' : 'on',
    },
    {
      label: 'PHONE',
      value: phone
        ? `LINKED · ${Math.round(phone.age_s)}S AGO`
        : m.tailnet
          ? 'ON TAILNET'
          : 'OFFLINE',
      state: phone ? 'on' : m.tailnet ? 'on' : 'off',
    },
    {
      label: 'VOICE',
      value: m.callLive ? 'CALL LIVE' : 'STANDBY',
      state: m.callLive ? 'on' : 'off',
    },
    {
      label: 'AUDIO',
      value: vol ? (vol.muted ? 'MUTED' : `${vol.pct}%`) : '—',
      state: vol ? (vol.muted ? 'warn' : 'on') : 'off',
    },
    {
      label: 'STORAGE',
      value: m.diskFree === null ? '—' : `${fmtBytes(m.diskFree)} FREE`,
      state: m.diskFree === null ? 'off' : m.diskFree < 10 * 1024 ** 3 ? 'warn' : 'on',
    },
  ];
}

/** Link status lamps: network, phone, voice call, audio, storage. */
export function Links({ m }: { m: Metrics }) {
  return (
    <ul className={s.links}>
      {links(m).map((l) => (
        <li key={l.label} className={s.link} data-state={l.state}>
          <span className={s.linkLamp} aria-hidden="true" />
          <span className={s.linkLabel}>{l.label}</span>
          <span className={s.linkValue}>{l.value}</span>
        </li>
      ))}
    </ul>
  );
}
