'use client';

import { useEffect, useState } from 'react';
import { bridgeSys } from '@/lib/bridge';

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

function Gauge({ label, pct, text }: { label: string; pct: number; text: string }) {
  const p = Math.max(0, Math.min(100, pct));
  return (
    <div className="hud-gauge" title={`${label} ${text}`}>
      <span className="hud-gauge__label">{label}</span>
      <span className="hud-gauge__bar">
        <span className="hud-gauge__fill" style={{ width: `${p}%` }} />
      </span>
      <span className="hud-gauge__text">{text}</span>
    </div>
  );
}

/** Silent 1s bridge poll. Pauses when tab hidden. Hidden in solo mode. */
export function SysGauges() {
  const [sys, setSys] = useState<Awaited<ReturnType<typeof bridgeSys>>>(null);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setInterval> | null = null;
    const tick = async () => {
      if (document.hidden) return;
      try {
        const j = await bridgeSys();
        if (alive && j) setSys(j);
      } catch {
        /* offline — keep last */
      }
    };
    tick();
    timer = setInterval(tick, 1000);
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, []);

  // Bridge /sys: load average (1/5/15), mem bytes, home disk free.
  const load = Number(sys?.load_1_5_15?.[0] ?? 0);
  const memTotal = sys?.mem_bytes?.MemTotal ?? 0;
  const memAvail = sys?.mem_bytes?.MemAvailable ?? 0;
  const memUsed = Math.max(0, memTotal - memAvail);
  const homeFree = sys?.home_free_bytes ?? 0;
  // Assume up to 8 cores for the load pct ballpark; pure cosmetic.
  const loadPct = Math.min(100, (load / 8) * 100);

  return (
    <div className="hud-gauges" role="status" aria-label="Resource monitor">
      <Gauge label="LOAD" pct={loadPct} text={`${load.toFixed(2)}`} />
      <Gauge
        label="RAM"
        pct={memTotal ? (memUsed / memTotal) * 100 : 0}
        text={`${fmtBytes(memUsed)}/${fmtBytes(memTotal)}`}
      />
      <Gauge label="HOME" pct={0} text={fmtBytes(homeFree)} />
    </div>
  );
}
