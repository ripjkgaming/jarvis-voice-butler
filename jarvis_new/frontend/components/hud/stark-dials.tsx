'use client';

import { useEffect, useState } from 'react';
import { bridgeSys } from '@/lib/bridge';

const CYAN = '#5fe3ff';
const DIM = 'rgba(95, 227, 255, 0.28)';

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

/** Circular Stark gauge: segmented shell, progress arc, tick ring, center readout. */
function Dial({
  label,
  pct,
  value,
  sub,
}: {
  label: string;
  pct: number;
  value: string;
  sub: string;
}) {
  const p = Math.max(0, Math.min(100, pct));
  const r = 44;
  const c = 2 * Math.PI * r;
  const ticks = Array.from({ length: 60 }, (_, i) => i);
  return (
    <div className="stark-dial" title={`${label} ${value} ${sub}`}>
      <svg viewBox="0 0 120 120" className="stark-dial__svg" aria-hidden="true">
        {/* segmented shell */}
        <circle
          cx="60"
          cy="60"
          r="56"
          fill="none"
          stroke={DIM}
          strokeWidth="1"
          strokeDasharray="14 4.6"
        />
        {/* progress arc */}
        <circle
          cx="60"
          cy="60"
          r={r}
          fill="none"
          stroke={CYAN}
          strokeWidth="2"
          strokeLinecap="round"
          strokeDasharray={`${(p / 100) * c} ${c}`}
          transform="rotate(-90 60 60)"
          style={{ filter: 'drop-shadow(0 0 4px rgba(95,227,255,0.9))' }}
        />
        {/* track */}
        <circle cx="60" cy="60" r={r} fill="none" stroke={DIM} strokeWidth="1" />
        {/* ticks */}
        {ticks.map((i) => {
          const a = (i * 6 * Math.PI) / 180;
          const major = i % 15 === 0;
          const r1 = major ? 33 : 35.5;
          const r2 = 38;
          return (
            <line
              key={i}
              x1={60 + r1 * Math.cos(a)}
              y1={60 + r1 * Math.sin(a)}
              x2={60 + r2 * Math.cos(a)}
              y2={60 + r2 * Math.sin(a)}
              stroke={major ? CYAN : DIM}
              strokeWidth={major ? 1.2 : 0.7}
            />
          );
        })}
      </svg>
      <div className="stark-dial__core">
        <span className="stark-dial__value">{value}</span>
        <span className="stark-dial__label">{label}</span>
        <span className="stark-dial__sub">{sub}</span>
      </div>
    </div>
  );
}

/** Header instrument cluster: DATE / CPU / RAM / TIME. Same 1s bridge poll. */
export function StarkDials() {
  const [sys, setSys] = useState<Awaited<ReturnType<typeof bridgeSys>>>(null);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    let alive = true;
    const tickSys = async () => {
      if (document.hidden) return;
      try {
        const j = await bridgeSys();
        if (alive && j) setSys(j);
      } catch {
        /* offline — keep last */
      }
    };
    tickSys();
    // Telemetry at 2s to match SysCore (clock below stays 1s for seconds).
    const sysTimer = setInterval(tickSys, 2000);
    const clockTimer = setInterval(() => {
      if (!document.hidden) setNow(new Date());
    }, 1000);
    return () => {
      alive = false;
      clearInterval(sysTimer);
      clearInterval(clockTimer);
    };
  }, []);

  const load = Number(sys?.load_1_5_15?.[0] ?? 0);
  const cores = Number(sys?.cpu_count ?? 8);
  const cpuPct = Math.min(100, (load / Math.max(1, cores)) * 100);
  const memTotal = sys?.mem_bytes?.MemTotal ?? 0;
  const memAvail = sys?.mem_bytes?.MemAvailable ?? 0;
  const memUsed = Math.max(0, memTotal - memAvail);
  const memPct = memTotal ? (memUsed / memTotal) * 100 : 0;

  const day = now.getDate();
  const month = now.toLocaleString('en-US', { month: 'short' }).toUpperCase();
  const year = now.getFullYear();
  const daysInMonth = new Date(year, now.getMonth() + 1, 0).getDate();
  const hh = String(now.getHours()).padStart(2, '0');
  const mm = String(now.getMinutes()).padStart(2, '0');
  const ss = String(now.getSeconds()).padStart(2, '0');

  return (
    <div className="stark-dials" role="status" aria-label="Stark instruments">
      <Dial
        label="DATE"
        pct={(day / daysInMonth) * 100}
        value={String(day)}
        sub={`${month} ${year}`}
      />
      <Dial label="CPU" pct={cpuPct} value={`${load.toFixed(1)}`} sub={`${cores} CORE`} />
      <Dial
        label="RAM"
        pct={memPct}
        value={`${fmtBytes(memUsed)}`}
        sub={`OF ${fmtBytes(memTotal)}`}
      />
      <Dial
        label="TIME"
        pct={((now.getHours() * 3600 + now.getMinutes() * 60 + now.getSeconds()) / 864) * 100}
        value={`${hh}:${mm}`}
        sub={`${ss} LOCAL`}
      />
    </div>
  );
}
