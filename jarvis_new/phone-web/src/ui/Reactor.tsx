/** CORE reactor (PLAN.md §2.3): ASCII field canvas, 5 detachable 40° arc
 * segments at 324/36/108/180/252°, the mark, and the ⌗ nav satellite. All
 * are siblings (never nested) so each can morph on its own. */
import { useEffect, useRef } from 'preact/hooks';
import { SEGMENTS } from '../motion/page-morph';
import { Hash } from './glyphs';
import { Mark } from './Mark';
import { field } from './field';

export type Geo = { W: number; H: number; R: number };

export function geometry(): Geo {
  const W = Math.min(window.innerWidth, 480);
  const H = window.innerHeight;
  return { W, H, R: Math.min(0.62 * W, 260) };
}

function Segment({ angle, geo }: { angle: number; geo: Geo }) {
  const rs = geo.R / 2 - 6;
  const w = 2 * rs * Math.sin((20 * Math.PI) / 180);
  const h = 14;
  const sag = rs * (1 - Math.cos((20 * Math.PI) / 180));
  const a = (angle * Math.PI) / 180;
  const cx = geo.W / 2 + rs * Math.sin(a);
  const cy = geo.H * 0.4 - rs * Math.cos(a);
  const base = h - 3;
  return (
    <div
      class="m seg"
      data-morph={`core.seg.${angle}`}
      data-rot={angle}
      aria-hidden="true"
      style={{ left: `${cx - w / 2}px`, top: `${cy - h / 2}px`, width: `${w}px`, height: `${h}px` }}
    >
      <svg viewBox={`0 0 ${w} ${h}`}>
        <path d={`M3 ${base} Q${w / 2} ${base - 2 * sag} ${w - 3} ${base}`} />
      </svg>
    </div>
  );
}

export function Reactor({
  geo,
  live,
  onTap,
  onLong,
  onNav,
  muted,
}: {
  geo: Geo;
  live: boolean;
  muted: boolean;
  onTap: () => void;
  onLong: () => void;
  onNav: () => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const press = useRef<{ timer: number; long: boolean } | null>(null);
  useEffect(() => field.attach(canvas.current!, 4, 7), []);
  useEffect(() => field.resize(), [geo.W, geo.H]);

  const down = () => {
    const p = { timer: 0, long: false };
    p.timer = window.setTimeout(() => {
      p.long = true;
      onLong();
    }, 450);
    press.current = p;
  };
  const up = () => {
    const p = press.current;
    press.current = null;
    if (!p) return;
    clearTimeout(p.timer);
    if (!p.long) onTap();
  };
  const cancel = () => {
    if (press.current) clearTimeout(press.current.timer);
    press.current = null;
  };

  return (
    <>
      <div class="m ring" data-morph="core.ring" aria-hidden="true">
        <canvas ref={canvas} data-field="core" />
      </div>
      {SEGMENTS.map((s) => (
        <Segment key={s.angle} angle={s.angle} geo={geo} />
      ))}
      <Mark morph="core.mark" cls="core-mark" />
      <button
        class="reactor-tap"
        aria-label={live ? 'End call' : 'Start call'}
        onPointerDown={down}
        onPointerUp={up}
        onPointerLeave={cancel}
        onPointerCancel={cancel}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onTap();
          }
        }}
        onContextMenu={(e) => e.preventDefault()}
      />
      <button class="sr" onClick={onLong} disabled={!live}>
        {muted ? 'Unmute microphone' : 'Mute microphone'}
      </button>
      <button class="m nav" data-morph="core.nav" aria-label="Open deck" onClick={onNav}>
        <Hash />
      </button>
    </>
  );
}
