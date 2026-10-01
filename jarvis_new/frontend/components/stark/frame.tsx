'use client';

import { type ReactNode, useEffect, useRef, useState } from 'react';
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

/** The panel outline in real pixels: a chamfered plate, a hairline edge,
 *  bright brackets on the square corners and a hot accent on the cuts.
 *  Static SVG (repaints only on resize), drawn behind the content. */
function Outline({ w, h }: { w: number; h: number }) {
  const x0 = 0.5;
  const y0 = 0.5;
  const x1 = w - 0.5;
  const y1 = h - 0.5;
  const plate = `M${x0 + CUT} ${y0} H${x1} V${y1 - CUT} L${x1 - CUT} ${y1} H${x0} V${y0 + CUT} Z`;
  return (
    <svg className={s.outline} width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <path d={plate} className={s.outlinePlate} />
      {/* brackets on the square corners (top-right, bottom-left) */}
      <path
        className={s.outlineBracket}
        d={`M${x1 - ARM} ${y0} H${x1} V${y0 + ARM} M${x0} ${y1 - ARM} V${y1} H${x0 + ARM}`}
      />
      {/* hot accents on the chamfers */}
      <path
        className={s.outlineCut}
        d={`M${x0} ${y0 + CUT + 6} V${y0 + CUT} L${x0 + CUT} ${y0} H${x0 + CUT + 6} M${x1} ${
          y1 - CUT - 6
        } V${y1 - CUT} L${x1 - CUT} ${y1} H${x1 - CUT - 6}`}
      />
    </svg>
  );
}

/** STARK OS instrument panel: outline, `◢ LABEL ───── 01` header, body. */
export function Frame({
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
  aside?: ReactNode;
  className?: string;
  bodyClassName?: string;
  live?: boolean;
  children: ReactNode;
}) {
  const { ref, size } = useBoxSize<HTMLElement>();
  return (
    <section
      ref={ref}
      className={`${s.frame} ${className ?? ''}`}
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
