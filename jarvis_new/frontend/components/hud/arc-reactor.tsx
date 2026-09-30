'use client';

/**
 * Arc-reactor core dressing (Iron Man 1–2): pure decorative SVG.
 * Concentric 1px wireframe rings, a 72-tick index ring, three segmented
 * arcs rotating at different speeds/directions, a 10-coil inner winding,
 * and a state-tinted core (var(--jarvis-state)) with a white hot spot.
 *
 * Presentation only: no hooks, no data, no props contract. Each rotating
 * ring is its OWN stacked <svg> layer spun by a CSS transform on the
 * element itself: WebKit composites that on the GPU, whereas rotating a
 * <g> inside one shared <svg> repainted the whole reactor (+ its
 * drop-shadow filter) every frame. Static linework lives in one base layer.
 */

const TICKS = Array.from({ length: 72 }, (_, i) => i);
const COILS = Array.from({ length: 10 }, (_, i) => i);
/** Tick coordinates rounded to 0.01: Node (prerender) and the webview
 *  disagree in the last float digit, which broke hydration. */
const px = (v: number) => Math.round(v * 100) / 100;

export function ArcReactor() {
  const vb = '0 0 400 400';
  return (
    <>
      <svg className="im-reactor__svg" viewBox={vb} aria-hidden="true" focusable="false">
        <defs>
          <radialGradient id="im-core-halo" cx="50%" cy="50%" r="50%">
            <stop
              offset="0%"
              style={{ stopColor: 'var(--jarvis-state, #5fe3ff)' }}
              stopOpacity="0.55"
            />
            <stop
              offset="55%"
              style={{ stopColor: 'var(--jarvis-state, #5fe3ff)' }}
              stopOpacity="0.16"
            />
            <stop offset="100%" stopColor="#000000" stopOpacity="0" />
          </radialGradient>
        </defs>
        {/* Outer + inner hairline bezels */}
        <circle cx="200" cy="200" r="193" className="im-ring im-ring--hair" />
        <circle cx="200" cy="200" r="186" className="im-ring im-ring--faint" />
        <circle cx="200" cy="200" r="108" className="im-ring im-ring--hair" />
        <circle cx="200" cy="200" r="88" className="im-ring im-ring--faint" />
        {/* State-reactive core: halo + iris + white hot spot */}
        <circle cx="200" cy="200" r="78" fill="url(#im-core-halo)" className="im-core__halo" />
        <circle cx="200" cy="200" r="46" className="im-core__iris" />
        <circle cx="200" cy="200" r="17" className="im-core__hot" />
        <circle cx="200" cy="200" r="6" className="im-core__spark" />
      </svg>

      {/* 72-tick index ring — slow forward */}
      <svg className="im-reactor__svg im-spin im-spin--slow" viewBox={vb} aria-hidden="true">
        {TICKS.map((i) => {
          const a = (i * 5 * Math.PI) / 180;
          const major = i % 6 === 0;
          const r1 = major ? 166 : 173;
          const r2 = 181;
          return (
            <line
              key={i}
              x1={px(200 + r1 * Math.cos(a))}
              y1={px(200 + r1 * Math.sin(a))}
              x2={px(200 + r2 * Math.cos(a))}
              y2={px(200 + r2 * Math.sin(a))}
              className={major ? 'im-tick im-tick--major' : 'im-tick'}
            />
          );
        })}
      </svg>

      {/* Segmented power arcs — medium forward / fast reverse / coil */}
      <svg className="im-reactor__svg im-spin im-spin--med" viewBox={vb} aria-hidden="true">
        <circle cx="200" cy="200" r="150" className="im-ring im-ring--seg" />
        <circle cx="200" cy="200" r="46" className="im-core__iris-dash" />
      </svg>
      <svg className="im-reactor__svg im-spin im-spin--fast-rev" viewBox={vb} aria-hidden="true">
        <circle cx="200" cy="200" r="138" className="im-ring im-ring--seg2" />
      </svg>
      <svg className="im-reactor__svg im-spin im-spin--med-rev" viewBox={vb} aria-hidden="true">
        <circle cx="200" cy="200" r="122" className="im-ring im-ring--coil" />
      </svg>

      {/* 10-coil winding — slow reverse */}
      <svg className="im-reactor__svg im-spin im-spin--slow-rev" viewBox={vb} aria-hidden="true">
        {COILS.map((i) => {
          const a = (i * 36 * Math.PI) / 180;
          const r1 = 92;
          const r2 = 104;
          return (
            <line
              key={i}
              x1={px(200 + r1 * Math.cos(a))}
              y1={px(200 + r1 * Math.sin(a))}
              x2={px(200 + r2 * Math.cos(a))}
              y2={px(200 + r2 * Math.sin(a))}
              className="im-tick im-tick--coil"
            />
          );
        })}
      </svg>
    </>
  );
}
