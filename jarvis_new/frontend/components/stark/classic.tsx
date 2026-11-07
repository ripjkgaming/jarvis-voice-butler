'use client';

/** Classic (non-ASCII) HUD pieces: the original LOCKE/STARK instruments that
 *  Jarvis and Friday wear. Ultron keeps the glyph versions (components/ascii).
 *  Swap points: frame.tsx, reactor.tsx, instruments.tsx, ops.tsx, desk.tsx,
 *  voice.tsx, stark-hud.tsx. */
import { memo, useEffect, useRef, useState } from 'react';
import type { JarvisState } from '@/hooks/hud/use-jarvis-state';
import { arc, pt } from './geometry';
import { useReactorPlasma } from './reactor-plasma';
import s from './stark.module.css';

/** Chamfer cut (px) at the top-left and bottom-right corners. */
const CUT = 12;
/** Corner bracket arm length (px). */
const ARM = 14;

function useBoxSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const w = Math.round(el.clientWidth);
      const h = Math.round(el.clientHeight);
      setSize((cur) => (cur && cur.w === w && cur.h === h ? cur : { w, h }));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return { ref, size };
}

/** Chamfered plate, hairline edge, corner brackets and hot cut accents. */
function Outline({ w, h }: { w: number; h: number }) {
  const x0 = 0.5;
  const y0 = 0.5;
  const x1 = w - 0.5;
  const y1 = h - 0.5;
  const plate = `M${x0 + CUT} ${y0} H${x1} V${y1 - CUT} L${x1 - CUT} ${y1} H${x0} V${y0 + CUT} Z`;
  return (
    <svg className={s.outline} width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <path d={plate} className={s.outlinePlate} />
      <path
        className={s.outlineBracket}
        d={`M${x1 - ARM} ${y0} H${x1} V${y0 + ARM} M${x0} ${y1 - ARM} V${y1} H${x0 + ARM}`}
      />
      <path
        className={s.outlineCut}
        d={`M${x0} ${y0 + CUT + 6} V${y0 + CUT} L${x0 + CUT} ${y0} H${x0 + CUT + 6} M${x1} ${
          y1 - CUT - 6
        } V${y1 - CUT} L${x1 - CUT} ${y1} H${x1 - CUT - 6}`}
      />
    </svg>
  );
}

export function ClassicFrame({
  label,
  index,
  aside,
  className,
  bodyClassName,
  live,
  children,
}: {
  label: string;
  index: number;
  aside?: React.ReactNode;
  className?: string;
  bodyClassName?: string;
  live?: boolean;
  children: React.ReactNode;
}) {
  const { ref, size } = useBoxSize<HTMLElement>();
  return (
    <section
      ref={ref}
      className={`${s.frame} ${s.frameClassic} ${className ?? ''}`}
      aria-label={label}
      data-live={live ? 'true' : undefined}
    >
      {size ? <Outline w={size.w} h={size.h} /> : null}
      <header className={s.frameHead}>
        <span className={s.frameMark} aria-hidden="true" />
        <span className={s.frameLabel}>{label}</span>
        <span className={s.frameRule} aria-hidden="true" />
        {aside ? <span className={s.frameAside}>{aside}</span> : null}
        <span className={s.frameIndex}>{String(index).padStart(2, '0')}</span>
      </header>
      <div className={`${s.frameBody} ${bodyClassName ?? ''}`}>{children}</div>
    </section>
  );
}

export function ClassicEmblem() {
  return (
    <svg className={`${s.emblem} ${s.emblemClassic}`} viewBox="-20 -20 40 40" aria-hidden="true">
      <circle r="18" className={s.emblemRing} />
      <circle r="12.5" className={s.emblemRing} strokeDasharray="5 2.85" />
      <path d="M0 -7.5 6.5 3.75 -6.5 3.75Z" className={s.emblemCore} />
      <circle r="2.4" className={s.emblemHeart} />
    </svg>
  );
}

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

export function ClassicSpark({ values, warn }: { values: number[]; warn?: boolean }) {
  const pts = sparkPoints(values);
  return (
    <svg
      className={s.sparkClassic}
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

/** Segmented cell bar: 20 cells, lit up to pct. */
export function ClassicCells({ pct, warn }: { pct: number; warn?: boolean }) {
  const lit = Math.round((Math.max(0, Math.min(100, pct)) / 100) * 20);
  return (
    <span className={s.cellsClassic} data-warn={warn ? 'true' : undefined} aria-hidden="true">
      {Array.from({ length: 20 }, (_, i) => (
        <i key={i} data-on={i < lit ? 'true' : undefined} />
      ))}
    </span>
  );
}

/** Thin progress rail (execution and research rows). Null pct = indeterminate. */
export function ClassicRail({ pct, className }: { pct: number | null; className?: string }) {
  return (
    <span
      className={`${s.railClassic} ${className ?? ''}`}
      data-indeterminate={pct === null ? 'true' : undefined}
      aria-hidden="true"
    >
      <i style={pct !== null ? { width: `${pct}%` } : undefined} />
    </span>
  );
}

export function ClassicLamp() {
  return <span className={s.linkLamp} aria-hidden="true" />;
}

/** Equity line for the paper-market card. */
export function ClassicDeskSpark({
  segments,
  tone,
}: {
  segments: { x: number; y: number }[][];
  tone?: string;
}) {
  return (
    <svg
      className={s.deskSparkClassic}
      viewBox="0 0 720 180"
      preserveAspectRatio="none"
      aria-hidden="true"
      data-tone={tone}
    >
      {segments.map((seg, i) => (
        <polyline key={i} points={seg.map((p) => `${p.x},${p.y}`).join(' ')} />
      ))}
    </svg>
  );
}

const COILS = Array.from({ length: 10 }, (_, i) => i * 36 - 90);
/** Copper windings across each coil block, degrees from the coil start. */
const WINDINGS = [5, 8.5, 12, 15.5, 19, 22.5];
const CORE_TICKS = Array.from({ length: 90 }, (_, i) => i * 4);
const INNER_TICKS = Array.from({ length: 36 }, (_, i) => i * 10);
const HEART_ARCS = Array.from({ length: 6 }, (_, i) => i * 60);

/** The core: a Mark I style arc reactor. Ten coil blocks around a hot
 *  centre, tinted by the voice state. */
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
      <circle r="150" className={s.rxCoreHalo} fill="url(#stark-core-glow)" data-ix-layer="2" />
      <g data-ix-layer="3">
        {CORE_TICKS.map((a) => {
          const long = a % 20 === 0;
          const [x0, y0] = pt(long ? 139 : 142, a);
          const [x1, y1] = pt(146, a);
          return <line key={a} x1={x0} y1={y0} x2={x1} y2={y1} className={s.rxCoreFine} />;
        })}
        <circle r="134" className={s.rxCoreDash} />
        <circle r="128" className={s.rxCoreRing} pathLength={1} data-ix-draw />
        <circle r="123" className={s.rxCoreFine} />
      </g>
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
          <g key={a} data-ix-layer="3">
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
      <g data-ix-layer="1">
        <circle r="84" className={s.rxCoreInner} pathLength={1} data-ix-draw />
        <circle r="76" className={s.rxCoreDash} />
        {INNER_TICKS.map((a) => {
          const [x0, y0] = pt(60, a);
          const [x1, y1] = pt(a % 30 === 0 ? 68 : 64, a);
          return <line key={a} x1={x0} y1={y0} x2={x1} y2={y1} className={s.rxCoreFine} />;
        })}
        <circle r="58" className={s.rxCoreFine} pathLength={1} data-ix-draw />
      </g>
      <g data-ix-layer="0">
        {HEART_ARCS.map((a) => (
          <path key={a} d={arc(46, a + 6, a + 54)} className={s.rxHeartArc} />
        ))}
        <circle r="40" className={s.rxCoreFine} />
        <circle r="34" className={s.rxCoreHot} pathLength={1} data-ix-draw />
        <circle r="27" className={s.rxCoreDash} />
        <circle r="19" className={s.rxCoreHeart} />
      </g>
    </g>
  );
});

/** Rotating rings, baked particle layers and the arc-reactor core. */
export function ClassicReactorEnergy({
  color,
  jarvis,
  muted,
}: {
  color: string;
  jarvis: JarvisState;
  muted: boolean;
}) {
  const plasma = useReactorPlasma(color, jarvis, muted);
  return (
    <>
      {plasma.dust}
      <svg
        className={`${s.rxLayer} ${s.rxSpinSlow}`}
        data-ix-layer="5"
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
        data-ix-layer="4"
        data-ix-spin
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
        data-ix-layer="4"
        data-ix-spin
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
    </>
  );
}
