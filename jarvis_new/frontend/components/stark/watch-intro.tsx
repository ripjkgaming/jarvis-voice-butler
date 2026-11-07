'use client';

import { type CSSProperties, useEffect, useRef, useState } from 'react';
import { useRoomState } from '@/hooks/hud/use-room-state';
import s from './watch-intro.module.css';

const TICKS = Array.from({ length: 48 }, (_, i) => i);
const EYE_W = 340;
/** Where the card's ASCII eye sits (watch_ascii.compose eye_y), as screen fractions. */
const CARD_EYE = { x: 0.5, y: 0.38 };

type Run = {
  at: number;
  vars: CSSProperties;
  ms: number;
};

function Eye() {
  return (
    <svg className={s.svg} viewBox="-160 -100 320 200" aria-hidden="true">
      <path
        className={s.lidGlyphs}
        d="M-158 0Q0 -112 158 0Q0 112 -158 0Z"
        transform="scale(1.06)"
      />
      <path className={s.lid} d="M-150 0Q0 -98 150 0Q0 98 -150 0Z" />
      <g className={s.scan}>
        <g className={s.iris}>
          <circle className={s.ring} r="58" strokeWidth="2" />
          <circle className={s.ring} r="46" strokeWidth="1" strokeDasharray="3 5" opacity="0.7" />
          <g className={s.ticks}>
            {TICKS.map((i) => (
              <line
                key={i}
                className={s.ring}
                x1="0"
                y1={i % 4 === 0 ? -62 : -60}
                x2="0"
                y2="-66"
                strokeWidth={i % 4 === 0 ? 2 : 1}
                transform={`rotate(${i * 7.5})`}
              />
            ))}
          </g>
          <circle className={s.pupil} r="22" />
        </g>
      </g>
      <g className={s.orb}>
        <circle r="16" fill="currentColor" opacity="0.28" />
        <circle r="7" fill="currentColor" opacity="0.7" />
        <circle className={s.core} r="3" />
      </g>
    </svg>
  );
}

/** Voice-triggered Watch Mode intro: the reactor orb opens into an eye, the HUD
 *  blacks out, and the eye hands over to the ASCII eye on every monitor. */
export function WatchIntro() {
  const room = useRoomState();
  const intro = room?.watch_intro ?? null;
  const [run, setRun] = useState<Run | null>(null);
  const seen = useRef(0);

  useEffect(() => {
    if (!intro || intro.at <= seen.current) return;
    seen.current = intro.at;
    const elapsed = Math.max(0, Date.now() / 1000 - intro.at);
    const total = intro.blackout_at + intro.cover;
    if (elapsed > total) return;
    const reactor = document.querySelector('[data-hud-reactor]');
    const rect = reactor?.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const cx = rect && rect.width > 0 ? rect.left + rect.width / 2 : vw / 2;
    const cy = rect && rect.height > 0 ? rect.top + rect.height / 2 : vh / 2;
    const width = Math.min(EYE_W, vw * 0.4);
    const s2 = Math.max(1, Math.min(vw * 0.36, vh * 0.8) / width);
    const color = getComputedStyle(
      document.querySelector('.hud') ?? document.body
    ).getPropertyValue('--state');
    const vars = {
      '--cx': `${cx}px`,
      '--cy': `${cy}px`,
      '--w': `${width}px`,
      '--tx': `${vw * CARD_EYE.x - cx}px`,
      '--ty': `${vh * CARD_EYE.y - cy}px`,
      '--s2': s2,
      '--b': `${intro.blackout_at}s`,
      '--c': `${intro.cover}s`,
      '--d': `${-elapsed}s`,
      ...(color.trim() ? { '--eye': color.trim() } : {}),
    } as CSSProperties;
    setRun({ at: intro.at, vars, ms: (total + 0.4 - elapsed) * 1000 });
  }, [intro]);

  useEffect(() => {
    if (!run) return;
    const root = document.documentElement;
    root.setAttribute('data-watch-intro', 'true');
    const t = setTimeout(() => setRun(null), run.ms);
    return () => {
      clearTimeout(t);
      root.removeAttribute('data-watch-intro');
    };
  }, [run]);

  if (!run) return null;
  return (
    <div className={s.root} style={run.vars} key={run.at} aria-hidden="true">
      <div className={s.black} />
      <div className={s.eye}>
        <Eye />
      </div>
    </div>
  );
}
