'use client';

import { useEffect, useRef, useState } from 'react';
import { NOISE, RAMP, glyphAtlas, hash, prefersReducedMotion } from '@/lib/ascii';
import { particleHex, particleSkin } from '@/lib/character/particle-skin';

/** One-shot glyph mosaic over a surface: ink cells full of scrambling glyphs
 * dissolve diagonally to reveal what is underneath, then the canvas is
 * removed. `delay` staggers panels; `reverse` covers instead (exit). */
export function AsciiWipe({
  delay = 0,
  duration = 620,
  color,
  reverse = false,
  onDone,
}: {
  delay?: number;
  duration?: number;
  /** Defaults to the active skin's accent. */
  color?: string;
  reverse?: boolean;
  onDone?: () => void;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx || prefersReducedMotion()) {
      setDone(true);
      onDone?.();
      return;
    }
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(1, rect.width);
    const h = Math.max(1, rect.height);
    const dpr = Math.min(1.5, window.devicePixelRatio || 1);
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const cw = 8;
    const ch = 14;
    const cols = Math.ceil(w / cw);
    const rows = Math.ceil(h / ch);
    const atlas = glyphAtlas(cw, ch, dpr, color ?? particleHex());
    const ink = `${particleSkin.ink[0] | 0},${particleSkin.ink[1] | 0},${particleSkin.ink[2] | 0}`;
    const t0 = performance.now() + delay;
    let raf = 0;
    const step = (now: number) => {
      const raw = (now - t0) / duration;
      const p = reverse ? 1 - Math.max(0, raw) : Math.max(0, raw);
      ctx.clearRect(0, 0, w, h);
      const tick = Math.floor(now / 55);
      for (let y = 0; y < rows; y++) {
        for (let x = 0; x < cols; x++) {
          const n = hash(x, y);
          const order = ((x / cols) * 0.55 + (y / rows) * 0.45) * 0.78 + n * 0.22;
          const lp = Math.min(1, Math.max(0, (p * 1.35 - order) / 0.35));
          if (lp >= 1) continue;
          ctx.fillStyle = `rgba(${ink},${(0.94 * (1 - lp)).toFixed(3)})`;
          ctx.fillRect(x * cw, y * ch, cw, ch);
          if (n > 0.62 && lp === 0) continue;
          const g = RAMP.length + Math.floor(hash(x + tick, y - tick) * NOISE.length);
          atlas.draw(ctx, g, lp > 0 ? 1 - lp * 0.6 : 0.35 + n * 0.3, x * cw, y * ch);
        }
      }
      if (raw < 1) raf = requestAnimationFrame(step);
      else {
        setDone(true);
        onDone?.();
      }
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (done && !reverse) return null;
  return (
    <canvas
      ref={ref}
      aria-hidden="true"
      data-ascii-wipe="true"
      style={{
        position: 'absolute',
        inset: 0,
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
        zIndex: 30,
      }}
    />
  );
}
