'use client';

import { GlyphBar, GlyphChart, GlyphLamp } from '@/components/ascii/glyphs';
import { useAscii } from '@/lib/character/hooks';
import { ClassicCells, ClassicLamp, ClassicSpark } from './classic';
import { type Metrics, type Sample, fmtBytes, metricWarnings } from './metrics';
import s from './stark.module.css';

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
  const ascii = useAscii();
  return (
    <div className={s.trend} data-warn={warn ? 'true' : undefined}>
      <div className={s.trendHead}>
        <span className={s.trendLabel}>{label}</span>
        <span className={s.trendSub}>{sub}</span>
      </div>
      <div className={s.trendRow}>
        <span className={s.trendValue}>{value}</span>
        {ascii ? (
          <GlyphChart
            values={values}
            width={26}
            rows={2}
            lo={0}
            hi={100}
            tone={warn ? 'warn' : undefined}
            className={s.spark}
          />
        ) : (
          <ClassicSpark values={values} warn={warn} />
        )}
      </div>
    </div>
  );
}

/** CPU / memory / core-temp trends over the last ~2 minutes. */
export function Trends({ m, history }: { m: Metrics; history: Sample[] }) {
  const warning = metricWarnings(m);
  const dash = '—';
  const temps = history.map((h) => (h.temp === null ? 0 : ((h.temp - 30) / 70) * 100));
  return (
    <div className={s.trends}>
      <Trend
        label="CPU"
        value={m.has ? `${Math.round(m.cpuPct)}%` : dash}
        sub={m.has ? `LOAD ${m.load.toFixed(2)}` : ''}
        values={history.map((h) => h.cpu)}
        warn={warning.cpu}
      />
      <Trend
        label="MEMORY"
        value={m.has ? `${Math.round(m.memPct)}%` : dash}
        sub={m.has ? `${fmtBytes(m.memUsed)} / ${fmtBytes(m.memTotal)}` : ''}
        values={history.map((h) => h.mem)}
        warn={warning.mem}
      />
      <Trend
        label="CORE TEMP"
        value={m.temp === null ? dash : `${Math.round(m.temp)}°`}
        sub="30–100 °C"
        values={temps}
        warn={warning.temp}
      />
    </div>
  );
}

/** Glyph cell bar: 20 cells, lit up to pct. */
function Cells({ pct, warn }: { pct: number; warn?: boolean }) {
  const ascii = useAscii();
  if (!ascii) return <ClassicCells pct={pct} warn={warn} />;
  return <GlyphBar pct={pct} width={24} tone={warn ? 'warn' : undefined} className={s.cells} />;
}

/** ARC REACTOR (laptop battery) and SUIT POWER (phone battery). */
export function Power({ m }: { m: Metrics }) {
  const batt = m.batt;
  const warning = metricWarnings(m);
  const lowBatt = warning.power;
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
        data-warn={warning.phone ? 'true' : undefined}
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
  const warning = metricWarnings(m);
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
      state: !net ? 'off' : warning.network ? 'warn' : 'on',
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
      state: vol ? (warning.audio ? 'warn' : 'on') : 'off',
    },
    {
      label: 'STORAGE',
      value: m.diskFree === null ? '—' : `${fmtBytes(m.diskFree)} FREE`,
      state: m.diskFree === null ? 'off' : warning.storage ? 'warn' : 'on',
    },
  ];
}

/** Link status lamps: network, phone, voice call, audio, storage. */
export function Links({ m }: { m: Metrics }) {
  const ascii = useAscii();
  return (
    <ul className={s.links}>
      {links(m).map((l) => (
        <li key={l.label} className={s.link} data-state={l.state}>
          {ascii ? <GlyphLamp state={l.state} /> : <ClassicLamp />}
          <span className={s.linkLabel}>{l.label}</span>
          <span className={s.linkValue}>{l.value}</span>
        </li>
      ))}
    </ul>
  );
}
