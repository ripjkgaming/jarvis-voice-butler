'use client';

import { memo } from 'react';
import { VoiceLinkReticle } from '@/components/hud/voice-link';
import type { JarvisState } from '@/hooks/hud/use-jarvis-state';
import type { VoiceLink } from '@/lib/voice-link';
import { type Metrics, fmtBytes, metricWarnings, reactorWarnings } from './metrics';
import { ReactorWarningLight, useReactorPlasma } from './reactor-plasma';
import s from './stark.module.css';

/** Coordinates rounded to 0.01: the static export and the webview disagree
 *  in the last float digit, which breaks hydration. */
const r2 = (v: number) => Math.round(v * 100) / 100;

function pt(r: number, deg: number): [number, number] {
  const a = (deg * Math.PI) / 180;
  return [r2(r * Math.cos(a)), r2(r * Math.sin(a))];
}

/** SVG arc from a0 to a1 degrees (clockwise, 0 = east). */
function arc(r: number, a0: number, a1: number): string {
  const [x0, y0] = pt(r, a0);
  const [x1, y1] = pt(r, a1);
  const large = Math.abs(a1 - a0) > 180 ? 1 : 0;
  return `M${x0} ${y0} A${r} ${r} 0 ${large} 1 ${x1} ${y1}`;
}

const TICKS = Array.from({ length: 120 }, (_, i) => i);
const DEGREE_LABELS = Array.from({ length: 12 }, (_, i) => i * 30);
const COILS = Array.from({ length: 10 }, (_, i) => i * 36 - 90);
/** Copper windings across each coil block, degrees from the coil start. */
const WINDINGS = [5, 8.5, 12, 15.5, 19, 22.5];
const CORE_TICKS = Array.from({ length: 90 }, (_, i) => i * 4);
const INNER_TICKS = Array.from({ length: 36 }, (_, i) => i * 10);
const HEART_ARCS = Array.from({ length: 6 }, (_, i) => i * 60);

/** Static scale ring: 120 ticks + bearings. Never re-renders. */
const ScaleRing = memo(function ScaleRing() {
  return (
    <g className={s.rxScale}>
      <circle r="276" className={s.rxHair} />
      <circle r="252" className={s.rxHairFaint} />
      {TICKS.map((i) => {
        const deg = i * 3;
        const major = i % 10 === 0;
        const mid = i % 5 === 0;
        const [x0, y0] = pt(major ? 256 : mid ? 262 : 266, deg);
        const [x1, y1] = pt(274, deg);
        return (
          <line
            key={i}
            x1={x0}
            y1={y0}
            x2={x1}
            y2={y1}
            className={major ? s.rxTickMajor : mid ? s.rxTickMid : s.rxTick}
          />
        );
      })}
      {DEGREE_LABELS.map((deg) => {
        // Bearings read like a compass: 000 at north, clockwise.
        const [x, y] = pt(289, deg - 90);
        return (
          <text key={deg} x={x} y={y} className={s.rxBearing} dominantBaseline="middle">
            {String(deg).padStart(3, '0')}
          </text>
        );
      })}
      {/* horizon leaders */}
      <path d="M-282 0H-332M282 0H332M-332 -4V4M332 -4V4" className={s.rxHair} />
    </g>
  );
});

type Callout = {
  key: string;
  label: string;
  value: string;
  sub: string;
  pct: number;
  /** Arc span on the data ring, degrees (clockwise from east). */
  from: number;
  to: number;
  side: 'left' | 'right';
  up: boolean;
  warn: boolean;
};

const DATA_R = 244;

function DataCallout({ c }: { c: Callout }) {
  const mid = (c.from + c.to) / 2;
  const [mx, my] = pt(DATA_R, mid);
  const [kx, ky] = pt(DATA_R + 56, mid);
  const ex = c.side === 'left' ? -312 : 312;
  const tx = c.side === 'left' ? -430 : 430;
  const anchor = c.side === 'left' ? 'start' : 'end';
  const fill = c.from + ((c.to - c.from) * Math.max(0, Math.min(100, c.pct))) / 100;
  const labelY = c.up ? ky - 28 : ky + 14;
  const valueY = c.up ? ky - 6 : ky + 38;
  return (
    <g className={s.rxCallout} data-warn={c.warn ? 'true' : undefined}>
      <path d={arc(DATA_R, c.from, c.to)} className={s.rxDataTrack} />
      {c.pct > 0.5 ? <path d={arc(DATA_R, c.from, fill)} className={s.rxDataFill} /> : null}
      <path d={`M${mx} ${my} L${kx} ${ky} H${ex}`} className={s.rxLeader} />
      <circle cx={mx} cy={my} r="2.6" className={s.rxNode} />
      <circle cx={ex} cy={ky} r="2" className={s.rxNode} />
      <text x={tx} y={labelY} textAnchor={anchor} className={s.rxCalloutLabel}>
        {c.label}
      </text>
      <text x={tx} y={valueY} textAnchor={anchor} className={s.rxCalloutValue}>
        {c.value}
      </text>
      <text x={tx} y={valueY + 17} textAnchor={anchor} className={s.rxCalloutSub}>
        {c.sub}
      </text>
    </g>
  );
}

function callouts(m: Metrics): Callout[] {
  const dash = '—';
  const batt = m.batt;
  const warning = metricWarnings(m);
  return [
    {
      key: 'cpu',
      label: 'CPU LOAD',
      value: m.has ? `${Math.round(m.cpuPct)}%` : dash,
      sub: m.has ? `${m.load.toFixed(2)} / ${m.cores} CORES` : '',
      pct: m.cpuPct,
      from: 196,
      to: 254,
      side: 'left',
      up: true,
      warn: warning.cpu,
    },
    {
      key: 'mem',
      label: 'MEMORY',
      value: m.has ? fmtBytes(m.memUsed) : dash,
      sub: m.has ? `OF ${fmtBytes(m.memTotal)}` : '',
      pct: m.memPct,
      from: 286,
      to: 344,
      side: 'right',
      up: true,
      warn: warning.mem,
    },
    {
      key: 'temp',
      label: 'CORE TEMP',
      value: m.temp === null ? dash : `${Math.round(m.temp)}°C`,
      sub: m.temp === null ? '' : m.temp >= 85 ? 'HOT' : 'NOMINAL',
      pct: m.tempPct,
      from: 16,
      to: 74,
      side: 'right',
      up: false,
      warn: warning.temp,
    },
    {
      key: 'power',
      label: 'ARC REACTOR',
      value: batt === null ? dash : `${Math.round(batt)}%`,
      sub: m.watts !== null ? `${Math.round(m.watts)} W` : m.ac ? 'AC' : '',
      pct: batt ?? 0,
      from: 106,
      to: 164,
      side: 'left',
      up: false,
      warn: warning.power,
    },
  ];
}

/** The core: a Mark I style arc reactor. Ten coil blocks around a hot
 *  centre, tinted by Jarvis's voice state. */
const Core = memo(function Core() {
  return (
    <g className={s.rxCore}>
      <defs>
        <radialGradient id="stark-core-glow">
          <stop offset="0%" stopColor="#ffffff" stopOpacity="0.95" />
          <stop offset="28%" style={{ stopColor: 'var(--state)' }} stopOpacity="0.85" />
          <stop offset="62%" style={{ stopColor: 'var(--state)' }} stopOpacity="0.18" />
          <stop offset="100%" style={{ stopColor: 'var(--state)' }} stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle r="150" className={s.rxCoreHalo} fill="url(#stark-core-glow)" />
      {CORE_TICKS.map((a) => {
        const long = a % 20 === 0;
        const [x0, y0] = pt(long ? 139 : 142, a);
        const [x1, y1] = pt(146, a);
        return <line key={a} x1={x0} y1={y0} x2={x1} y2={y1} className={s.rxCoreFine} />;
      })}
      <circle r="134" className={s.rxCoreDash} />
      <circle r="128" className={s.rxCoreRing} />
      <circle r="123" className={s.rxCoreFine} />
      {COILS.map((a) => {
        const p1 = pt(90, a + 3);
        const p2 = pt(119, a + 1);
        const p3 = pt(119, a + 27);
        const p4 = pt(90, a + 25);
        const c0 = pt(95, a + 14);
        const c1 = pt(114, a + 14);
        const bolt = pt(124, a + 31.5);
        const lug = pt(106, a + 31.5);
        return (
          <g key={a}>
            <path
              d={`M${p1[0]} ${p1[1]} L${p2[0]} ${p2[1]} A119 119 0 0 1 ${p3[0]} ${p3[1]} L${p4[0]} ${p4[1]} A90 90 0 0 0 ${p1[0]} ${p1[1]} Z`}
              className={s.rxCoil}
            />
            {WINDINGS.map((d) => {
              const [wx0, wy0] = pt(93, a + d);
              const [wx1, wy1] = pt(116, a + d - 0.6);
              return (
                <line key={d} x1={wx0} y1={wy0} x2={wx1} y2={wy1} className={s.rxCoilWinding} />
              );
            })}
            <path d={arc(104.5, a + 4, a + 26)} className={s.rxCoilWinding} />
            <line x1={c0[0]} y1={c0[1]} x2={c1[0]} y2={c1[1]} className={s.rxCoilWire} />
            <circle cx={bolt[0]} cy={bolt[1]} r="2.6" className={s.rxBolt} />
            <circle cx={lug[0]} cy={lug[1]} r="1.4" className={s.rxBoltHot} />
          </g>
        );
      })}
      <circle r="84" className={s.rxCoreInner} />
      <circle r="76" className={s.rxCoreDash} />
      {INNER_TICKS.map((a) => {
        const [x0, y0] = pt(60, a);
        const [x1, y1] = pt(a % 30 === 0 ? 68 : 64, a);
        return <line key={a} x1={x0} y1={y0} x2={x1} y2={y1} className={s.rxCoreFine} />;
      })}
      <circle r="58" className={s.rxCoreFine} />
      {HEART_ARCS.map((a) => (
        <path key={a} d={arc(46, a + 6, a + 54)} className={s.rxHeartArc} />
      ))}
      <circle r="40" className={s.rxCoreFine} />
      <circle r="34" className={s.rxCoreHot} />
      <circle r="27" className={s.rxCoreDash} />
      <circle r="19" className={s.rxCoreHeart} />
    </g>
  );
});

/**
 * Centre instrument: a static scale with live data callouts (CPU, memory,
 * core temp, battery), three rotating rings on their own layers (WebKit
 * repaints a whole <svg> for any animation inside it, so each moving
 * ring is its own element), and the arc-reactor core wrapped in baked
 * particle layers (reactor-plasma.tsx).
 */
export function Reactor({
  m,
  link,
  color,
  jarvis,
  muted,
}: {
  m: Metrics;
  link: VoiceLink;
  color: string;
  jarvis: JarvisState;
  muted: boolean;
}) {
  const plasma = useReactorPlasma(color, jarvis, muted);
  const warning = reactorWarnings(m, muted, link.phase);
  return (
    <div className={s.reactor} data-energizing={link.joining && !muted ? 'true' : undefined}>
      <VoiceLinkReticle link={link} />
      <svg className={s.rxStatic} viewBox="-440 -300 880 600" aria-hidden="true">
        <ScaleRing />
        {callouts(m).map((c) => (
          <DataCallout key={c.key} c={c} />
        ))}
      </svg>
      <div className={s.rxEnergy} data-reactor-energy="true" aria-hidden="true">
        {plasma.dust}
        <svg
          className={`${s.rxLayer} ${s.rxSpinSlow}`}
          viewBox="-300 -300 600 600"
          aria-hidden="true"
        >
          <circle r="222" className={s.rxSegments} />
          <circle r="214" className={s.rxHairFaint} />
          <path d={arc(230, -100, -80)} className={s.rxMarker} />
          <path d={arc(230, 80, 100)} className={s.rxMarker} />
        </svg>
        <svg
          className={`${s.rxLayer} ${s.rxSpinMedRev}`}
          viewBox="-300 -300 600 600"
          aria-hidden="true"
        >
          <circle r="198" className={s.rxDashed} />
          {[0, 90, 180, 270].map((a) => (
            <path key={a} d={arc(188, a - 12, a + 12)} className={s.rxBracketArc} />
          ))}
        </svg>
        {plasma.orbits}
        <svg
          className={`${s.rxLayer} ${s.rxSpinFast}`}
          viewBox="-300 -300 600 600"
          aria-hidden="true"
        >
          {[0, 120, 240].map((a) => (
            <path key={a} d={arc(170, a, a + 54)} className={s.rxStateArc} />
          ))}
          <circle r="162" className={s.rxHairFaint} />
        </svg>
        <svg
          className={`${s.rxLayer} ${s.rxCoreLayer}`}
          viewBox="-300 -300 600 600"
          aria-hidden="true"
        >
          <Core />
        </svg>
        {plasma.inner}
      </div>
      <ReactorWarningLight warning={warning} />
    </div>
  );
}
