'use client';

import { memo, useEffect, useLayoutEffect, useRef } from 'react';
import type { JarvisState } from '@/hooks/hud/use-jarvis-state';
import { useAscii } from '@/lib/character/hooks';
import type { VoiceLink } from '@/lib/voice-link';
import { ClassicReactorEnergy } from './classic';
import { arc, pt } from './geometry';
import intro from './intro.module.css';
import { type Metrics, fmtBytes, metricWarnings, reactorWarnings } from './metrics';
import { OrbReactor } from './orb-reactor';
import { ReactorWarningLight } from './reactor-plasma';
import { ReactorProgress } from './reactor-progress';
import s from './stark.module.css';
import { WALL_FRAME_EVENT, refreshWallFrame, wallEmblemOnScreen } from './wall-emblem';

const MUTED = '#6b7280';

const useIsoLayoutEffect = typeof window !== 'undefined' ? useLayoutEffect : useEffect;

/** The ASCII sphere / scale ring radius in reactor viewBox units (880 x 600). */
const ORB_RADIUS_UNITS = 276;
/** How far the registration may move / grow the reactor (keeps a small or
 *  windowed overlay sane: the wallpaper emblem only lines up when the window
 *  covers the screen). */
const ALIGN_MAX_SCALE = 1.15;
const ALIGN_MIN_SCALE = 0.8;

/** Register the live orb over the wallpaper's static emblem: translate + scale
 *  the reactor box so its outer ring has the emblem's centre and radius
 *  (wall-emblem.ts). Measured un-transformed, re-run on resize. */
function useWallRegistration(ref: React.RefObject<HTMLDivElement | null>): void {
  useIsoLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    let raf = 0;
    const apply = () => {
      el.style.setProperty('--wx', '0px');
      el.style.setProperty('--wy', '0px');
      el.style.setProperty('--wk', '1');
      const r = el.getBoundingClientRect();
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      if (r.width < 40 || r.height < 40 || vh < 200) return;
      const unit = r.width / 880;
      const target = wallEmblemOnScreen(vw, vh);
      const k = Math.max(
        ALIGN_MIN_SCALE,
        Math.min(ALIGN_MAX_SCALE, target.r / (ORB_RADIUS_UNITS * unit))
      );
      const dx = Math.max(-0.1 * vw, Math.min(0.1 * vw, target.cx - (r.left + r.width / 2)));
      const dy = Math.max(-0.12 * vh, Math.min(0.16 * vh, target.cy - (r.top + r.height / 2)));
      el.style.setProperty('--wx', `${dx.toFixed(1)}px`);
      el.style.setProperty('--wy', `${dy.toFixed(1)}px`);
      el.style.setProperty('--wk', k.toFixed(4));
      // dev self-check: window.__jarvisAlign.check() re-measures the live orb
      // against the wallpaper emblem (residuals should be ~0 px).
      const w = window as unknown as { __jarvisAlign?: Record<string, unknown> };
      w.__jarvisAlign = {
        ...w.__jarvisAlign,
        dx,
        dy,
        k,
        target,
        check: () => {
          const b = el.getBoundingClientRect();
          const live = {
            cx: b.left + b.width / 2,
            cy: b.top + b.height / 2,
            r: ORB_RADIUS_UNITS * (b.width / 880),
          };
          return {
            dx: live.cx - target.cx,
            dy: live.cy - target.cy,
            dr: live.r - target.r,
            live,
            target,
          };
        },
      };
    };
    const schedule = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(apply);
    };
    apply();
    refreshWallFrame();
    const onResize = () => {
      schedule();
      refreshWallFrame();
    };
    window.addEventListener('resize', onResize);
    window.addEventListener(WALL_FRAME_EVENT, schedule);
    const ro = new ResizeObserver(schedule);
    if (el.parentElement) ro.observe(el.parentElement);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
      window.removeEventListener(WALL_FRAME_EVENT, schedule);
      ro.disconnect();
    };
  }, [ref]);
}

const TICKS = Array.from({ length: 120 }, (_, i) => i);
const DEGREE_LABELS = Array.from({ length: 12 }, (_, i) => i * 30);

/** Static scale ring: 120 ticks + bearings. Never re-renders. */
const ScaleRing = memo(function ScaleRing() {
  return (
    <g className={s.rxScale} data-ix-layer="6">
      <circle r="276" className={s.rxHair} pathLength={1} data-ix-draw />
      <circle r="252" className={s.rxHairFaint} pathLength={1} data-ix-draw />
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
  // Text columns sit well inside the reactor box: the orb is registered onto the
  // wallpaper emblem (shifted/scaled), so the old +-430 text edge ran under the
  // side panels and clipped the unit ("12 GB" -> "12").
  const ex = c.side === 'left' ? -272 : 272;
  const tx = c.side === 'left' ? -372 : 372;
  const anchor = c.side === 'left' ? 'start' : 'end';
  const fill = c.from + ((c.to - c.from) * Math.max(0, Math.min(100, c.pct))) / 100;
  const labelY = c.up ? ky - 28 : ky + 14;
  const valueY = c.up ? ky - 6 : ky + 38;
  return (
    <g className={s.rxCallout} data-warn={c.warn ? 'true' : undefined} data-ix-layer="7">
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
  const warning = reactorWarnings(m, muted, link.phase);
  const ascii = useAscii();
  const ref = useRef<HTMLDivElement>(null);
  useWallRegistration(ref);
  return (
    <div ref={ref} className={s.reactor} data-hud-reactor="true">
      <div className={intro.orbBody}>
        <i className={intro.orbGlow} aria-hidden="true" />
        <svg className={s.rxStatic} viewBox="-440 -300 880 600" aria-hidden="true">
          <ScaleRing />
          {callouts(m).map((c) => (
            <DataCallout key={c.key} c={c} />
          ))}
        </svg>
        <div className={s.rxEnergy} data-reactor-energy="true" data-ix-layer="0" aria-hidden="true">
          <OrbReactor
            className={s.rxLayer}
            color={muted ? MUTED : color}
            jarvis={jarvis}
            muted={muted}
            activation={link.activation}
            warn={warning.lit ? Math.min(1, 0.55 + 0.15 * (warning.active.length - 1)) : 0}
            fallback={
              ascii ? undefined : (
                <ClassicReactorEnergy color={color} jarvis={jarvis} muted={muted} />
              )
            }
          />
        </div>
        <ReactorProgress />
        <ReactorWarningLight warning={warning} />
      </div>
      <i className={intro.orbWave} aria-hidden="true" />
      <i className={intro.orbWave} aria-hidden="true" />
    </div>
  );
}
