'use client';

import { useEffect, useRef, useState } from 'react';
import { useBridgeSysSnapshot } from '@/hooks/hud/use-bridge-sys';
import { MUTED_COLOR, useJarvisState, useMicMuted } from '@/hooks/hud/use-jarvis-state';

function fmtBytes(n: number): string {
  if (!n) return '0B';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 10 ? 0 : 1)}${u[i]}`;
}

/** Baked bar: the track texture is pure CSS (composited); only width% + text go live. */
function CoreBar({ label, pct, text }: { label: string; pct: number; text: string }) {
  const p = Math.max(0, Math.min(100, pct));
  return (
    <div
      className="hud-core__bar"
      data-warn={p >= 85 ? 'true' : 'false'}
      title={`${label} ${text}`}
    >
      <span className="hud-core__bar-label">{label}</span>
      <span className="hud-core__bar-track">
        <span className="hud-core__bar-fill" style={{ width: `${p}%` }} />
      </span>
      <span className="hud-core__bar-text">{text}</span>
    </div>
  );
}

const RING_R = 26;
const RING_C = 2 * Math.PI * RING_R;

/** Compact live stat gauge: thin SVG ring arc, tabular number in the
 *  centre, mono uppercase label (+ optional sub) beneath. Amber when bad. */
function StatGauge({
  label,
  pct,
  value,
  sub,
  warn,
}: {
  label: string;
  pct: number;
  value: string;
  sub?: string;
  warn: boolean;
}) {
  const p = Math.max(0, Math.min(100, pct));
  return (
    <div
      className="hud-core__gauge"
      data-warn={warn ? 'true' : 'false'}
      title={sub ? `${label} ${value} ${sub}` : `${label} ${value}`}
    >
      <span className="hud-core__ring">
        <svg viewBox="0 0 64 64" aria-hidden="true">
          <circle cx="32" cy="32" r={RING_R} className="hud-core__ring-track" />
          <circle
            cx="32"
            cy="32"
            r={RING_R}
            className="hud-core__ring-fg"
            strokeDasharray={`${(p / 100) * RING_C} ${RING_C}`}
            transform="rotate(-90 32 32)"
          />
        </svg>
        <span className="hud-core__gauge-value">{value}</span>
      </span>
      <span className="hud-core__gauge-label">{label}</span>
      {sub ? <span className="hud-core__gauge-sub">{sub}</span> : null}
    </div>
  );
}

type SuitMode = 'offline' | 'booting' | 'lost' | 'online' | 'charging' | 'full' | 'low';

const BOOT_MS = 1200;
const LOST_MS = 900;

/** Suit power lifecycle: boots when the phone links, powers down when it
 *  drops. Returns the display mode plus the (count-up) battery shown. */
function useSuitPower(phone: { battery: number; charging: boolean } | null) {
  const linked = phone !== null;
  const [phase, setPhase] = useState<'idle' | 'booting' | 'lost'>('idle');
  const [shown, setShown] = useState(0);
  const prevLinked = useRef(linked);

  useEffect(() => {
    const was = prevLinked.current;
    prevLinked.current = linked;
    if (linked && !was) {
      setPhase('booting');
      const t = setTimeout(() => setPhase('idle'), BOOT_MS);
      return () => clearTimeout(t);
    }
    if (!linked && was) {
      setPhase('lost');
      const t = setTimeout(() => setPhase('idle'), LOST_MS);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [linked]);

  // One-shot count-up during boot (rAF only for BOOT_MS, then idle).
  const target = phone?.battery ?? 0;
  useEffect(() => {
    if (phase !== 'booting') {
      setShown(target);
      return undefined;
    }
    const t0 = performance.now();
    let raf = 0;
    const step = (now: number) => {
      const k = Math.min(1, (now - t0) / BOOT_MS);
      setShown(Math.round(target * (1 - (1 - k) ** 3)));
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [phase, target]);

  let mode: SuitMode;
  if (phase === 'lost') mode = 'lost';
  else if (!phone) mode = 'offline';
  else if (phase === 'booting') mode = 'booting';
  else if (phone.charging && phone.battery >= 100) mode = 'full';
  else if (phone.charging) mode = 'charging';
  else if (phone.battery < 20) mode = 'low';
  else mode = 'online';
  return { mode, shown };
}

const SUIT_SUB: Record<SuitMode, string> = {
  offline: 'NO LINK',
  booting: 'SUIT ONLINE',
  lost: 'LINK LOST',
  online: 'PHONE',
  charging: 'CHARGING',
  full: 'FULL',
  low: 'LOW POWER',
};

/** SUIT POWER gauge. Motion lives on standalone HTML layers (transform /
 *  opacity only) so the compositor animates it without SVG repaints. */
function SuitGauge({
  phone,
  hasData,
}: {
  phone: { battery: number; charging: boolean } | null;
  hasData: boolean;
}) {
  const { mode, shown } = useSuitPower(phone);
  const pct = mode === 'offline' || mode === 'lost' ? 0 : shown;
  const value = !hasData || mode === 'offline' || mode === 'lost' ? '—' : `${shown}%`;
  const warn = hasData && (mode === 'offline' || mode === 'lost' || mode === 'low');
  return (
    <div
      className="hud-core__gauge hud-suit"
      data-mode={mode}
      data-warn={warn ? 'true' : 'false'}
      title={`SUIT POWER ${value} ${SUIT_SUB[mode]}`}
    >
      <span className="hud-core__ring">
        <svg viewBox="0 0 64 64" aria-hidden="true">
          <circle cx="32" cy="32" r={RING_R} className="hud-core__ring-track" />
          <circle
            cx="32"
            cy="32"
            r={RING_R}
            className="hud-core__ring-fg hud-suit__fg"
            strokeDasharray={`${(pct / 100) * RING_C} ${RING_C}`}
            transform="rotate(-90 32 32)"
          />
        </svg>
        {/* Energy flow: a short bright arc on its own layer, rotated. */}
        <svg className="hud-suit__flow" viewBox="0 0 64 64" aria-hidden="true">
          <circle
            cx="32"
            cy="32"
            r={RING_R}
            strokeDasharray={`${RING_C * 0.12} ${RING_C}`}
            transform="rotate(-90 32 32)"
          />
        </svg>
        <span className="hud-suit__flash" aria-hidden="true" />
        <span className="hud-core__gauge-value">{value}</span>
        <svg className="hud-suit__bolt" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M13 2 4 14h7l-1 8 9-12h-7z" />
        </svg>
      </span>
      <span className="hud-core__gauge-label">SUIT POWER</span>
      <span className="hud-core__gauge-sub">{hasData ? SUIT_SUB[mode] : '—'}</span>
    </div>
  );
}

/** Uplink ring drains over 10 min from the last phone sighting. */
const UPLINK_FULL_S = 600;
/** Core-temp ring maps 30–100 °C across the arc. */
const TEMP_MIN_C = 30;
const TEMP_MAX_C = 100;

/**
 * SYSTEM core panel: four live stat gauges (suit power, arc reactor,
 * uplink, core temp) over the CPU + RAM bars. Numbers come from the
 * shared bridge /sys snapshot; '—' until data arrives, never invented.
 */
export function SysCore() {
  const { color: stateColor } = useJarvisState();
  const { muted } = useMicMuted();
  const color = muted === true ? MUTED_COLOR : stateColor;
  const sys = useBridgeSysSnapshot();

  const load = Number(sys?.load_1_5_15?.[0] ?? 0);
  const cores = Number(sys?.cpu_count ?? 8);
  const cpuPct = Math.min(100, (load / Math.max(1, cores)) * 100);
  const memTotal = sys?.mem_bytes?.MemTotal ?? 0;
  const memAvail = sys?.mem_bytes?.MemAvailable ?? 0;
  const memUsed = Math.max(0, memTotal - memAvail);

  const hasData = sys !== null;
  const phone = sys?.phone ?? null;
  const laptop = sys?.laptop_power ?? null;
  const temp = typeof sys?.cpu_temp_c === 'number' ? (sys.cpu_temp_c as number) : null;

  const arcBatt = typeof laptop?.battery === 'number' ? (laptop.battery as number) : null;
  const arcWatts =
    typeof laptop?.watts === 'number' && (laptop.watts as number) > 0
      ? Math.round(laptop.watts as number)
      : null;
  const arcValue = !hasData || arcBatt === null ? '—' : `${Math.round(arcBatt)}%`;
  const arcSub = !hasData ? '—' : arcWatts !== null ? `${arcWatts} W` : laptop?.ac ? 'AC' : '—';
  const arcWarn = hasData && (arcBatt === null || (arcBatt < 20 && laptop?.ac !== true));

  const ageS = phone ? phone.age_s : null;
  const uplinkValue = !hasData
    ? '—'
    : ageS !== null
      ? `${Math.round(ageS)}S`
      : sys?.phone_tailnet?.online
        ? 'ON'
        : '—';
  const onTailnet = sys?.phone_tailnet?.online === true;
  const uplinkSub = !hasData ? '—' : phone ? 'AGO' : onTailnet ? 'TAILNET' : 'OFFLINE';
  const uplinkWarn = hasData && !phone && !onTailnet;
  const uplinkPct = phone ? Math.max(0, (1 - phone.age_s / UPLINK_FULL_S) * 100) : 0;

  const tempValue = !hasData || temp === null ? '—' : `${Math.round(temp)}°C`;
  const tempWarn = temp !== null && temp >= 85;
  const tempPct =
    temp === null
      ? 0
      : Math.max(0, Math.min(100, ((temp - TEMP_MIN_C) / (TEMP_MAX_C - TEMP_MIN_C)) * 100));

  return (
    <div
      className="hud-core"
      role="status"
      aria-label="System core monitor"
      style={{ ['--jarvis-state' as string]: color }}
    >
      <div className="hud-graph__head">
        <span>SYSTEM</span>
      </div>
      <div className="hud-core__gauges">
        <SuitGauge phone={phone} hasData={hasData} />
        <StatGauge
          label="ARC REACTOR"
          pct={arcBatt ?? 0}
          value={arcValue}
          sub={arcSub}
          warn={arcWarn}
        />
        <StatGauge
          label="UPLINK"
          pct={uplinkPct}
          value={uplinkValue}
          sub={uplinkSub}
          warn={uplinkWarn}
        />
        <StatGauge label="CORE TEMP" pct={tempPct} value={tempValue} warn={tempWarn} />
      </div>
      <div className="hud-core__bars">
        <CoreBar label="CPU" pct={cpuPct} text={`${load.toFixed(2)}`} />
        <CoreBar
          label="RAM"
          pct={memTotal ? (memUsed / memTotal) * 100 : 0}
          text={`${fmtBytes(memUsed)}/${fmtBytes(memTotal)}`}
        />
      </div>
    </div>
  );
}
